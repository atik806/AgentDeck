"""Offline tests for the "new workspace" dialog. Run:

    .venv\\Scripts\\python.exe test_new_workspace_dialog.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import new_workspace_dialog as nwd
from agents import CUSTOM_KEY, PLAIN_KEY
from new_workspace_dialog import NewWorkspaceDialog
from workspace import MAX_PANES

app = QApplication(sys.argv)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


# ---------------------------------------------------------------------------
print("[1] agent list: installed + Plain + Custom, Custom last")
_real_avail = nwd.available_agents
try:
    nwd.available_agents = lambda: [("claude", "Claude Code", "claude")]
    d = NewWorkspaceDialog()
    keys = [d._combo.itemData(i) for i in range(d._combo.count())]
    check("claude offered", "claude" in keys)
    check("plain shell present", PLAIN_KEY in keys)
    check("custom present and last", keys[-1] == CUSTOM_KEY)
    check("no duplicate keys", len(keys) == len(set(keys)))
finally:
    nwd.available_agents = _real_avail


# ---------------------------------------------------------------------------
print("[2] defaults are honoured")
_real_avail = nwd.available_agents
try:
    nwd.available_agents = lambda: [("claude", "Claude Code", "claude")]
    d = NewWorkspaceDialog(default_agent="claude", default_count=6)
    check("default agent selected", d._agent_key() == "claude")
    check("default count selected", d._count.value() == 6)

    d2 = NewWorkspaceDialog(default_agent="not-installed", default_count=99)
    check("unknown agent falls back to plain shell", d2._agent_key() == PLAIN_KEY)
    check("count clamped to the pane limit", d2._count.value() == MAX_PANES)
finally:
    nwd.available_agents = _real_avail


# ---------------------------------------------------------------------------
print("[3] custom command gates Create and drives the command")
d = NewWorkspaceDialog(default_agent=CUSTOM_KEY)
check("custom field revealed when Custom is selected", d._custom.isVisibleTo(d))
check("empty custom -> Create disabled", not d._create.isEnabled())
d._custom.setText("aider --model sonnet")
check("filled custom -> Create enabled", d._create.isEnabled())
check("preview shows the command", "aider --model sonnet" in d._note.text())

d._combo.setCurrentIndex(d._combo.findData(PLAIN_KEY))
check("custom field hidden again for plain shell", not d._custom.isVisibleTo(d))
check("plain shell -> Create enabled", d._create.isEnabled())
check("preview mentions plain shells", "plain shell" in d._note.text().lower())


# ---------------------------------------------------------------------------
print("[4] result dict + accept")
d = NewWorkspaceDialog(default_agent=CUSTOM_KEY)
d._custom.setText("codex --full-auto")
d._count.setValue(4)
accepted = []
d.accepted.connect(lambda: accepted.append(1))
d._accept()
r = d.result_choice()
check("dialog accepted", accepted == [1])
check("agent key carried", r["agent_key"] == CUSTOM_KEY)
check("custom text carried", r["agent_custom"] == "codex --full-auto")
check("resolved command carried", r["agent_command"] == "codex --full-auto")
check("count carried", r["count"] == 4)

d = NewWorkspaceDialog()
check("no result before accept", d.result_choice() is None)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
