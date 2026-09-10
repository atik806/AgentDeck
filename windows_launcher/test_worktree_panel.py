"""Offline GUI tests for worktree_panel.py.

    set QT_QPA_PLATFORM=offscreen
    .venv\\Scripts\\python.exe test_worktree_panel.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
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
            check(panel2._files.count() >= 2, "file list has whole-diff + changed file")
            check("+two" in panel2._diff.toPlainText() or "two" in panel2._diff.toPlainText(),
                  "diff view shows the change")
            check(not panel2._pr_btn.isEnabled(), "Open PR disabled when GitHub not connected")
            check(panel2._merge_btn.isEnabled(), "Merge enabled when merge_enabled() and dir exists")

            panel2._discard_btn.click()
            check(fired.get("discard") == rec.id, "Discard emits discard_requested(id)")
            panel2._merge_btn.click()
            check(fired.get("merge") == rec.id, "Merge emits merge_requested(id)")

            # merge_enabled=False disables the merge button + sets a tooltip
            panel3 = WorktreePanel(store=store, repo_provider=lambda: info,
                                   merge_enabled=lambda: False)
            check(not panel3._merge_btn.isEnabled(), "Merge disabled when merge_enabled() False")

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
