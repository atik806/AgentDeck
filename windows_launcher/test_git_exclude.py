"""Offline tests for git_exclude.py -- runs against a real temp repository.

    .venv\\Scripts\\python.exe test_git_exclude.py

The point of this module is a case unit tests kept missing: inside a **linked
worktree** ``.git`` is a file, not a directory, and the old inline copies of this
helper bailed out there without excluding anything. So these tests assert against
git itself (``git status --porcelain`` / ``git add``), not just against the bytes
we wrote -- "the exclude file contains the line" is not the same claim as "git
ignores the file".

Skips (exit 0) with a clear message when git is not on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import git_exclude
import git_worktree as gw

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def _git(cwd, *args: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env
    ).stdout.strip()


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _drop_scratch(folder: Path, name: str = "handoff-secret.md") -> Path:
    d = folder / ".agentdeck"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("# Handoff\n\nsk-not-a-real-token\n", encoding="utf-8")
    return p


def _untracked(folder: Path) -> str:
    return _git(folder, "status", "--porcelain")


if shutil.which("git") is None:
    print("git not on PATH -- skipping")
    raise SystemExit(0)

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    repo = _make_repo(root)

    # -----------------------------------------------------------------------
    print("[1] ordinary checkout (.git is a directory)")
    _drop_scratch(repo)
    check(".agentdeck shows before excluding", ".agentdeck" in _untracked(repo))
    check("add() reports success", git_exclude.add(repo, ".agentdeck/") is True)
    check("git now ignores it", ".agentdeck" not in _untracked(repo))
    check("idempotent", git_exclude.add(repo, ".agentdeck/") is True)
    body = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    check("pattern written exactly once", body.split().count(".agentdeck/") == 1)

    # -----------------------------------------------------------------------
    # A fresh repo, so the "before" state is honest: repo #1's exclude is in its
    # common dir and therefore already covers any worktree cut from it (asserted
    # at the end of this block -- that sharing is the point of the fix).
    print("[2] linked worktree (.git is a FILE -- the regression)")
    repo2 = root / "repo2"
    repo2.mkdir()
    _git(repo2, "init", "-b", "main")
    _git(repo2, "config", "user.email", "t@t.t")
    _git(repo2, "config", "user.name", "Test")
    (repo2 / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo2, "add", "-A")
    _git(repo2, "commit", "-m", "init")

    wt = root / "wt"
    _git(repo2, "worktree", "add", "-b", "feature", str(wt))
    check("precondition: .git is a file here", (wt / ".git").is_file())

    gd = git_exclude.git_dir(wt)
    check("git_dir() resolves through the gitdir: pointer", gd is not None and gd.is_dir())
    # realpath on both sides: Windows hands us %TEMP% in 8.3 short form
    # ("ATIKSH~1") while git reports the long one -- same dir, different string.
    check(
        "common_dir() points back at the main .git",
        gd is not None
        and os.path.normcase(os.path.realpath(git_exclude.common_dir(gd)))
        == os.path.normcase(os.path.realpath(repo2 / ".git")),
    )

    secret = _drop_scratch(wt)
    check("scratch shows in the worktree first", ".agentdeck" in _untracked(wt))
    check("add() succeeds from the worktree", git_exclude.add(wt, ".agentdeck/") is True)
    check("git ignores it in the worktree", ".agentdeck" not in _untracked(wt))
    check(
        "wrote to the shared common dir, not the worktree's own admin dir",
        ".agentdeck/" in (repo2 / ".git" / "info" / "exclude").read_text(encoding="utf-8"),
    )
    # ...which is why a *later* worktree of repo #1 is covered without asking.
    wt_b = root / "wt-b"
    _git(repo, "worktree", "add", "-b", "later", str(wt_b))
    _drop_scratch(wt_b)
    check("a worktree made later inherits the exclusion", ".agentdeck" not in _untracked(wt_b))

    # -----------------------------------------------------------------------
    print("[3] commit_all never stages the scratch dir")
    (wt / "work.txt").write_text("real work\n", encoding="utf-8")
    sha = gw.commit_all(wt, "wt work")
    files = _git(wt, "show", "--name-only", "--format=", sha).split()
    check("the real edit is committed", "work.txt" in files)
    check("the handoff transcript is not", not any(f.startswith(".agentdeck") for f in files))
    check("and it is still on disk for the agent to read", secret.is_file())

    # Even with the exclude file deliberately unwritable/absent, the pathspec
    # in commit_all must still hold the line.
    print("[4] commit_all holds even when the exclude never got written")
    (repo2 / ".git" / "info" / "exclude").write_text("", encoding="utf-8")
    _drop_scratch(wt, "handoff-second.md")
    (wt / "more.txt").write_text("more\n", encoding="utf-8")
    check("scratch is visible again (exclude wiped)", ".agentdeck" in _untracked(wt))
    sha2 = gw.commit_all(wt, "more work")
    files2 = _git(wt, "show", "--name-only", "--format=", sha2).split()
    check("real edit committed", "more.txt" in files2)
    check("scratch still not committed", not any(f.startswith(".agentdeck") for f in files2))

    # -----------------------------------------------------------------------
    print("[5] not a repository / bad input")
    plain = root / "plain"
    plain.mkdir()
    check("non-repo folder -> False", git_exclude.add(plain, ".agentdeck/") is False)
    check("non-repo folder -> git_dir None", git_exclude.git_dir(plain) is None)
    check("empty pattern -> False", git_exclude.add(repo, "") is False)
    bogus = root / "bogus"
    bogus.mkdir()
    (bogus / ".git").write_text("not a gitdir pointer\n", encoding="utf-8")
    check("unparseable .git file -> False", git_exclude.add(bogus, ".agentdeck/") is False)

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
