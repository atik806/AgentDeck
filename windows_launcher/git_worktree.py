"""A thin, Qt-free wrapper around ``git worktree`` + diff/merge plumbing.

The Worktrees feature gives each terminal pane its own ``git worktree`` on a
throwaway branch, so several agents can work the same repository in parallel
without stepping on each other or on the user's real checkout. This module is
the only place that shells out to ``git``; everything above it
(:mod:`worktree_store`, :mod:`worktree_panel`, ``terminal_panel``) works with
the dataclasses defined here.

Qt-free on purpose (same rule as :mod:`agents` / :mod:`entitlements`): plain
functions over plain data, so it can be unit-tested headless against a real
temporary repository.

Design notes
------------
* Every ``git`` call goes through :func:`_run`, which passes
  ``creationflags=CREATE_NO_WINDOW`` so a GUI build never flashes a console, and
  sets ``GIT_TERMINAL_PROMPT=0`` so a missing credential helper fails fast
  instead of hanging on a prompt.
* ``merge_to_base`` (v2) never runs ``git checkout`` in the user's working
  tree. It merges inside an *ephemeral* detached worktree and then advances the
  base ref with a compare-and-swap ``update-ref``. The user's checkout simply
  shows "behind" afterwards -- clean, expected, and documented in the UI.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "GitError",
    "NotAGitRepo",
    "WorktreeConflict",
    "DirtyWorktree",
    "RepoInfo",
    "WorktreeStatus",
    "FileDelta",
    "MergeResult",
    "git_available",
    "is_git_repo",
    "detect_repo",
    "list_worktrees",
    "add_worktree",
    "remove_worktree",
    "delete_branch",
    "prune_worktrees",
    "status",
    "diff_stat",
    "diff_text",
    "is_dirty",
    "commit_all",
    "merge_to_base",
    "rebase_onto_base",
    "push_branch",
    "base_moved",
]

#: ``subprocess`` flag: don't pop a console window for the child (Windows only).
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

#: Cap for :func:`diff_text` -- a diff bigger than this is truncated with a
#: marker rather than shovelled into a QPlainTextEdit.
DEFAULT_DIFF_MAX_BYTES = 2_000_000

_DEFAULT_TIMEOUT = 30.0
#: Branch names we consider "the mainline" when the repo doesn't tell us.
_BASE_CANDIDATES = ("main", "master", "trunk", "develop", "development")


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #

class GitError(RuntimeError):
    """A ``git`` command failed, or the environment is missing ``git``."""


class NotAGitRepo(GitError):
    """A path that was expected to be inside a git repository is not."""


class WorktreeConflict(GitError):
    """A merge/rebase stopped on conflicts. ``conflicts`` lists the paths."""

    def __init__(self, message: str, conflicts: "list[str] | None" = None):
        super().__init__(message)
        self.conflicts: list[str] = list(conflicts or [])


class DirtyWorktree(GitError):
    """An operation needs a clean worktree and it isn't. ``files`` lists why."""

    def __init__(self, message: str, files: "list[str] | None" = None):
        super().__init__(message)
        self.files: list[str] = list(files or [])


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RepoInfo:
    """Everything callers need about the repository behind a folder."""

    toplevel: str          #: the main working tree's root
    git_common_dir: str    #: the shared ``.git`` directory (absolute)
    current_branch: str    #: "" when the main checkout is detached
    default_base: str      #: best guess at the mainline branch (may be "")
    remote_url: str        #: ``origin`` URL, "" if there is no origin
    remote_slug: str       #: "owner/name" when ``origin`` is GitHub, else ""


@dataclass(frozen=True)
class WorktreeStatus:
    """A point-in-time snapshot of one worktree relative to its base branch."""

    path: str
    branch: str
    head_sha: str
    base_branch: str
    base_sha: str
    ahead: int
    behind: int
    dirty: bool
    dirty_files: int
    exists: bool           #: the directory is present on disk
    registered: bool       #: ``git worktree list`` knows about it


@dataclass(frozen=True)
class FileDelta:
    """One changed path in a worktree-vs-base diff."""

    status: str            #: A / M / D / R / C / T / U
    path: str
    old_path: str = ""     #: source path for a rename/copy
    added: int = 0
    removed: int = 0

    @property
    def display_path(self) -> str:
        if self.old_path and self.old_path != self.path:
            return f"{self.old_path} → {self.path}"
        return self.path


