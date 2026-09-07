"""Offline widget tests for the SKILLS panel and its sidebar nav button.

No window, no shells. Run:

    .venv\\Scripts\\python.exe test_skills_panel.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from skills_panel import SkillsPanel, skill_icon
from skills_store import SkillsStore
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
    return SkillsStore(path=Path(tempfile.mkdtemp()) / "skills.json")


# ---------------------------------------------------------------------------
print("[1] skill_icon / empty panel")
check("skill_icon draws something", not skill_icon(16).isNull())

panel = SkillsPanel(store=fresh_store())
panel.resize(940, 560)
panel.grab()  # must not raise
check("panel paints without raising", True)
check("empty state shown, editor hidden",
      not panel._empty.isHidden() and panel._editor.isHidden())


# ---------------------------------------------------------------------------
print("[2] new / edit / autosave-flush / persist / reload")
store = fresh_store()
seen = {"changed": 0, "count": None}
panel = SkillsPanel(store=store, config={})
panel.changed.connect(lambda: seen.__setitem__("changed", seen["changed"] + 1))
panel.count_changed.connect(lambda n: seen.__setitem__("count", n))
panel._on_new()
check("New adds a row", panel._list.count() == 1)
check("editor visible", not panel._editor.isHidden() and panel._empty.isHidden())
check("new skill starts disabled", panel._enabled_box.isChecked() is False)

panel._name_edit.setText("Deploy Runbook")
panel._name_edit.textEdited.emit("Deploy Runbook")
panel._desc_edit.setText("Use when deploying to production")
panel._desc_edit.textEdited.emit("x")
panel._body_edit.setPlainText("1. bump version\n2. push tag")
panel._enabled_box.setChecked(True)
panel.flush()

sk = store.all()[0]
check("name saved", sk.name == "Deploy Runbook")
check("description saved", sk.description == "Use when deploying to production")
check("body saved", "push tag" in sk.body)
check("enabled saved", sk.enabled is True)
check("changed signal fired", seen["changed"] >= 1)
check("persists across reload", SkillsStore(path=store.path).all()[0].body == sk.body)


# ---------------------------------------------------------------------------
print("[3] upload SKILL.md")
store = fresh_store()
panel = SkillsPanel(store=store, config={})
md = Path(tempfile.mkdtemp()) / "SKILL.md"
md.write_text(
    "---\nname: Imported One\ndescription: from a file\n---\nDo the imported thing.",
    encoding="utf-8",
)
# Drive the importer directly (QFileDialog is modal / not offscreen-friendly).
with open(md, "r", encoding="utf-8") as fh:
    store.import_markdown(fh.read(), fallback_name="SKILL")
panel.reload()
check("row added from upload", panel._list.count() == 1)
check("uploaded name", store.all()[0].name == "Imported One")
check("uploaded source", store.all()[0].source == "upload")


# ---------------------------------------------------------------------------
print("[4] improve_requested carries the current id, remembers agent")
store = fresh_store()
cfg = {}
panel = SkillsPanel(store=store, config=cfg)
got = []
panel.improve_requested.connect(got.append)
panel._on_new()
sid = panel._current_id
# Only fires when an agent is installed; skip the emit assertion if none are.
if panel._installed_agents:
    panel._improve_combo.setCurrentIndex(0)
    panel._on_improve()
    check("improve_requested emitted the skill id", got == [sid])
    check("picked agent remembered in config",
          cfg.get("skills_improve_agent") == panel._improve_combo.currentData())
else:
    check("improve button disabled with no agent", not panel._improve_btn.isEnabled())


# ---------------------------------------------------------------------------
print("[5] delete")
store = fresh_store()
panel = SkillsPanel(store=store, config={})
panel._on_new()
panel._on_new()
check("two rows", panel._list.count() == 2)
panel._on_delete()
check("one left after delete", len(store) == 1)


# ---------------------------------------------------------------------------
print("[6] sidebar has a Skills nav item")
sb = WorkspaceSidebar()
fired = []
sb.skills_selected.connect(lambda: fired.append(True))
sb._skills_btn.click()
check("skills_selected emitted", fired == [True])
sb.set_skills_active(True)
check("nav button reflects active", sb._skills_btn.isChecked())
sb.set_skills_active(False)
check("nav button reflects inactive", not sb._skills_btn.isChecked())


# ---------------------------------------------------------------------------
print("[7] reload_current_from_store picks up an out-of-band edit")
store = fresh_store()
panel = SkillsPanel(store=store, config={})
panel._on_new()
sid = panel._current_id
store.update(sid, body="rewritten by an agent", source="agent")
panel.reload_current_from_store()
check("editor shows the new body", panel._body_edit.toPlainText() == "rewritten by an agent")
# ...but not when the user has an unsaved edit going.
panel._body_edit.setPlainText("user is typing")
panel._dirty = True
store.update(sid, body="agent again")
panel.reload_current_from_store()
check("unsaved local edit not clobbered", panel._body_edit.toPlainText() == "user is typing")


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
