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
print("[5] agent cards: every known agent + Plain + Custom")
import setup_wizard as _sw
from agents import known_agents

w = fresh()
keys = [c.key for c in w._agent_cards]
check("a card for every known agent",
      all(k in keys for k, _l, _c in known_agents()))
check("Plain shell present", PLAIN_KEY in keys)
check("Custom present and last", keys[-1] == CUSTOM_KEY)
check("no duplicate keys", len(keys) == len(set(keys)))
check("card count = agents + plain + custom",
      len(keys) == len(known_agents()) + 2)

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
print("[5b] a not-installed agent: install steps inline, Launch blocked")
_real_all = _sw.all_agents
try:
    # pretend nothing is installed
    _sw.all_agents = lambda: [(k, lbl, cmd, False) for k, lbl, cmd in known_agents()]
    w = SetupWizard({"working_folder": HERE, "default_count": 3})
    w._goto(2)
    w._folder_edit.setText(HERE)

    first = known_agents()[0][0]
    w._select_agent(first)
    check("not-installed agent -> Launch disabled", not w._next_btn.isEnabled())
    check("note explains it's not installed",
          "isn't installed" in w._agent_note.text())

    card = next(c for c in w._agent_cards if c.key == first)
    check("the card carries a pill", card._pill.text() == "not installed")
    check("selecting it built the InstallHint", card._hint is not None)
    check("the hint has a command", bool(card._hint._cmd.text()))
    check("the hint has a docs url", card._hint._docs.startswith("http"))
    card._hint._do_copy()
    check("Copy puts the install command on the clipboard",
          QApplication.clipboard().text() == card._hint._cmd.text())

    # Plain shell is always launchable
    w._select_agent(PLAIN_KEY)
    check("plain shell still launchable", w._next_btn.isEnabled())

    # Re-check that succeeds re-enables Launch
    w._select_agent(first)
    card = next(c for c in w._agent_cards if c.key == first)
    card._on_rechecked(True)
    check("a successful Re-check re-enables Launch", w._next_btn.isEnabled())
finally:
    _sw.all_agents = _real_all


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
