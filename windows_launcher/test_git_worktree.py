"""Offline tests for git_worktree.py -- runs against a real temp repository.

    .venv\\Scripts\\python.exe test_git_worktree.py

Skips (exit 0) with a clear message when git is not on PATH.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import git_worktree as gw

_PASS = 0
_FAIL = 0


def check(cond: bool, label: str) -> None:
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
    (repo / "keep.txt").write_text("keep\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def main() -> int:
    if not gw.git_available():
        print("git not on PATH — skipping git_worktree tests")
        return 0

    # A temp dir that is itself inside a git repo (e.g. because %TEMP% lives
    # under a repo'd home directory) would make the "plain dir" negative checks
    # meaningless -- detect that and skip just those.
    outer_repo = gw.is_git_repo(tempfile.gettempdir())

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = _make_repo(root)
        wt_root = root / "wts"

        def canon(p) -> str:
            return os.path.normcase(os.path.realpath(str(p)))

        # -- discovery --
        check(gw.is_git_repo(repo), "is_git_repo(repo)")
        check(not gw.is_git_repo(root / "nope"), "is_git_repo(missing) is False")
        plain = root / "plain"
        plain.mkdir()
        if not outer_repo:
            check(not gw.is_git_repo(plain), "is_git_repo(plain dir) is False")

        info = gw.detect_repo(repo)
        check(canon(info.toplevel) == canon(repo), "detect_repo.toplevel")
        check(info.default_base == "main", f"default_base == main (got {info.default_base!r})")
        check(info.remote_slug == "", "no remote slug without origin")
        if not outer_repo:
            try:
                gw.detect_repo(plain)
                check(False, "detect_repo(plain) raises NotAGitRepo")
            except gw.NotAGitRepo:
                check(True, "detect_repo(plain) raises NotAGitRepo")

        # -- add_worktree --
        branch = "agentdeck/test/p1-xyz"
        dest = wt_root / "p1"
        st = gw.add_worktree(info, worktree_path=dest, branch=branch, base="main")
        check(dest.is_dir(), "worktree dir created")
        check(st.branch == branch, f"status.branch == {branch} (got {st.branch})")
        check(st.ahead == 0 and st.behind == 0, "fresh worktree even with base")
        listed = gw.list_worktrees(info)
        paths = {canon(e["path"]) for e in listed}
        check(canon(dest) in paths, "list_worktrees sees the new worktree")

        # branch-name collision -> retry with suffix
        dest2 = wt_root / "p1b"
        st2 = gw.add_worktree(info, worktree_path=dest2, branch=branch, base="main")
        check(st2.branch != branch and st2.branch.startswith(branch),
              f"collision retried with suffix (got {st2.branch})")

        # -- status: ahead / behind / dirty --
        (dest / "b.txt").write_text("new\n", encoding="utf-8")
        d, files = gw.is_dirty(dest)
        check(d and "b.txt" in files, "is_dirty detects the untracked file")
        _git(dest, "add", "-A")
        _git(dest, "commit", "-m", "add b")
        st = gw.status(dest, "main")
        check(st.ahead == 1, f"ahead == 1 after a commit (got {st.ahead})")
        check(st.behind == 0, "behind == 0")
        check(not st.dirty, "clean after commit")

        (repo / "c.txt").write_text("on main\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "advance main")
        st = gw.status(dest, "main")
        check(st.behind == 1, f"behind == 1 after base advanced (got {st.behind})")
        check(gw.base_moved(info, "main", st2.base_sha), "base_moved True after base advanced")

        # -- diff_stat / diff_text --
        (dest / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
        (dest / "keep.txt").unlink()
        _git(dest, "mv", "b.txt", "b_renamed.txt")
        deltas = {fd.status: fd for fd in gw.diff_stat(dest, "main")}
        check("M" in deltas, "diff_stat sees a modified file")
        check("D" in deltas, "diff_stat sees a deleted file")
        check(any(fd.status == "A" for fd in gw.diff_stat(dest, "main")) or "R" in deltas,
              "diff_stat sees the rename (as A/ R)")
        txt = gw.diff_text(dest, "main")
        check("+world" in txt, "diff_text contains the added line")
        tiny = gw.diff_text(dest, "main", max_bytes=10)
        check("truncated" in tiny, "diff_text truncates past max_bytes")

        # -- commit_all + merge_to_base --
        gw.commit_all(dest, "wt changes")
        res = gw.merge_to_base(info, branch=st.branch, base="main", scratch_root=str(wt_root))
        check(res.ok, "merge_to_base ok")
        merged_main = _git(repo, "log", "--oneline", "main")
        check("wt changes" in merged_main, "base branch now contains the worktree commit")
        # user's checkout untouched (still on main, working tree clean of the merge)
        check(_git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main",
              "user checkout still on main")

        # -- conflict path --
        cdest = wt_root / "pc"
        cst = gw.add_worktree(info, worktree_path=cdest, branch="agentdeck/test/pc",
                              base="main")
        (cdest / "a.txt").write_text("conflict-side\n", encoding="utf-8")
        gw.commit_all(cdest, "conflict from worktree")
        (repo / "a.txt").write_text("conflict-main\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "conflict from main")
        try:
            gw.merge_to_base(info, branch=cst.branch, base="main", scratch_root=str(wt_root))
            check(False, "merge_to_base raises WorktreeConflict on conflict")
        except gw.WorktreeConflict as exc:
            check("a.txt" in exc.conflicts, "conflict lists a.txt")
        check(_git(repo, "log", "--oneline", "main").count("conflict from worktree") == 0,
              "nothing merged on conflict")

        # -- remove_worktree + delete_branch + prune --
        gw.remove_worktree(info, dest, force=True)
        check(not dest.is_dir(), "remove_worktree deleted the dir")
        gw.delete_branch(info, st.branch)
        check(st.branch not in _git(repo, "branch", "--list", st.branch),
              "delete_branch removed the branch")
        gw.prune_worktrees(info)
        check(True, "prune_worktrees ran without error")

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
