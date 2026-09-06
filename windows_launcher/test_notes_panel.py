"""Offline widget tests for the NOTES panel and its sidebar nav button.

No window, no shells. Run:

    .venv\\Scripts\\python.exe test_notes_panel.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from notes_panel import NotesPanel, note_icon
from notes_store import NotesStore
from workspace_sidebar import WorkspaceSidebar

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
    return NotesStore(path=Path(tempfile.mkdtemp()) / "notes.json")


# ---------------------------------------------------------------------------
print("[1] note_icon / empty panel")
check("note_icon draws something", not note_icon(16).isNull())

panel = NotesPanel(store=fresh_store())
panel.resize(800, 500)
panel.grab()  # must not raise
check("panel paints without raising", True)
check("empty state shown, editor hidden",
      not panel._empty.isHidden() and panel._editor.isHidden())


# ---------------------------------------------------------------------------
print("[2] create / edit / autosave-flush / persist")
store = fresh_store()
panel = NotesPanel(store=store)
panel._on_new()
check("New note adds a row", panel._list.count() == 1)
check("editor now visible", not panel._editor.isHidden() and panel._empty.isHidden())
check("count_changed wired", True)

panel._body_edit.setPlainText("Deploy steps\n1. bump version")
check("edit marks dirty", panel._dirty)
panel.flush()
check("flush clears dirty", not panel._dirty)
check("body persisted to store", store.all()[0].body.startswith("Deploy steps"))
check("row title reflects first line",
      panel._list.itemWidget(panel._list.item(0))._title.text() == "Deploy steps")

# reload from a second store instance -> survives "restart"
panel2 = NotesPanel(store=NotesStore(path=store.path))
check("reload shows the note", panel2._list.count() == 1)
check("body round-trips through reload",
      panel2._body_edit.toPlainText().startswith("Deploy steps"))


# ---------------------------------------------------------------------------
print("[3] multiple notes, selection switches editor, edits don't leak")
store = fresh_store()
panel = NotesPanel(store=store)
panel._on_new()
panel._body_edit.setPlainText("note A")
panel.flush()
panel._on_new()
panel._body_edit.setPlainText("note B")
panel.flush()
check("two rows", panel._list.count() == 2)

# select the other row
first_item = panel._list.item(1)
panel._list.setCurrentItem(first_item)
check("switching rows loads that note",
      panel._body_edit.toPlainText() in ("note A", "note B"))
loaded = panel._body_edit.toPlainText()
other = "note B" if loaded == "note A" else "note A"
panel._list.setCurrentItem(panel._list.item(0))
check("switching back loads the sibling", panel._body_edit.toPlainText() == other)
check("both notes intact after switching",
      {n.body for n in store.all()} == {"note A", "note B"})


# ---------------------------------------------------------------------------
print("[4] explicit title + delete")
store = fresh_store()
panel = NotesPanel(store=store)
panel._on_new()
panel._body_edit.setPlainText("body first line")
panel._title_edit.setText("Custom Title")
panel._title_edit.textEdited.emit("Custom Title")  # setText doesn't emit textEdited
panel.flush()
check("explicit title stored", store.all()[0].title == "Custom Title")
check("row shows the custom title",
      panel._list.itemWidget(panel._list.item(0))._title.text() == "Custom Title")

panel._on_delete()
check("note deleted from store", len(store) == 0)
check("panel back to empty state",
      not panel._empty.isHidden() and panel._editor.isHidden())

panel.apply_theme()  # must not raise
check("apply_theme survives", True)


# ---------------------------------------------------------------------------
print("[5] sidebar nav strip has Plugins + Notes")
sb = WorkspaceSidebar()

fired = []
sb.notes_selected.connect(lambda: fired.append(1))
sb._notes_btn.click()
check("clicking Notes emits notes_selected", fired == [1])

sb.set_notes_active(True)
check("nav button checks when notes active", sb._notes_btn.isChecked())
sb.set_notes_active(False)
check("nav button unchecks", not sb._notes_btn.isChecked())

# plugins and notes are independent toggles the panel drives
sb.set_plugins_active(True)
sb.set_notes_active(False)
check("plugins on, notes off", sb._plugins_btn.isChecked() and not sb._notes_btn.isChecked())


# ---------------------------------------------------------------------------
print("[6] search filters the list")
store = fresh_store()
panel = NotesPanel(store=store)
panel._on_new(); panel._body_edit.setPlainText("deploy checklist"); panel.flush()
panel._on_new(); panel._body_edit.setPlainText("grocery run"); panel.flush()
panel._search.setText("deploy")
hidden = [panel._list.item(i).isHidden() for i in range(panel._list.count())]
check("one row hidden by the filter", hidden.count(True) == 1)
check("selection followed the filter to the visible note",
      panel._body_edit.toPlainText() == "deploy checklist")
panel._search.clear()
check("clearing the search shows everything again",
      not any(panel._list.item(i).isHidden() for i in range(panel._list.count())))


# ---------------------------------------------------------------------------
print("[7] pin float + colour label")
store = fresh_store()
panel = NotesPanel(store=store)
panel._on_new(); panel._body_edit.setPlainText("first"); panel.flush()
first_id = panel._current_id
panel._on_new(); panel._body_edit.setPlainText("second"); panel.flush()
check("newest note is on top", panel._list.item(0).data(0x0100) == panel._current_id)

# select + pin the older note
panel._select_id(first_id)
panel._pin_btn.setChecked(True)
check("store recorded the pin", store.get(first_id).pinned is True)
check("pinned note moved to row 0", panel._list.item(0).data(0x0100) == first_id)
check("pin button relabelled", panel._pin_btn.text().strip() == "Pinned")

panel._on_color_pick("green")
check("colour stored", store.get(first_id).color == "green")
panel._on_color_pick("green")  # click again clears
check("re-picking the colour clears it", store.get(first_id).color == "")


# ---------------------------------------------------------------------------
print("[8] copy + send-to-terminal")
store = fresh_store()
panel = NotesPanel(store=store)
panel._on_new()
panel._body_edit.setPlainText("run the thing")
panel.flush()

sent = []
panel.send_to_terminal.connect(sent.append)
panel._on_send()
check("send_to_terminal carries the body", sent == ["run the thing"])

app.clipboard().clear()
panel._on_copy()
check("copy puts the body on the clipboard", app.clipboard().text() == "run the thing")


# ---------------------------------------------------------------------------
print("[9] duplicate")
store = fresh_store()
panel = NotesPanel(store=store)
panel._on_new()
panel._body_edit.setPlainText("template prompt")
panel._title_edit.setText("Prompt")
panel._title_edit.textEdited.emit("Prompt")
panel.flush()
panel._on_duplicate()
check("duplicate added a second note", len(store) == 2)
check("editor moved to the copy", panel._body_edit.toPlainText() == "template prompt")
check("copy title tagged", panel._title_edit.text() == "Prompt (copy)")


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
