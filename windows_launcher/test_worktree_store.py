"""Offline tests for worktree_store.py.

    .venv\\Scripts\\python.exe test_worktree_store.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import worktree_store as ws

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


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store_path = root / "worktrees.json"

        # -- branch_name / slug / repo_key --
        b = ws.branch_name("API work!!", 0, when=datetime(2026, 9, 7, 14, 30))
        check(b.startswith("agentdeck/api-work/p1-260907-1430-"),
              f"branch_name shape (got {b})")
        check(ws.slugify("  Some / Weird __ Name  ") == "some-weird-name",
              "slugify collapses junk")
        # repo_key_for is built on os.path.normcase, which is deliberately
        # OS-dependent: Windows' filesystem is case-insensitive (so folding
        # case there prevents two spellings of the same repo getting
        # different worktree keys), Linux's is case-sensitive (so two
        # differently-cased paths really are two different repos, and
        # collapsing them would cause real worktree collisions). Assert
        # whichever behaviour is actually correct for the platform running
        # this test, not a single hardcoded expectation.
        if sys.platform == "win32":
            k1 = ws.repo_key_for("E:\\Code\\MyApp")
            k2 = ws.repo_key_for("e:/code/myapp")
            check(k1 == k2, "repo_key is case/-separator insensitive on Windows")
        else:
            k1 = ws.repo_key_for("/tmp/Code/MyApp")
            k2 = ws.repo_key_for("/tmp/code/myapp")
            check(k1 != k2, "repo_key is case-sensitive on a case-sensitive filesystem")
        check(len(k1) == 12, "repo_key is 12 hex chars")

        wt_dir = ws.worktree_dir(root, k1, "agentdeck/x/p1-abc")
        check(wt_dir.name == "agentdeck__x__p1-abc", "worktree_dir flattens slashes")

        # -- CRUD --
        store = ws.WorktreeStore(store_path)
        check(len(store) == 0, "fresh store is empty")
        rec = store.create(
            repo_root=str(root / "repo"), branch="agentdeck/x/p1",
            path=str(root / "wt1"), base_branch="main",
            workspace_name="X", agent_key="claude",
        )
        check(rec.id.startswith("wt_"), "create returns an id")
        check(rec.repo_key == ws.repo_key_for(str(root / "repo")),
              "create backfills repo_key")
        check(store_path.exists(), "create persisted the file")
        check(not (store_path.with_suffix(".json.tmp")).exists(),
              "atomic write left no .tmp")

        t0 = rec.updated
        updated = store.update(rec.id, pane_id="p_1234")
        check(updated.pane_id == "p_1234", "update set pane_id")
        check(updated.updated >= t0, "update bumped updated")
        check(store.update(rec.id, status="bogus").status != "bogus",
              "update rejects an unknown status")

        check(store.by_pane("p_1234") is not None, "by_pane finds it")
        check(store.by_path(str(root / "wt1")) is not None, "by_path finds it")
        check(len(store.for_repo(str(root / "repo"))) == 1, "for_repo finds it")

        store.set_status(rec.id, "merged")
        check(rec.id not in [r.id for r in store.active()],
              "merged record drops out of active()")

        r2 = store.create(repo_root=str(root / "repo"), branch="agentdeck/x/p2",
                          path=str(root / "wt2"), workspace_name="X")
        check(len(store.active()) == 1, "one active record")

        check(store.delete(r2.id), "delete returns True")
        check(store.get(r2.id) is None, "deleted record is gone")

        # -- tolerant load --
        store_path.write_text("{ this is not json", encoding="utf-8")
        s2 = ws.WorktreeStore(store_path)
        check(s2.all() == [], "corrupt file loads as empty, no raise")

        # -- reconcile --
        s3 = ws.WorktreeStore(root / "recon.json")
        gone = s3.create(repo_root=str(root / "repo"), branch="agentdeck/x/p3",
                         path=str(root / "does-not-exist"), workspace_name="X")
        (root / "live-wt").mkdir()
        live = s3.create(repo_root=str(root / "repo"), branch="agentdeck/x/p4",
                         path=str(root / "live-wt"), workspace_name="X")
        live_by_repo = {
            str(root / "repo"): [
                {"path": str(root / "live-wt"), "branch": "agentdeck/x/p4"},
                {"path": str(root / "stray-wt"), "branch": "agentdeck/x/stray"},
            ]
        }
        (root / "stray-wt").mkdir()
        # a repo that wasn't successfully scanned must not orphan its records
        # (nor adopt strays "seen" only in a failed scan)
        changed0 = s3.reconcile(live_by_repo, scanned=set())
        check(s3.get(gone.id).status == "active",
              "reconcile leaves records alone when their repo wasn't scanned")
        check(changed0 == [],
              "unscanned reconcile changes nothing")

        changed = s3.reconcile(live_by_repo, scanned={str(root / "repo")})
        check(s3.get(gone.id).status == "orphaned",
              "reconcile marks a vanished dir orphaned")
        check(s3.get(live.id).status == "active",
              "reconcile leaves a live registered worktree alone")
        check(any(r.workspace_name == "(imported)" for r in changed),
              "reconcile adopts a stray agentdeck/* worktree")

        # the vanished dir comes back -> recovered to detached, not left orphaned
        (root / "does-not-exist").mkdir()
        live_by_repo[str(root / "repo")].append(
            {"path": str(root / "does-not-exist"), "branch": "agentdeck/x/p3"}
        )
        s3.reconcile(live_by_repo, scanned={str(root / "repo")})
        check(s3.get(gone.id).status == "detached",
              "reconcile recovers an orphaned record that reappears")

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
