"""Offline tests for the conversation-handoff dialog. Run:

    .venv\\Scripts\\python.exe test_handoff_dialog.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import handoff_dialog as hd
from agents import PLAIN_KEY, known_agents
from handoff_dialog import HandoffDialog

_ALL = known_agents()


def _fake_all(installed):
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


_real_all = hd.all_agents
hd.all_agents = _fake_all(["claude", "codex", "opencode"])

try:
    # ---------------------------------------------------------------------
    print("[1] population + defaults")
    d = HandoffDialog(source_key="claude", source_dir=r"C:\proj")
    tkeys = [d._target.itemData(i) for i in range(d._target.count())]
    skeys = [d._source.itemData(i) for i in range(d._source.count())]
    check("every known agent is a target", all(k in tkeys for k, _l, _c in _ALL))
    check("source list has Plain shell", PLAIN_KEY in skeys)
    check("source preselected to detected agent", d._source.currentData() == "claude")
    check("target defaults to the same agent", d._target.currentData() == "claude")
    check("source folder prefilled", d._folder.text() == r"C:\proj")

    # ---------------------------------------------------------------------
    print("[2] mode note flips resume <-> transcript")
    check("same-agent claude -> resume note",
          "Resumes" in d._note.text() and not d._fork.isHidden())
    check("thinking checkbox hidden in resume mode", d._thinking.isHidden())
    ci = d._target.findData("codex")
    d._target.setCurrentIndex(ci)
    check("claude -> codex shows transcript note", "Exports the conversation" in d._note.text())
    check("fork hidden for cross-agent", d._fork.isHidden())
    check("thinking shown for cross-agent", not d._thinking.isHidden())

    # ---------------------------------------------------------------------
    print("[3] not-installed target disables Hand off")
    d2 = HandoffDialog(source_key="claude")
    gi = d2._target.findData("gemini")  # not in the fake installed set
    d2._target.setCurrentIndex(gi)
    check("Hand off disabled when target missing", not d2._go.isEnabled())
    check("install hint shown", d2._hint is not None and not d2._hint.isHidden())

    # ---------------------------------------------------------------------
    print("[4] result_choice shape")
    d3 = HandoffDialog(source_key="claude", source_dir=r"C:\proj")
    d3._target.setCurrentIndex(d3._target.findData("opencode"))
    d3._any_cwd.setChecked(True)
    d3._accept()
    r = d3.result_choice()
    check("has all keys", set(r) == {"source_key", "source_dir", "target_key",
          "target_command", "fork", "include_thinking", "any_cwd"})
    check("source_key", r["source_key"] == "claude")
    check("target_key", r["target_key"] == "opencode")
    check("any_cwd carried", r["any_cwd"] is True)

    d4 = HandoffDialog(source_key=PLAIN_KEY)
    d4._accept()
    check("plain-shell source normalised to ''", d4.result_choice()["source_key"] == "")
finally:
    hd.all_agents = _real_all

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