@dataclass(frozen=True)
class MergeResult:
    ok: bool
    fast_forward: bool
    merge_sha: str
    base_sha_before: str
    conflicts: list = field(default_factory=list)
    message: str = ""


# --------------------------------------------------------------------------- #
# Process plumbing
# --------------------------------------------------------------------------- #

def _run(
    args: "list[str]",
    cwd: "str | os.PathLike | None",
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run ``git <args>`` in ``cwd`` and return the completed process.

    Raises :class:`GitError` when ``git`` is missing, times out, or (with
    ``check``) exits non-zero. ``stdout``/``stderr`` are text.
    """
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        # Deterministic output regardless of the user's git config / locale.
        "GIT_PAGER": "cat",
        "LC_ALL": "C",
    }
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:  # git not on PATH
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {timeout:g}s") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {detail}")
    return proc


def _out(args: "list[str]", cwd, **kw) -> str:
    return _run(args, cwd, **kw).stdout.strip()


def _canon(path: "str | os.PathLike") -> str:
    """Case-folded real path -- collapses 8.3 short names, symlinks, ``..``.

    Used for every worktree-path comparison: git reports canonical long paths
    while the app builds paths from ``%LOCALAPPDATA%`` (which may arrive short),
    so a plain ``normpath`` comparison is not reliable on Windows.
    """
    try:
        return os.path.normcase(os.path.realpath(str(path)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.normpath(str(path)))


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def git_available() -> bool:
    """True when a ``git`` executable is on PATH."""
    return shutil.which("git") is not None


def is_git_repo(path: "str | os.PathLike | None") -> bool:
    """True when ``path`` is inside a git working tree (cheap, never raises)."""
    if not path or not git_available():
        return False
    try:
        p = Path(path)
    except (TypeError, ValueError):
        return False
    if not p.exists():
        return False
    try:
        out = _run(
            ["rev-parse", "--is-inside-work-tree"], p, check=False, timeout=10.0
        )
    except GitError:
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


_GH_SLUG_RE = re.compile(
    r"github\.com[/:]+(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$", re.IGNORECASE
)


def _github_slug(remote_url: str) -> str:
    m = _GH_SLUG_RE.search(remote_url or "")
    return f"{m.group('owner')}/{m.group('name')}" if m else ""


def _guess_default_base(toplevel: str, current_branch: str) -> str:
    """Best effort at the mainline branch name.

    Prefers ``origin/HEAD``'s target, then a local ``main``/``master``/…, then
    the branch the main checkout is on. May legitimately be "" (brand-new repo
    with an unborn branch, or a detached checkout with no mainline).
    """
    try:
        head_ref = _out(
            ["symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD"],
            toplevel,
            check=False,
        )
    except GitError:
        head_ref = ""
    if head_ref.startswith("origin/"):
        name = head_ref[len("origin/"):]
        if _ref_exists(toplevel, f"refs/heads/{name}") or _ref_exists(
            toplevel, f"refs/remotes/origin/{name}"
        ):
            return name
    for cand in _BASE_CANDIDATES:
        if _ref_exists(toplevel, f"refs/heads/{cand}"):
            return cand
    for cand in _BASE_CANDIDATES:
        if _ref_exists(toplevel, f"refs/remotes/origin/{cand}"):
            return cand
    return current_branch or ""


def _ref_exists(cwd, ref: str) -> bool:
    try:
        return _run(
            ["rev-parse", "--verify", "-q", ref], cwd, check=False, timeout=10.0
        ).returncode == 0
    except GitError:
        return False


def detect_repo(path: "str | os.PathLike | None") -> RepoInfo:
    """Resolve the repository containing ``path``.

    Raises :class:`NotAGitRepo` when ``path`` is missing, not a directory, or
    not inside a work tree.
    """
    if not path:
        raise NotAGitRepo("no folder given")
    p = Path(path)
    if not p.exists():
        raise NotAGitRepo(f"{p} does not exist")
    if not git_available():
        raise GitError("git is not installed or not on PATH")
    if not is_git_repo(p):
        raise NotAGitRepo(f"{p} is not a git repository")

    toplevel = _out(["rev-parse", "--show-toplevel"], p)
    common = _out(["rev-parse", "--path-format=absolute", "--git-common-dir"], p)
    # Older git without --path-format: fall back to a plain (possibly relative) read.
    if not common or common.startswith("--"):
        common = _out(["rev-parse", "--git-common-dir"], toplevel)
        common = str((Path(toplevel) / common).resolve()) if not os.path.isabs(common) else common

    branch = _out(["rev-parse", "--abbrev-ref", "HEAD"], toplevel, check=False)
    if branch == "HEAD":  # detached
        branch = ""

    remote_url = _out(["remote", "get-url", "origin"], toplevel, check=False)
    if remote_url.startswith("fatal") or remote_url.startswith("error"):
        remote_url = ""

    return RepoInfo(
        toplevel=os.path.normpath(toplevel),
        git_common_dir=os.path.normpath(common),
        current_branch=branch,
        default_base=_guess_default_base(toplevel, branch),
        remote_url=remote_url,
        remote_slug=_github_slug(remote_url),
    )


# --------------------------------------------------------------------------- #
# Worktree list / add / remove
# --------------------------------------------------------------------------- #

def list_worktrees(repo: "RepoInfo | str | os.PathLike") -> "list[dict]":
    """Parse ``git worktree list --porcelain`` into dicts.

    Each dict has ``path`` (absolute, normalised), ``head`` (sha or ""),
    ``branch`` (short name or ""), ``bare`` / ``detached`` / ``locked`` /
    ``prunable`` booleans. The first entry is always the main working tree.
    """
    top = repo.toplevel if isinstance(repo, RepoInfo) else str(repo)
    raw = _out(["worktree", "list", "--porcelain"], top)
    entries: list[dict] = []
    cur: dict = {}
    for line in raw.splitlines():
        if not line.strip():
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            if cur:
                entries.append(cur)
            cur = {
                "path": os.path.normpath(line[len("worktree "):]),
                "head": "",
                "branch": "",
                "bare": False,
                "detached": False,
                "locked": False,
                "prunable": False,
            }
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line.strip() == "bare":
            cur["bare"] = True
        elif line.strip() == "detached":
            cur["detached"] = True
        elif line.startswith("locked"):
            cur["locked"] = True
        elif line.startswith("prunable"):
            cur["prunable"] = True
    if cur:
        entries.append(cur)
    return entries


def _wt_registered(top: str, worktree_path: str) -> bool:
    target = _canon(worktree_path)
    return any(_canon(e["path"]) == target for e in list_worktrees(top))


def add_worktree(
    repo: "RepoInfo | str",
    *,
    worktree_path: "str | os.PathLike",
    branch: str,
    base: str,
    create_branch: bool = True,
) -> WorktreeStatus:
    """Create a worktree at ``worktree_path`` on ``branch`` forked from ``base``.

    Retries once with a fresh ``-N`` suffix if the branch name collides. Wraps
    "No space left" / permission errors as :class:`GitError`. On success returns
    a fresh :func:`status`.
    """
    top = repo.toplevel if isinstance(repo, RepoInfo) else str(repo)
    dest = Path(worktree_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    base_arg = [base] if base else []
    # Each retry gets a fresh branch name *and* a fresh destination directory --
    # "already exists" can mean either the branch or the path, and reusing the
    # same dir just fails the same way.
    attempts = 3 if create_branch else 1

    last_err: "GitError | None" = None
    for i in range(attempts):
        name = branch if i == 0 else f"{branch}-{i + 1}"
        dst = dest if i == 0 else dest.with_name(f"{dest.name}-{i + 1}")
        args = ["worktree", "add"]
        if create_branch:
            args += ["-b", name]
        args += [str(dst), *base_arg]
        proc = _run(args, top, check=False, timeout=120.0)
        if proc.returncode == 0:
            return status(str(dst), base or name)
        detail = (proc.stderr or proc.stdout or "").strip()
        last_err = GitError(f"git worktree add failed: {detail}")
        low = detail.lower()
        if "no space left" in low:
            raise GitError("not enough disk space to create the worktree") from last_err
        if create_branch and ("already exists" in low or "already used by worktree" in low):
            continue  # retry with a fresh branch + dir
        break
    raise last_err or GitError("git worktree add failed")


def remove_worktree(
    repo: "RepoInfo | str", worktree_path: "str | os.PathLike", *, force: bool = False
) -> None:
    """Unregister and delete a worktree directory.

    Tolerates an already-gone directory (runs ``worktree prune`` instead). A
    residual :class:`OSError` from a locked directory is re-raised so the caller
    can defer cleanup.
    """
    top = repo.toplevel if isinstance(repo, RepoInfo) else str(repo)
    dest = os.path.normpath(str(worktree_path))
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(dest)
    proc = _run(args, top, check=False, timeout=60.0)
    if proc.returncode == 0:
        return
    detail = (proc.stderr or proc.stdout or "").strip().lower()
    if "is not a working tree" in detail or "not a valid" in detail or "no such" in detail:
        prune_worktrees(top)
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        return
    # Last resort: prune the admin entry and remove the tree ourselves.
    prune_worktrees(top)
    if os.path.isdir(dest):
        shutil.rmtree(dest)  # may raise OSError -> caller defers to next launch


def delete_branch(repo: "RepoInfo | str", branch: str, *, force: bool = True) -> None:
    """Delete a local branch. No-op (no raise) if it's already gone."""
    if not branch:
        return
    top = repo.toplevel if isinstance(repo, RepoInfo) else str(repo)
    flag = "-D" if force else "-d"
    _run(["branch", flag, branch], top, check=False, timeout=30.0)


def prune_worktrees(repo: "RepoInfo | str") -> None:
    """``git worktree prune`` -- drop admin entries for vanished directories."""
    top = repo.toplevel if isinstance(repo, RepoInfo) else str(repo)
    _run(["worktree", "prune"], top, check=False, timeout=30.0)


# --------------------------------------------------------------------------- #
# Status / diff
# --------------------------------------------------------------------------- #

def _rev(cwd, ref: str) -> str:
    return _out(["rev-parse", "--verify", "-q", ref], cwd, check=False)


def is_dirty(worktree_path: "str | os.PathLike") -> "tuple[bool, list[str]]":
    """``(dirty, paths)`` from ``git status --porcelain`` for a worktree."""
    if not os.path.isdir(worktree_path):
        return False, []
    out = _out(["status", "--porcelain"], worktree_path, check=False)
    files = [ln[3:].strip() for ln in out.splitlines() if ln.strip()]
    return bool(files), files


def _tracked_changes(worktree_path: str) -> "list[str]":
    """Modified/staged tracked paths only -- untracked (``??``) files excluded.

    ``git merge`` handles untracked files itself (it only aborts when one would
    be clobbered), so they should not block an in-place merge.
    """
    if not os.path.isdir(worktree_path):
        return []
    out = _out(["status", "--porcelain"], worktree_path, check=False)
    return [
        ln[3:].strip()
        for ln in out.splitlines()
        if ln.strip() and not ln.startswith("??")
    ]


def _ahead_behind(worktree_path: str, base_ref: str) -> "tuple[int, int]":
    """``(ahead, behind)`` of HEAD relative to ``base_ref``."""
    if not base_ref:
        return 0, 0
    out = _out(
        ["rev-list", "--left-right", "--count", f"{base_ref}...HEAD"],
        worktree_path,
        check=False,
    )
    parts = out.split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        behind, ahead = int(parts[0]), int(parts[1])
        return ahead, behind
    return 0, 0


def _resolve_base_ref(worktree_path: str, base_branch: str) -> str:
    """Pick a resolvable ref for ``base_branch`` as seen from the worktree."""
    for ref in (base_branch, f"refs/heads/{base_branch}", f"origin/{base_branch}"):
        if ref and _rev(worktree_path, ref):
            return ref
    return ""


def _merge_base(cwd, a: str, b: str) -> str:
    """The common ancestor of ``a`` and ``b`` (``""`` if there is none)."""
    if not a or not b:
        return ""
    return _out(["merge-base", a, b], cwd, check=False)


def _diff_base(worktree_path: str, base_ref: str) -> str:
    """The ref a worktree-vs-base diff should be taken against.

    Not ``base_ref`` itself: once the base branch advances past where this
    worktree forked, diffing against its *tip* renders base's own new commits
    as deletions in the worktree's "review" diff. The merge-base of
    ``base_ref`` and ``HEAD`` isolates just the worktree's own work.
    """
    return _merge_base(worktree_path, base_ref, "HEAD") or base_ref


def _checked_out_worktree(top: str, branch: str) -> str:
    """Path of the registered worktree that has ``branch`` checked out, or ``""``.

    ``list_worktrees`` already strips the ``refs/heads/`` prefix, so the entry's
    ``branch`` is the short name.
    """
    if not branch:
        return ""
    for e in list_worktrees(top):
        if e.get("branch", "") == branch:
            return e.get("path", "")
    return ""


def status(worktree_path: "str | os.PathLike", base_branch: str) -> WorktreeStatus:
    """A :class:`WorktreeStatus` snapshot for one worktree."""
    path = os.path.normpath(str(worktree_path))
    exists = os.path.isdir(path)
    if not exists:
        return WorktreeStatus(
            path=path, branch="", head_sha="", base_branch=base_branch,
            base_sha="", ahead=0, behind=0, dirty=False, dirty_files=0,
            exists=False, registered=False,
        )
    branch = _out(["rev-parse", "--abbrev-ref", "HEAD"], path, check=False)
    if branch == "HEAD":
        branch = ""
    head_sha = _rev(path, "HEAD")
    base_ref = _resolve_base_ref(path, base_branch)
    base_sha = _rev(path, base_ref) if base_ref else ""
    ahead, behind = _ahead_behind(path, base_ref)
    dirty, files = is_dirty(path)
    registered = False
    try:
        top = _out(["rev-parse", "--show-toplevel"], path, check=False)
        common = _out(["rev-parse", "--git-common-dir"], path, check=False)
        main_top = os.path.normpath(str(Path(common).parent)) if common else top
        registered = _wt_registered(main_top or top, path)
    except GitError:
        registered = False
    return WorktreeStatus(
        path=path, branch=branch, head_sha=head_sha, base_branch=base_branch,
        base_sha=base_sha, ahead=ahead, behind=behind, dirty=dirty,
        dirty_files=len(files), exists=True, registered=registered,
    )


def _numstat(worktree_path: str, base_ref: str) -> "dict[str, tuple[int, int]]":
    """``{path: (added, removed)}`` from ``git diff --numstat`` (two-dot:
    includes the working tree)."""
    out = _out(
        ["diff", "--numstat", "--find-renames", base_ref], worktree_path, check=False
    )
    result: dict[str, tuple[int, int]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added = int(parts[0]) if parts[0].isdigit() else 0
        removed = int(parts[1]) if parts[1].isdigit() else 0
        path = parts[2]
        # rename form "old => new" inside braces already collapsed by git in
        # --numstat only for the "{a => b}" style; keep the raw path either way.
        result[path] = (added, removed)
    return result


def diff_stat(worktree_path: "str | os.PathLike", base_branch: str) -> "list[FileDelta]":
    """Per-file deltas of the worktree (incl. uncommitted) against its base."""
    path = os.path.normpath(str(worktree_path))
    if not os.path.isdir(path):
        return []
    base_ref = _resolve_base_ref(path, base_branch)
    if not base_ref:
        return []
    diff_base = _diff_base(path, base_ref)
    nums = _numstat(path, diff_base)
    out = _out(
        ["diff", "--name-status", "--find-renames", diff_base], path, check=False
    )
    deltas: list[FileDelta] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0]
        letter = code[0]
        if letter in ("R", "C") and len(parts) >= 3:
            old_path, new_path = parts[1], parts[2]
            added, removed = nums.get(new_path, nums.get(f"{old_path} => {new_path}", (0, 0)))
            deltas.append(FileDelta(letter, new_path, old_path, added, removed))
        else:
            new_path = parts[1]
            added, removed = nums.get(new_path, (0, 0))
            deltas.append(FileDelta(letter, new_path, "", added, removed))
    deltas.sort(key=lambda d: d.path.lower())
    return deltas


def diff_text(
    worktree_path: "str | os.PathLike",
    base_branch: str,
    *,
    path: "str | None" = None,
    context: int = 3,
    max_bytes: int = DEFAULT_DIFF_MAX_BYTES,
) -> str:
    """The unified diff of the worktree against its base (optionally one file).

    Truncated with a trailing marker once it passes ``max_bytes`` so a huge
    diff can't lock up the text view.
    """
    wt = os.path.normpath(str(worktree_path))
    if not os.path.isdir(wt):
        return ""
    base_ref = _resolve_base_ref(wt, base_branch)
    if not base_ref:
        return ""
    diff_base = _diff_base(wt, base_ref)
    args = ["diff", "--no-color", f"-U{max(0, int(context))}", "--find-renames", diff_base]
    if path:
        args += ["--", path]
    text = _out(args, wt, check=False, timeout=60.0)
    if len(text.encode("utf-8", "replace")) > max_bytes:
        clipped = text.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "ignore")
        return clipped + "\n\n… diff truncated — open this worktree in a pane to see it all\n"
    return text


