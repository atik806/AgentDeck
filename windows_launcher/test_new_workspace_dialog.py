"""Offline tests for the "new workspace" dialog. Run:

    .venv\\Scripts\\python.exe test_new_workspace_dialog.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import new_workspace_dialog as nwd
from agents import CUSTOM_KEY, PLAIN_KEY, known_agents
from new_workspace_dialog import NewWorkspaceDialog
from workspace import MAX_PANES

_ALL = known_agents()


def _fake_all(installed):
    """A stand-in for agents.all_agents() with `installed` marked present."""
    inst = set(installed)
    return lambda: [(k, lbl, cmd, k in inst) for k, lbl, cmd in _ALL]

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


_real_all = nwd.all_agents


# ---------------------------------------------------------------------------
print("[1] agent list: every known agent + Plain + Custom, Custom last")
try:
    nwd.all_agents = _fake_all(["claude"])
    d = NewWorkspaceDialog()
    keys = [d._combo.itemData(i) for i in range(d._combo.count())]
    check("every known agent offered", all(k in keys for k, _l, _c in _ALL))
    check("plain shell present", PLAIN_KEY in keys)
    check("custom present and last", keys[-1] == CUSTOM_KEY)
    check("no duplicate keys", len(keys) == len(set(keys)))
    labels = [d._combo.itemText(i) for i in range(d._combo.count())]
    check("not-installed agents are marked",
          any("not installed" in t for t in labels))
finally:
    nwd.all_agents = _real_all


# ---------------------------------------------------------------------------
print("[2] defaults are honoured")
try:
    nwd.all_agents = _fake_all(["claude"])
    d = NewWorkspaceDialog(default_agent="claude", default_count=6)
    check("default agent selected", d._agent_key() == "claude")
    check("default count selected", d._count.value() == 6)

    d2 = NewWorkspaceDialog(default_agent="not-a-key", default_count=99)
    check("unknown agent falls back to plain shell", d2._agent_key() == PLAIN_KEY)
    check("count clamped to the pane limit", d2._count.value() == MAX_PANES)
finally:
    nwd.all_agents = _real_all


# ---------------------------------------------------------------------------
print("[1b] a not-installed agent shows install steps and blocks Create")
try:
    nwd.all_agents = _fake_all(["claude"])
    d = NewWorkspaceDialog(default_agent="claude")
    missing = next(k for k, _l, _c in _ALL if k != "claude")
    d._combo.setCurrentIndex(d._combo.findData(missing))
    check("Create disabled for a not-installed agent", not d._create.isEnabled())
    check("InstallHint panel appears", d._hint is not None and d._hint.isVisibleTo(d))
    check("note explains it", "isn't installed" in d._note.text())
    d._on_rechecked(True)
    check("a successful Re-check re-enables Create", d._create.isEnabled())
    d._combo.setCurrentIndex(d._combo.findData("claude"))
    check("switching to an installed agent hides the hint",
          not d._hint.isVisibleTo(d))
finally:
    nwd.all_agents = _real_all


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
