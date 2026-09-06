"""Offline widget tests for the ROUTINES panel and its sidebar nav button.

No window, no shells, no real agent PATH probing (monkeypatched). Run:

    .venv\\Scripts\\python.exe test_routines_panel.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication

import routines_panel as rp
from agents import CUSTOM_KEY, PLAIN_KEY, known_agents
from routines_panel import RoutinesPanel, routine_icon
from routines_store import NEW_WORKSPACE, RoutinesStore
from workspace_sidebar import WorkspaceSidebar

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


def fresh_store():
    return RoutinesStore(path=Path(tempfile.mkdtemp()) / "routines.json")


# ---------------------------------------------------------------------------
print("[1] routine_icon / empty panel")
check("routine_icon draws something", not routine_icon(16).isNull())

panel = RoutinesPanel(store=fresh_store())
panel.resize(900, 560)
panel.grab()  # must not raise
check("panel paints without raising", True)
check("empty state shown, editor hidden",
      not panel._empty.isHidden() and panel._editor.isHidden())


# ---------------------------------------------------------------------------
print("[2] create / edit every field / autosave-flush / persist / reload")
store = fresh_store()
panel = RoutinesPanel(store=store, workspaces_provider=lambda: ["Workspace 1", "Dev"])
panel._on_new()
check("New routine adds a row", panel._list.count() == 1)
check("editor now visible", not panel._editor.isHidden() and panel._empty.isHidden())

panel._name_edit.setText("Morning check-in")
panel._name_edit.textEdited.emit("Morning check-in")  # setText alone doesn't emit
panel._prompt_edit.setPlainText("Check for new PRs and summarize them")
panel._time_edit.setTime(QTime(8, 30))
panel._day_btns[0].setChecked(True)  # Mon
panel._day_btns[2].setChecked(True)  # Wed
idx = panel._ws_combo.findData("Dev")
panel._ws_combo.setCurrentIndex(idx)
check("dirty after edits", panel._dirty)
panel.flush()
check("flush clears dirty", not panel._dirty)

saved = store.all()[0]
check("name persisted", saved.name == "Morning check-in")
check("prompt persisted", saved.prompt == "Check for new PRs and summarize them")
check("time persisted as 24h HH:MM", saved.time == "08:30")
check("days persisted (Mon=0, Wed=2)", saved.days == [0, 2])
check("workspace target persisted", saved.workspace_target == "Dev")
check("row schedule line reflects it",
      "Mon, Wed" in panel._list.itemWidget(panel._list.item(0))._schedule.text())

# reload from a second store instance -> survives "restart"
panel2 = RoutinesPanel(store=RoutinesStore(path=store.path), workspaces_provider=lambda: [])
check("reload shows the routine", panel2._list.count() == 1)
check("prompt round-trips through reload",
      panel2._prompt_edit.toPlainText() == "Check for new PRs and summarize them")
check("closed workspace target still shown (with a marker) rather than silently reset",
      panel2._ws_combo.currentData() == "Dev"
      and "closed" in panel2._ws_combo.currentText())


# ---------------------------------------------------------------------------
print("[3] agent picker: known agent / custom / install hint")
_real_all_agents = rp.all_agents
try:
    rp.all_agents = _fake_all(["claude"])  # only claude "installed"
    store = fresh_store()
    panel = RoutinesPanel(store=store)
    panel._on_new()

    idx = panel._agent_combo.findData("claude")
    panel._agent_combo.setCurrentIndex(idx)
    check("installed agent: no install hint shown",
          panel._hint is None or panel._hint.isHidden())
    check("custom field hidden for a known agent", panel._custom_edit.isHidden())

    idx = panel._agent_combo.findData("codex")  # in _fake_all(["claude"]) -> not installed
    panel._agent_combo.setCurrentIndex(idx)
    check("not-installed agent shows an install hint",
          panel._hint is not None and not panel._hint.isHidden())

    idx = panel._agent_combo.findData(CUSTOM_KEY)
    panel._agent_combo.setCurrentIndex(idx)
    check("custom field shown for Custom command", not panel._custom_edit.isHidden())
    panel._custom_edit.setText("aider --model sonnet")
    panel._custom_edit.textEdited.emit("aider --model sonnet")
    panel.flush()
    check("custom command persisted", store.all()[0].agent_custom == "aider --model sonnet")
    check("custom key persisted", store.all()[0].agent_key == CUSTOM_KEY)

    idx = panel._agent_combo.findData(PLAIN_KEY)
    panel._agent_combo.setCurrentIndex(idx)
    check("plain shell hides both custom field and hint",
          panel._custom_edit.isHidden() and (panel._hint is None or panel._hint.isHidden()))
finally:
    rp.all_agents = _real_all_agents


# ---------------------------------------------------------------------------
print("[4] enabled toggle reflected in row styling / schedule line")
store = fresh_store()
panel = RoutinesPanel(store=store)
panel._on_new()
panel._name_edit.setText("Nightly build")
panel._name_edit.textEdited.emit("Nightly build")
panel.flush()
check("enabled by default", store.all()[0].enabled)

panel._enabled_box.setChecked(False)
panel.flush()
check("disabled persisted", store.all()[0].enabled is False)
check("row shows 'disabled'",
      "disabled" in panel._list.itemWidget(panel._list.item(0))._schedule.text())


# ---------------------------------------------------------------------------
print("[5] multiple routines, selection switches editor, edits don't leak")
store = fresh_store()
panel = RoutinesPanel(store=store)
panel._on_new()
panel._prompt_edit.setPlainText("prompt A")
panel.flush()
panel._on_new()
panel._prompt_edit.setPlainText("prompt B")
panel.flush()
check("two rows", panel._list.count() == 2)

panel._list.setCurrentItem(panel._list.item(1))
loaded = panel._prompt_edit.toPlainText()
other = "prompt B" if loaded == "prompt A" else "prompt A"
panel._list.setCurrentItem(panel._list.item(0))
check("switching back loads the sibling", panel._prompt_edit.toPlainText() == other)
check("both routines intact after switching",
      {r.prompt for r in store.all()} == {"prompt A", "prompt B"})


# ---------------------------------------------------------------------------
print("[6] delete + refresh_list (used after a routine fires)")
store = fresh_store()
panel = RoutinesPanel(store=store)
panel._on_new()
panel._name_edit.setText("One-off")
panel._name_edit.textEdited.emit("One-off")
panel.flush()
rid = store.all()[0].id
store.mark_run(rid, "ok")
panel.refresh_list()  # must not raise, reflects the new last-run stamp
check("refresh_list survives and picks up last-run status",
      "ago" in panel._list.itemWidget(panel._list.item(0))._last.text()
      or panel._list.itemWidget(panel._list.item(0))._last.text() == "just now")

panel._on_delete()
check("routine deleted from store", len(store) == 0)
check("panel back to empty state",
      not panel._empty.isHidden() and panel._editor.isHidden())

panel.apply_theme()  # must not raise
check("apply_theme survives", True)


# ---------------------------------------------------------------------------
print("[7] sidebar nav strip has a Routines button after Notes")
sb = WorkspaceSidebar()

fired = []
sb.routines_selected.connect(lambda: fired.append(1))
sb._routines_btn.click()
check("clicking Routines emits routines_selected", fired == [1])

sb.set_routines_active(True)
check("nav button checks when routines active", sb._routines_btn.isChecked())
sb.set_routines_active(False)
check("nav button unchecks", not sb._routines_btn.isChecked())

sb.set_notes_active(True)
sb.set_routines_active(False)
check("notes on, routines off -- independent toggles",
      sb._notes_btn.isChecked() and not sb._routines_btn.isChecked())


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