# --------------------------------------------------------------------------- #
# Mutations (v2 -- merge / rebase / push)
# --------------------------------------------------------------------------- #

def commit_all(worktree_path: "str | os.PathLike", message: str) -> str:
    """Stage everything in the worktree and commit. Returns the new sha.

    Raises :class:`GitError` when there is nothing to commit.
    """
    wt = str(worktree_path)
    _run(["add", "-A"], wt)
    proc = _run(["commit", "-m", message or "WIP (AgentDeck worktree)"], wt, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if "nothing to commit" in detail.lower():
            raise GitError("nothing to commit in this worktree")
        raise GitError(f"git commit failed: {detail}")
    return _rev(wt, "HEAD")


def base_moved(repo: "RepoInfo | str", base: str, known_base_sha: str) -> bool:
    """True when ``base``'s tip has moved on from ``known_base_sha``."""
    if not known_base_sha:
        return False
    top = repo.toplevel if isinstance(repo, RepoInfo) else str(repo)
    ref = _resolve_base_ref(top, base)
    live = _rev(top, ref) if ref else ""
    return bool(live) and live != known_base_sha


def rebase_onto_base(worktree_path: "str | os.PathLike", base: str) -> MergeResult:
    """Rebase the worktree's branch onto ``base``. Aborts + raises on conflict."""
    wt = str(worktree_path)
    base_ref = _resolve_base_ref(wt, base)
    if not base_ref:
        raise GitError(f"cannot resolve base branch {base!r}")
    before = _rev(wt, "HEAD")
    proc = _run(["rebase", base_ref], wt, check=False, timeout=120.0)
    if proc.returncode != 0:
        conflicts = _out(
            ["diff", "--name-only", "--diff-filter=U"], wt, check=False
        ).splitlines()
        _run(["rebase", "--abort"], wt, check=False)
        raise WorktreeConflict("rebase hit conflicts", conflicts)
    return MergeResult(
        ok=True, fast_forward=False, merge_sha=_rev(wt, "HEAD"),
        base_sha_before=before, conflicts=[], message="rebased",
    )


def _merge_args(branch: str, base: str, mode: str, message: "str | None") -> "list[str]":
    args = ["merge"]
    if mode == "ff-only":
        args.append("--ff-only")
    elif mode == "no-ff":
        args.append("--no-ff")
    if message:
        args += ["-m", message]
    elif mode != "ff-only":
        args += ["-m", f"Merge {branch} into {base} (AgentDeck)"]
    args.append(branch)
    return args


def _fast_forwarded(cwd, sha: str) -> bool:
    # `rev-list --parents -n1` prints "<sha> <parent...>": <=2 tokens == single
    # parent == no merge commit was created.
    parents = _out(["rev-list", "--parents", "-n", "1", sha], cwd).split()
    return len(parents) <= 2


def _merge_in_place(
    host: str, *, branch: str, base: str, mode: str, message: "str | None",
    old_base_sha: str,
) -> MergeResult:
    """Merge ``branch`` into ``base`` directly in the worktree that has ``base``
    checked out. Keeps that worktree's index/tree consistent with the moved ref.

    Refuses (``DirtyWorktree``) if the host worktree has uncommitted changes to
    *tracked* files -- a merge there would collide with the user's own work.
    Untracked files are left for ``git merge`` itself to guard.
    """
    changed = _tracked_changes(host)
    if changed:
        raise DirtyWorktree(
            f"your checkout of {base!r} has uncommitted changes — commit or "
            "stash them before merging a worktree into it",
            changed,
        )
    proc = _run(_merge_args(branch, base, mode, message), host, check=False, timeout=120.0)
    if proc.returncode != 0:
        conflicts = _out(
            ["diff", "--name-only", "--diff-filter=U"], host, check=False
        ).splitlines()
        _run(["merge", "--abort"], host, check=False)
        if conflicts:
            raise WorktreeConflict(
                f"merging {branch} into {base} hit conflicts", conflicts
            )
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitError(f"git merge failed: {detail}")
    new_sha = _rev(host, "HEAD")
    ff = _fast_forwarded(host, new_sha)
    return MergeResult(
        ok=True, fast_forward=ff, merge_sha=new_sha, base_sha_before=old_base_sha,
        conflicts=[], message="fast-forward" if ff else "merge commit",
    )


def merge_to_base(
    repo: "RepoInfo | str",
    *,
    branch: str,
    base: str,
    mode: str = "auto",
    message: "str | None" = None,
    scratch_root: "str | os.PathLike | None" = None,
) -> MergeResult:
    """Merge ``branch`` into ``base`` without leaving the user's checkout dirty.

    ``mode``: ``"ff-only"`` (fail unless fast-forwardable), ``"no-ff"`` (always a
    merge commit), or ``"auto"`` (fast-forward when possible).

    * If ``base`` is checked out in a worktree, the merge runs *there* -- moving
      the branch ref out from under a checked-out worktree via ``update-ref``
      would leave that worktree's index/tree desynced from HEAD (every merged
      file shows as deleted in ``git status``). Refuses with
      :class:`DirtyWorktree` if that worktree has uncommitted changes.
    * Otherwise it runs in an *ephemeral* detached worktree and advances
      ``refs/heads/<base>`` with a compare-and-swap ``update-ref``.

    Raises :class:`WorktreeConflict` on conflicts (nothing is changed) and
    :class:`GitError` for everything else.
    """
    info = repo if isinstance(repo, RepoInfo) else detect_repo(repo)
    top = info.toplevel
    base_ref = _resolve_base_ref(top, base)
    if not base_ref:
        raise GitError(f"cannot resolve base branch {base!r}")
    if not _rev(top, branch):
        raise GitError(f"cannot resolve branch {branch!r}")

    old_base_sha = _rev(top, f"refs/heads/{base}") or _rev(top, base_ref)
    if not old_base_sha:
        raise GitError(f"base branch {base!r} has no commits")

    host = _checked_out_worktree(top, base)
    if host and os.path.isdir(host):
        return _merge_in_place(
            host, branch=branch, base=base, mode=mode, message=message,
            old_base_sha=old_base_sha,
        )

    root = Path(scratch_root) if scratch_root else Path(top).parent
    tmp = root / f".agentdeck-merge-{os.getpid()}-{_short_token()}"

    _run(["worktree", "add", "--detach", str(tmp), old_base_sha], top, timeout=120.0)
    try:
        proc = _run(
            _merge_args(branch, base, mode, message), str(tmp), check=False, timeout=120.0
        )
        if proc.returncode != 0:
            conflicts = _out(
                ["diff", "--name-only", "--diff-filter=U"], str(tmp), check=False
            ).splitlines()
            _run(["merge", "--abort"], str(tmp), check=False)
            if conflicts:
                raise WorktreeConflict(
                    f"merging {branch} into {base} hit conflicts", conflicts
                )
            detail = (proc.stderr or proc.stdout or "").strip()
            raise GitError(f"git merge failed: {detail}")

        new_sha = _rev(str(tmp), "HEAD")
        fast_forward = _fast_forwarded(str(tmp), new_sha)

        cas = _run(
            ["update-ref", f"refs/heads/{base}", new_sha, old_base_sha], top, check=False
        )
        if cas.returncode != 0:
            raise GitError(
                "base branch moved during the merge — nothing was changed; refresh and retry"
            )
        return MergeResult(
            ok=True, fast_forward=fast_forward, merge_sha=new_sha,
            base_sha_before=old_base_sha, conflicts=[],
            message="fast-forward" if fast_forward else "merge commit",
        )
    finally:
        _run(["worktree", "remove", "--force", str(tmp)], top, check=False)
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        prune_worktrees(top)


def push_branch(
    worktree_path: "str | os.PathLike",
    *,
    remote: str = "origin",
    branch: str,
    set_upstream: bool = True,
) -> str:
    """Push ``branch`` to ``remote`` from the worktree. Returns the remote ref.

    Relies on the user's git credential helper; ``GIT_TERMINAL_PROMPT=0`` means
    a missing helper fails fast instead of hanging.
    """
    wt = str(worktree_path)
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args += [remote, f"{branch}:{branch}"]
    proc = _run(args, wt, check=False, timeout=120.0)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitError(f"git push failed: {detail}")
    return f"{remote}/{branch}"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _short_token() -> str:
    import secrets

    return secrets.token_hex(3)


def _is_ancestor(cwd, maybe_ancestor: str, ref: str) -> bool:
    return _run(
        ["merge-base", "--is-ancestor", maybe_ancestor, ref], cwd, check=False
    ).returncode == 0
