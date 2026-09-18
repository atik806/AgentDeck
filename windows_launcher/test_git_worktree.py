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
        # main is checked out in `repo`; an uncommitted *tracked* change here
        # must block the in-place merge. Prove that, then revert and merge for
        # real. (An untracked file must NOT block -- git merge guards those.)
        (repo / "untracked.txt").write_text("scratch\n", encoding="utf-8")
        (repo / "c.txt").write_text("locally edited\n", encoding="utf-8")
        try:
            gw.merge_to_base(info, branch=st.branch, base="main", scratch_root=str(wt_root))
            check(False, "merge_to_base raises DirtyWorktree when base checkout is dirty")
        except gw.DirtyWorktree:
            check(True, "merge_to_base raises DirtyWorktree when base checkout is dirty")
        _git(repo, "checkout", "--", "c.txt")
        (repo / "untracked.txt").unlink()

        res = gw.merge_to_base(info, branch=st.branch, base="main", scratch_root=str(wt_root))
        check(res.ok, "merge_to_base ok")
        merged_main = _git(repo, "log", "--oneline", "main")
        check("wt changes" in merged_main, "base branch now contains the worktree commit")
        # user's checkout still on main *and* not left showing the merge as a
        # pile of pending deletions (the whole point of the in-place path).
        check(_git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main",
              "user checkout still on main")
        check(_git(repo, "status", "--porcelain") == "",
              "user checkout clean after merge (not desynced from the moved ref)")
        check(_git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "main"),
              "user checkout HEAD == merged main")

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

        # -- rebase_onto_base --
        rdest = wt_root / "pr"
        gw.add_worktree(info, worktree_path=rdest, branch="agentdeck/test/pr", base="main")
        (rdest / "r.txt").write_text("rebase me\n", encoding="utf-8")
        gw.commit_all(rdest, "wt work to rebase")
        (repo / "d.txt").write_text("main moved on\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "main moved on")
        result = gw.rebase_onto_base(rdest, "main")
        check(result.ok, "rebase_onto_base ok")
        check(_git(rdest, "log", "--oneline").count("main moved on") == 1,
              "rebased branch now contains the moved base commit")
        check(int(_git(rdest, "rev-list", "--count", "HEAD").strip()) ==
              int(_git(repo, "rev-list", "--count", "main").strip()) + 1,
              "rebased branch's history length is base + its own commit")

        # -- rebase_onto_base conflict --
        rcdest = wt_root / "prc"
        gw.add_worktree(info, worktree_path=rcdest, branch="agentdeck/test/prc", base="main")
        (rcdest / "a.txt").write_text("conflict-side-2\n", encoding="utf-8")
        gw.commit_all(rcdest, "conflicting worktree edit")
        before_rebase = _git(rcdest, "rev-parse", "HEAD")
        (repo / "a.txt").write_text("conflict-main-2\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "conflicting main edit")
        try:
            gw.rebase_onto_base(rcdest, "main")
            check(False, "rebase_onto_base raises WorktreeConflict on conflict")
        except gw.WorktreeConflict as exc:
            check("a.txt" in exc.conflicts, "rebase conflict lists a.txt")
        check(_git(rcdest, "rev-parse", "HEAD") == before_rebase,
              "branch back where it started after a failed rebase (aborted)")
        check(_git(rcdest, "status", "--porcelain") == "",
              "worktree clean after a failed rebase (aborted)")

        # -- rebase_onto_base still aborts on a timeout, not just a conflict --
        # ``_run`` raises GitError straight out of subprocess.TimeoutExpired,
        # bypassing the returncode-based abort entirely unless rebase_onto_base
        # catches it separately.
        rtdest = wt_root / "prt"
        gw.add_worktree(info, worktree_path=rtdest, branch="agentdeck/test/prt", base="main")
        (rtdest / "t.txt").write_text("timeout test\n", encoding="utf-8")
        gw.commit_all(rtdest, "wt work for timeout test")
        real_run = gw._run
        abort_calls = []

        def _fake_run(args, cwd, **kw):
            if args[:1] == ["rebase"] and args[1:2] != ["--abort"]:
                raise gw.GitError("git rebase timed out after 120s")
            if args[:2] == ["rebase", "--abort"]:
                abort_calls.append(True)
            return real_run(args, cwd, **kw)

        gw._run = _fake_run
        try:
            gw.rebase_onto_base(rtdest, "main")
            check(False, "a timed-out rebase re-raises GitError")
        except gw.GitError as exc:
            check("timed out" in str(exc), "a timed-out rebase re-raises GitError")
        finally:
            gw._run = real_run
        check(bool(abort_calls), "a timed-out rebase still runs `git rebase --abort`")

        # -- remove_worktree + delete_branch + prune --
        gw.remove_worktree(info, dest, force=True)
        check(not dest.is_dir(), "remove_worktree deleted the dir")
        gw.delete_branch(info, st.branch)
        check(st.branch not in _git(repo, "branch", "--list", st.branch),
              "delete_branch removed the branch")
        gw.prune_worktrees(info)
        check(True, "prune_worktrees ran without error")

        # -- a worktree folder that vanished under a running git call --
        # Discarding a worktree while the Review panel is still probing/diffing
        # it used to let Windows' NotADirectoryError (WinError 267) escape the
        # module and crash the app. Every spawn failure must surface as
        # GitError, and the guarded readers must simply come back empty.
        try:
            gw._run(["status", "--porcelain"], dest, check=False)
            check(False, "git in a deleted worktree raises GitError")
        except gw.GitError as exc:
            check("gone" in str(exc).lower(), "git in a deleted worktree raises GitError")
        except OSError:
            check(False, "git in a deleted worktree raises GitError, not OSError")
        check(gw.diff_text(dest, "main") == "", "diff_text on a deleted worktree is empty")
        check(gw.diff_stat(dest, "main") == [], "diff_stat on a deleted worktree is empty")

        # ...and when the folder goes away *after* the isdir() guard, the
        # GitError still reaches the caller rather than a raw OSError.
        real_isdir = os.path.isdir
        os.path.isdir = lambda p, _d=str(dest), _r=real_isdir: True if str(p) == _d else _r(p)
        try:
            gw.diff_text(dest, "main")
            check(False, "a worktree deleted mid-diff raises GitError")
        except gw.GitError:
            check(True, "a worktree deleted mid-diff raises GitError")
        except OSError:
            check(False, "a worktree deleted mid-diff raises GitError, not OSError")
        finally:
            os.path.isdir = real_isdir

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
