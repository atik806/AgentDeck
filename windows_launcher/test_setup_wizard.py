"""Offline tests for the setup wizard. Run:

    .venv\\Scripts\\python.exe test_setup_wizard.py
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

import setup_wizard
from agents import CUSTOM_KEY, PLAIN_KEY
from setup_wizard import SetupWizard
from workspace import grid_dims

app = QApplication(sys.argv)

HERE = str(Path(__file__).resolve().parent)      # a folder that definitely exists
BOGUS = str(Path(__file__).resolve().parent / "does-not-exist-xyz")

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


def fresh(cfg=None):
    return SetupWizard(cfg or {"working_folder": HERE, "default_count": 4})


# ---------------------------------------------------------------------------
print("[1] structure + step indicator")
w = fresh()
check("three pages", w._stack.count() == 3)
check("starts on page 0", w._stack.currentIndex() == 0)
check("step indicator follows", w._steps._current == 0)
w._goto(2)
check("goto moves stack + indicator",
      w._stack.currentIndex() == 2 and w._steps._current == 2)
w._goto(0)
check("back button hidden on page 0", w._back_btn.isHidden())
check("skip button shown on page 0", not w._skip_btn.isHidden())
w._goto(2)
check("primary button says Launch on the last page",
      w._next_btn.text() == "Launch")
check("back button shown past page 0", not w._back_btn.isHidden())
check("skip hidden past page 0", w._skip_btn.isHidden())


# ---------------------------------------------------------------------------
print("[2] working folder validation gates Continue")
w = fresh()
w._goto(1)
w._folder_edit.setText(HERE)
check("valid folder -> Continue enabled",
      w._folder_is_valid() and w._next_btn.isEnabled())
w._folder_edit.setText(BOGUS)
check("bogus folder -> Continue disabled",
      not w._folder_is_valid() and not w._next_btn.isEnabled())
w._folder_edit.setText("")
check("empty folder -> disabled", not w._next_btn.isEnabled())


# ---------------------------------------------------------------------------
print("[3] Browse fills the field")
w = fresh()
_real = QFileDialog.getExistingDirectory
try:
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: HERE)
    w._goto(1)
    w._folder_edit.setText("")
    w._browse()
    check("browse populated the folder field", w._folder_edit.text() == HERE)
finally:
    QFileDialog.getExistingDirectory = _real


# ---------------------------------------------------------------------------
print("[4] count tiles + badge match the real grid")
w = fresh()
w._goto(1)
for c in (1, 2, 6, 8, 12):
    w._select_count(c)
    rows, cols = grid_dims(c)
    sel = [t.count for t in w._tiles if t._selected]
    check(f"count {c}: exactly that tile selected", sel == [c])
    check(f"count {c}: badge reads {cols}x{rows}",
          f"{cols}×{rows} grid" in w._count_badge.text())


# ---------------------------------------------------------------------------
print("[5] agent cards: detected + Plain + Custom")
w = fresh()
keys = [c.key for c in w._agent_cards]
check("Plain shell present", PLAIN_KEY in keys)
check("Custom present and last", keys[-1] == CUSTOM_KEY)
check("no duplicate keys", len(keys) == len(set(keys)))

w._goto(2)
w._folder_edit.setText(HERE)          # keep folder valid
w._select_agent(PLAIN_KEY)
check("plain shell -> Launch enabled", w._next_btn.isEnabled())
check("note mentions plain shells", "plain shell" in w._agent_note.text().lower())

w._select_agent(CUSTOM_KEY)
check("custom + empty field -> Launch disabled", not w._next_btn.isEnabled())
custom_card = next(c for c in w._agent_cards if c.key == CUSTOM_KEY)
check("custom field is revealed when selected", not custom_card.field.isHidden())
w._select_agent(PLAIN_KEY)
check("custom field is hidden again when deselected", custom_card.field.isHidden())
w._select_agent(CUSTOM_KEY)
custom_card.field.setText("claude --resume")
check("custom + text -> Launch enabled", w._next_btn.isEnabled())
check("note shows the command", "claude --resume" in w._agent_note.text())


# ---------------------------------------------------------------------------
print("[5b] install guide when an agent isn't installed")
import setup_wizard as _sw
from agents import known_agents

_real_avail = _sw.available_agents
try:
    _sw.available_agents = lambda: []          # pretend nothing is installed
    w = SetupWizard({"working_folder": HERE, "default_count": 3})
    keys = [c.key for c in w._agent_cards]
    check("with nothing installed, only Plain shell + Custom are selectable",
          keys == [PLAIN_KEY, CUSTOM_KEY])

    rows = [w._agent_box.itemAt(i).widget() for i in range(w._agent_box.count())]
    irows = [r for r in rows if isinstance(r, _sw._InstallRow)]
    check("one install row per known agent",
          len(irows) == len(known_agents()))
    heads = [r for r in rows if r is not None and r.objectName() == "installHead"]
    check("an explanatory heading is shown",
          heads and "don't have" in heads[0].text().lower())

    r0 = irows[0]
    check("install row carries a command", bool(r0._cmd.text()))
    check("install row has a docs url", r0._docs.startswith("http"))
    r0._copy()
    check("Copy puts the command on the clipboard",
          QApplication.clipboard().text() == r0._cmd.text())

    # can still launch -- it just falls back to a plain shell
    w._goto(2)
    w._folder_edit.setText(HERE)
    w._select_agent(PLAIN_KEY)
    check("Launch is still available (plain shell)", w._next_btn.isEnabled())

    # a partly-installed machine -> "Other agents" heading instead
    _sw.available_agents = lambda: [("claude", "Claude Code", "claude")]
    w2 = SetupWizard({"working_folder": HERE})
    rows2 = [w2._agent_box.itemAt(i).widget() for i in range(w2._agent_box.count())]
    heads2 = [r for r in rows2 if r is not None and r.objectName() == "installHead"]
    irows2 = [r for r in rows2 if isinstance(r, _sw._InstallRow)]
    check("claude selectable, the rest are install rows",
          "claude" in [c.key for c in w2._agent_cards]
          and len(irows2) == len(known_agents()) - 1)
    check("heading switches to 'other agents'",
          heads2 and "other agents" in heads2[0].text().lower())
finally:
    _sw.available_agents = _real_avail


# ---------------------------------------------------------------------------
print("[6] Launch produces a choices dict and Accepts")
w = fresh()
w._goto(1)
w._folder_edit.setText(HERE)
w._select_count(6)
w._goto(2)
w._select_agent(CUSTOM_KEY)
next(c for c in w._agent_cards if c.key == CUSTOM_KEY).field.setText("opencode")
done = []
w.accepted.connect(lambda: done.append(1))
w._launch()
ch = w.choices()
check("dialog accepted", done == [1])
check("folder carried", ch["folder"] == HERE)
check("count carried", ch["count"] == 6)
check("agent key + resolved command carried",
      ch["agent_key"] == CUSTOM_KEY and ch["agent_command"] == "opencode")


# ---------------------------------------------------------------------------
print("[7] Skip uses saved config and accepts")
cfg = {"working_folder": HERE, "default_count": 3,
       "agent": "custom", "agent_command": "aider"}
w = SetupWizard(cfg)
acc = []
w.accepted.connect(lambda: acc.append(1))
w._skip()
ch = w.choices()
check("skip accepted", acc == [1])
check("skip took the saved folder", ch["folder"] == HERE)
check("skip took the saved agent",
      ch["agent_key"] == "custom" and ch["agent_command"] == "aider")


# ---------------------------------------------------------------------------
print("[8] recent-folder quick launch")
w = SetupWizard({"working_folder": HERE, "recent_folders": [HERE, str(Path.home())]})
acc = []
w.accepted.connect(lambda: acc.append(1))
w._quick_launch(str(Path.home()))
check("quick launch accepted", acc == [1])
check("quick launch used that folder",
      w.choices()["folder"] == str(Path.home()))


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
