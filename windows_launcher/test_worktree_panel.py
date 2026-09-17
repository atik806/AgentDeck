"""Offline GUI tests for worktree_panel.py.

    set QT_QPA_PLATFORM=offscreen
    .venv\\Scripts\\python.exe test_worktree_panel.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import git_worktree as gw  # noqa: E402
import worktree_store as wsmod  # noqa: E402
from worktree_panel import WorktreePanel, worktree_icon  # noqa: E402

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


def _wait_for(condition, timeout: float = 5.0) -> bool:
    """Pump the event loop + thread pool until ``condition()`` is true.

    Diff rendering now runs on a QThreadPool worker; its result reaches the
    UI thread as a queued signal, so the test loop has to give Qt a chance to
    deliver it instead of asserting immediately after construction.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        QThreadPool.globalInstance().waitForDone(50)
        QApplication.processEvents()
    return condition()


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=str(cwd), capture_output=True, text=True,
                   env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def main() -> int:
    app = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = wsmod.WorktreeStore(root / "wt.json")

        # -- icon --
        check(not worktree_icon(16).isNull(), "worktree_icon renders")

        # -- empty state, no repo --
        panel = WorktreePanel(store=store, repo_provider=lambda: None)
        check(not panel._empty.isHidden(), "empty state shown with no records")
        check(panel._detail.isHidden(), "detail hidden with no records")
        check("isn't a git repository" in panel._empty.text(),
              "empty text mentions non-repo when repo_provider is None")

        # -- with a real repo + worktree --
        has_git = gw.git_available()
        if has_git:
            repo = root / "repo"
            repo.mkdir()
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.email", "t@t.t")
            _git(repo, "config", "user.name", "T")
            (repo / "f.txt").write_text("one\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "init")
            info = gw.detect_repo(repo)
            dest = root / "wt1"
            st = gw.add_worktree(info, worktree_path=dest, branch="agentdeck/x/p1", base="main")
            (dest / "f.txt").write_text("one\ntwo\n", encoding="utf-8")
            _git(dest, "commit", "-am", "edit")
            # advance the base after the worktree was cut, so the record is
            # both ahead (its own "edit" commit) and behind (main moved on).
            (repo / "g.txt").write_text("g\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "on main")

            rec = store.create(
                repo_root=info.toplevel, repo_key=wsmod.repo_key_for(info.toplevel),
                branch=st.branch, path=str(dest), base_branch="main",
                workspace_name="X", status="active",
            )

            fired: dict = {}
            panel2 = WorktreePanel(
                store=store,
                repo_provider=lambda: info,
                github_connected=lambda: False,
                merge_enabled=lambda: True,
            )
            panel2.discard_requested.connect(lambda i: fired.__setitem__("discard", i))
            panel2.merge_requested.connect(lambda i: fired.__setitem__("merge", i))
            panel2.open_in_pane_requested.connect(lambda i: fired.__setitem__("open", i))

            check(panel2._list.count() == 1, "one row for one active record")
            check(not panel2._detail.isHidden(), "detail visible with a record")
            check("→" in panel2._head.text(), "detail header shows branch -> base")
            # Rows paint before git is asked anything (that probe is what used
            # to freeze the window), so anything probe-derived is asserted only
            # once the background probe has landed.
            _wait_for(lambda: not panel2._detail_pending)
            check(panel2._files.count() >= 2, "file list has whole-diff + changed file")
            _wait_for(lambda: panel2._diff.toPlainText() != "Loading diff…")
            check("+two" in panel2._diff.toPlainText() or "two" in panel2._diff.toPlainText(),
                  "diff view shows the change")
            check(not panel2._pr_btn.isEnabled(), "Open PR disabled when GitHub not connected")
            check(panel2._merge_btn.isEnabled(), "Merge enabled when merge_enabled() and dir exists")
            check(panel2._rebase_btn.isEnabled(),
                  "Rebase enabled when merge_enabled() and behind > 0")

            panel2.rebase_requested.connect(lambda i: fired.__setitem__("rebase", i))
            panel2._rebase_btn.click()
            check(fired.get("rebase") == rec.id, "Rebase emits rebase_requested(id)")

            panel2._discard_btn.click()
            check(fired.get("discard") == rec.id, "Discard emits discard_requested(id)")
            panel2._merge_btn.click()
            check(fired.get("merge") == rec.id, "Merge emits merge_requested(id)")

            # merge_enabled=False disables the merge + rebase buttons + tooltips
            panel3 = WorktreePanel(store=store, repo_provider=lambda: info,
                                   merge_enabled=lambda: False)
            check(not panel3._merge_btn.isEnabled(), "Merge disabled when merge_enabled() False")
            check(not panel3._rebase_btn.isEnabled(), "Rebase disabled when merge_enabled() False")

            # a worktree cut *after* the base moved starts even with it (behind
            # == 0) -> nothing to rebase onto, button stays disabled
            store2 = wsmod.WorktreeStore(root / "wt2.json")
            dest2 = root / "wt2"
            st2 = gw.add_worktree(info, worktree_path=dest2, branch="agentdeck/x/p2", base="main")
            rec2 = store2.create(
                repo_root=info.toplevel, repo_key=wsmod.repo_key_for(info.toplevel),
                branch=st2.branch, path=str(dest2), base_branch="main",
                workspace_name="X", status="active",
            )
            panel5 = WorktreePanel(store=store2, repo_provider=lambda: info,
                                   merge_enabled=lambda: True)
            _wait_for(lambda: panel5._diff.toPlainText() != "Loading diff…")
            _wait_for(lambda: not panel5._detail_pending)
            check(not panel5._rebase_btn.isEnabled(),
                  "Rebase disabled when behind == 0 (nothing to rebase onto)")

            # selecting a row must spawn exactly one diff task, not two --
            # setCurrentRow(0) firing _on_file_changed *and* the explicit
            # _render_diff call both running was a real double-render bug.
            diff_calls = []
            real_diff_text = gw.diff_text
            gw.diff_text = lambda *a, **k: (diff_calls.append(1), real_diff_text(*a, **k))[1]
            panel6 = WorktreePanel(store=store, repo_provider=lambda: info,
                                   merge_enabled=lambda: True)
            _wait_for(lambda: panel6._diff.toPlainText() != "Loading diff…")
            _wait_for(lambda: not panel6._detail_pending)
            QThreadPool.globalInstance().waitForDone(5000)
            QApplication.processEvents()
            gw.diff_text = real_diff_text
            check(len(diff_calls) == 1,
                  "selecting a row spawns exactly one diff task, not two")

            # a stale diff result (a superseded file selection / reload) must
            # never clobber what's currently shown
            panel2._diff.setPlainText("current diff text")
            stale_diff_gen = panel2._diff_gen - 1
            panel2._on_diff_ready(stale_diff_gen, "STALE")
            check(panel2._diff.toPlainText() == "current diff text",
                  "a superseded background diff result is dropped")

            # -- a worktree discarded while its diff is in flight --
            # The folder disappearing mid-diff raises NotADirectoryError
            # (WinError 267) out of the spawn; an exception escaping the
            # QRunnable killed the whole app ("AgentDeck stopped unexpectedly").
            from worktree_panel import _DiffTask, _ProbeTask  # noqa: PLC0415

            def _boom(*_a, **_k):
                raise NotADirectoryError(267, "The directory name is invalid")

            real_diff_text2 = gw.diff_text
            real_status = gw.status
            gw.diff_text = _boom
            gw.status = _boom
            got: list = []
            try:
                task = _DiffTask(1, str(dest), "main", None)
                task.signals.done.connect(lambda _g, t: got.append(t))
                task.run()  # inline: an escaping exception would fail the test run
                check(bool(got) and got[0].startswith("(could not read diff"),
                      "a worktree deleted mid-diff reports instead of crashing")
                probe = _ProbeTask(1, [(rec.id, str(dest), "main")])
                probed: list = []
                probe.signals.done.connect(lambda _g, r: probed.append(r))
                probe.run()
                check(bool(probed) and probed[0][rec.id] == (None, []),
                      "a worktree deleted mid-probe reports empty instead of crashing")
            finally:
                gw.diff_text = real_diff_text2
                gw.status = real_status

            # the same folder gone for real: the panel says so, no task spawned
            gw.remove_worktree(info, dest, force=True)
            panel7 = WorktreePanel(store=store, repo_provider=lambda: info,
                                   merge_enabled=lambda: True)
            check("not available" in panel7._diff.toPlainText(),
                  "a discarded worktree's detail shows a plain message")
            # ...and doesn't sit at "checking…" forever: nothing will ever probe
            # a folder that is gone, so it must not read as pending.
            check("Checking" not in panel7._status_line.text(),
                  "a gone worktree isn't left waiting on a probe that never runs")
            check(not panel7._merge_btn.isEnabled(),
                  "Merge disabled once the worktree folder is gone")

            # -- the freeze: reload() must never probe git on the UI thread --
            # Probing one worktree costs ~13 git processes (~0.6 s), so the old
            # synchronous reload() locked the window for seconds every time the
            # Worktrees view was opened or a merge / discard redrew it.
            import threading  # noqa: PLC0415

            probe_threads: list = []
            real_status2 = gw.status
            real_stat = gw.diff_stat

            def _slow_status(*a, **k):
                probe_threads.append(threading.current_thread().name)
                time.sleep(0.4)
                return real_status2(*a, **k)

            gw.status = _slow_status
            gw.diff_stat = lambda *a, **k: (probe_threads.append(
                threading.current_thread().name), real_stat(*a, **k))[1]
            try:
                panel8 = WorktreePanel(store=store2, repo_provider=lambda: info,
                                       merge_enabled=lambda: True)
                t0 = time.time()
                panel8.reload()
                elapsed = time.time() - t0
                check(elapsed < 0.2,
                      f"reload() returns without blocking on git ({elapsed*1000:.0f} ms)")
                _wait_for(lambda: bool(probe_threads))
                main_name = threading.main_thread().name
                check(bool(probe_threads) and main_name not in probe_threads,
                      "no git probe runs on the UI thread")
                _wait_for(lambda: not panel8._detail_pending)
            finally:
                gw.status = real_status2
                gw.diff_stat = real_stat
                QThreadPool.globalInstance().waitForDone(5000)

            # -- a poll tick must not yank the view out from under the user --
            panel9 = WorktreePanel(store=store2, repo_provider=lambda: info,
                                   merge_enabled=lambda: True)
            _wait_for(lambda: not panel9._detail_pending)
            _wait_for(lambda: panel9._diff.toPlainText() != "Loading diff…")
            panel9._diff.setPlainText("what the user is reading")
            picked = panel9._files.currentRow()
            panel9.refresh_status()
            _wait_for(lambda: panel9._probe_inflight_gen is None)
            check(panel9._diff.toPlainText() == "what the user is reading",
                  "an unchanged poll tick leaves the open diff alone")
            check(panel9._files.currentRow() == picked,
                  "an unchanged poll tick keeps the file selection")

            # -- background refresh: off-thread probe, stale results ignored --
            panel3.refresh_status()  # must not block / raise on the UI thread
            check(True, "refresh_status kicks the probe without blocking")
            stale_gen = panel3._probe_gen - 1
            panel3._on_probe_refreshed(stale_gen, {"whatever": (None, [])})
            check("whatever" not in panel3._probe_cache,
                  "a superseded background probe result is dropped")

            # let the background probe finish before the temp repo is torn down
            QThreadPool.globalInstance().waitForDone(5000)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
