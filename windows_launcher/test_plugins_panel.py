"""Offline widget tests for the PLUGINS panel and the sidebar's nav strip.

No window, no shells. Run:

    .venv\\Scripts\\python.exe test_plugins_panel.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from plugins_panel import PluginsPanel, plugin_icon
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


class FakeWorkspace:
    def __init__(self, name, busy=False):
        self.name = name
        self.accent = "#3b78ff"
        self.pane_count = 2
        self._busy = busy

    def is_busy(self):
        return self._busy


# ---------------------------------------------------------------------------
print("[1] plugin_icon / PluginsPanel")
check("plugin_icon draws something", not plugin_icon(16).isNull())
panel = PluginsPanel()
panel.resize(600, 400)
panel.grab()  # must not raise
check("panel paints without raising", True)


# ---------------------------------------------------------------------------
print("[2] sidebar nav strip")
sb = WorkspaceSidebar()
ws = [FakeWorkspace("Workspace 1"), FakeWorkspace("Workspace 2")]
sb.refresh(ws, ws[0])

fired = []
sb.plugins_selected.connect(lambda: fired.append(1))
sb._plugins_btn.click()
check("clicking Plugins emits plugins_selected", fired == [1])

sb.set_plugins_active(True)
check("nav button checks when plugins active", sb._plugins_btn.isChecked())
sb.refresh(ws, None)
check("workspace rows still listed while plugins active",
      sb._list.count() - 1 == 2)
check("no row highlighted with active=None",
      all(sb._list.itemAt(i).widget().property("active") == "false"
          for i in range(sb._list.count() - 1)))

sb.set_plugins_active(False)
check("nav button unchecks", not sb._plugins_btn.isChecked())


# ---------------------------------------------------------------------------
print("[3] workspace activity glow dot")
from PySide6.QtCore import QAbstractAnimation
from workspace_sidebar import _WorkspaceRow

idle = FakeWorkspace("Idle", busy=False)
working = FakeWorkspace("Working", busy=True)
sb.refresh([idle, working], idle)
dot_rows = [
    sb._list.itemAt(i).widget()
    for i in range(sb._list.count())
    if isinstance(sb._list.itemAt(i).widget(), _WorkspaceRow)
]
check("idle workspace dot dark", dot_rows[0]._dot._busy is False)
check("working workspace dot lit", dot_rows[1]._dot._busy is True)
check(
    "lit dot is pulsing",
    dot_rows[1]._dot._pulse.state() == QAbstractAnimation.State.Running,
)

kept = dot_rows[0]._dot
idle._busy = True
working._busy = False
sb.refresh_activity()
check("refresh_activity reuses the row", dot_rows[0]._dot is kept)
check("dot follows the workspace on", dot_rows[0]._dot._busy is True)
check("dot follows the workspace off", dot_rows[1]._dot._busy is False)
check(
    "darkened dot stops pulsing",
    dot_rows[1]._dot._pulse.state() == QAbstractAnimation.State.Stopped,
)
dot_rows[0]._dot._on_pulse(0.5)
dot_rows[0]._dot.grab()  # must not raise
check("lit dot paints", True)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
