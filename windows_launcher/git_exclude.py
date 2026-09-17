"""Add a pattern to a repository's ``info/exclude`` -- worktree-aware.

AgentDeck drops scratch files into the *working folder* (``.agentdeck/``: cross-agent
handoff transcripts, PR review briefs, materialised skills). Those are local
artefacts that must never be committed, so each writer asks git to ignore the
directory. ``.gitignore`` is tracked and belongs to the user, so the pattern goes
into ``info/exclude`` instead -- local-only, never committed, invisible to
``git status``.

Three modules used to carry their own copy of this, and every copy assumed
``<folder>/.git`` is a **directory**:

    exclude = folder / ".git" / "info" / "exclude"
    if not exclude.parent.is_dir():
        return                      # <-- silently gave up

In a linked worktree ``.git`` is a *file* holding ``gitdir: <path>``, so that
check failed and the exclusion never happened -- exactly where AgentDeck's own
isolated panes run, and where ``git_worktree.commit_all`` then ran ``git add -A``
and ``push_branch`` published the result. A handoff transcript is a verbatim
record of an agent conversation; pushing one to a shared branch is a real leak.

So resolve the admin directory properly:

* ``.git`` is a directory -> that is the git dir (an ordinary checkout);
* ``.git`` is a file -> read ``gitdir:`` out of it (a linked worktree, or a
  submodule) and then follow that dir's ``commondir`` marker to the *shared*
  admin dir.

The common dir is the right target either way: git reads ``info/exclude`` from
``$GIT_COMMON_DIR``, so one write covers the main checkout and every worktree
hanging off it.

Qt-free, stdlib-only, and it never raises -- a read-only ``.git``, a foreign
file or a folder that is not a repository at all all read as "couldn't exclude"
(``False``), which callers treat as a reason to be careful elsewhere rather than
as an error to show the user.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

__all__ = ["add", "git_dir", "common_dir"]


def git_dir(folder: "str | Path") -> Optional[Path]:
    """The git admin dir for ``folder``, or ``None`` if it isn't a work tree.

    Handles both shapes of ``.git``: a directory (ordinary checkout) and a file
    containing ``gitdir: <path>`` (linked worktree / submodule).
    """
    dot = Path(folder) / ".git"
    if dot.is_dir():
        return dot
    try:
        text = dot.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    target = Path(text.split(":", 1)[1].strip())
    if not target.is_absolute():
        target = Path(folder) / target
    try:
        target = target.resolve()
    except OSError:
        return None
    return target if target.is_dir() else None


def common_dir(gitdir: "str | Path") -> Path:
    """The shared admin dir for ``gitdir``.

    A linked worktree's admin dir (``<repo>/.git/worktrees/<name>``) carries a
    ``commondir`` file pointing back at ``<repo>/.git``; anything else is its own
    common dir. Falls back to ``gitdir`` whenever the marker is missing or does
    not resolve to a real directory.
    """
    gitdir = Path(gitdir)
    try:
        rel = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return gitdir
    if not rel:
        return gitdir
    common = Path(rel)
    if not common.is_absolute():
        common = gitdir / common
    try:
        common = common.resolve()
    except OSError:
        return gitdir
    return common if common.is_dir() else gitdir


def add(folder: "str | Path", pattern: str) -> bool:
    """Ensure ``pattern`` is in ``folder``'s repository-local exclude file.

    Returns True when the pattern is in place (already there, or just written)
    and False when ``folder`` is not a work tree or the file could not be
    written. Never raises.
    """
    if not pattern:
        return False
    gd = git_dir(folder)
    if gd is None:
        return False
    info = common_dir(gd) / "info"
    exclude = info / "exclude"
    try:
        info.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if pattern in existing.split():
            return True
        with exclude.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"{pattern}\n")
        return True
    except OSError:
        return False
