"""workspaces_store -- snapshot round-trip + tolerance of junk. Offline, no Qt."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from workspaces_store import (
    SessionSnapshot,
    WorkspaceSnapshot,
    WorkspacesStore,
)

_passed = 0
_failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


tmp = Path(tempfile.mkdtemp())
store = WorkspacesStore(path=tmp / "workspaces.json")

print("[1] empty / missing file")
s = store.load()
check("missing file -> empty snapshot", s.is_usable() is False)
check("empty defaults: grid layout", s.layout == "grid")

print("[2] round-trip")
store.save(
    SessionSnapshot(
        folder=r"E:\code\app",
        layout="columns",
        active=1,
        workspaces=[
            WorkspaceSnapshot("API", 4, "claude", "claude"),
            WorkspaceSnapshot("UI", 6, "codex", "codex"),
        ],
    )
)
s = store.load()
check("two workspaces", len(s.workspaces) == 2)
check("folder kept", s.folder == r"E:\code\app")
check("layout kept", s.layout == "columns")
check("active kept", s.active == 1)
check("names kept", [w.name for w in s.workspaces] == ["API", "UI"])
check("pane counts kept", [w.panes for w in s.workspaces] == [4, 6])
check("agent command kept", s.workspaces[1].agent_command == "codex")
check("is_usable", s.is_usable() is True)

print("[3] clamping + junk tolerance")
(tmp / "workspaces.json").write_text(
    json.dumps(
        {
            "version": 1,
            "layout": "spiral",          # invalid
            "active": 99,                # out of range
            "workspaces": [
                {"name": "A", "panes": 999},          # clamp to 16
                {"name": "B", "panes": "lots"},       # -> default 4
                "not a dict",                          # skipped
            ],
        }
    ),
    encoding="utf-8",
)
s = store.load()
check("invalid layout -> grid", s.layout == "grid")
check("two valid workspaces (string dropped)", len(s.workspaces) == 2)
check("panes clamped high", s.workspaces[0].panes == 16)
check("bad panes -> default", s.workspaces[1].panes == 4)
check("active clamped into range", s.active == 1)

print("[4] corrupt file never raises")
(tmp / "workspaces.json").write_text("{ not json", encoding="utf-8")
check("garbage -> empty snapshot", store.load().is_usable() is False)

print("[5] clear()")
store.save(SessionSnapshot(workspaces=[WorkspaceSnapshot("X", 2)]))
check("saved before clear", store.load().is_usable() is True)
store.clear()
check("cleared", store.load().is_usable() is False)

print()
print(f"{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
