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


# ---------------------------------------------------------------------------
print("[4] plugins catalog + GitHub detail")

from PySide6.QtCore import QObject, Signal
from plugins_panel import PluginsPanel
from plugin_store import PluginConnection, GITHUB


class FakeGitHub(QObject):
    connected = Signal(dict)
    disconnected = Signal()
    busy_changed = Signal(bool)
    error = Signal(str)
    device_code_ready = Signal(dict)
    repos_ready = Signal(list)
    state_changed = Signal()

    def __init__(self, connected=False):
        super().__init__()
        self._connected = connected
        self.is_busy = False
        self.login = "atik806" if connected else ""
        self._caps = ["read", "review"]
        self.started = False
        self.repos_fetched = False

    @property
    def is_connected(self):
        return self._connected

    @property
    def connection(self):
        if not self._connected:
            return None
        return PluginConnection(GITHUB, login=self.login, capabilities=self._caps)

    def start_connect(self):
        self.started = True

    def cancel_connect(self):
        pass

    def disconnect(self):
        self._connected = False
        self.disconnected.emit()

    def set_capabilities(self, caps):
        self._caps = list(caps)
        self.state_changed.emit()

    def set_automation(self, cap, mode):
        pass

    def fetch_repos(self):
        self.repos_fetched = True
        self.repos_ready.emit([{"full_name": "atik806/AgentDeck", "private": False}])


# -- catalog, not connected
gh0 = FakeGitHub(connected=False)
panel = PluginsPanel(github=gh0, account=None, config={})
panel.resize(900, 640)
panel.grab()
check("starts on the catalog", panel._stack.currentIndex() == 0)
gh_card = [c for c in panel._cards if c.key == "github"][0]
check("github card is interactive", gh_card.property("interactive") == "true")
check("github card shows NOT CONNECTED", "NOT CONNECTED" in gh_card._pill.text())

panel._open_detail("github")
check("clicking github opens detail", panel._stack.currentIndex() == 1)
check("detail shows Connect", not panel._gh_detail._primary.isHidden())
panel._gh_detail._on_primary()
check("Connect calls the controller", gh0.started)

# -- search filter
panel.show_catalog()
panel._filter_cards("hub")
check("search 'hub' keeps GitHub", not gh_card.isHidden())
panel._filter_cards("zzz")
check("search 'zzz' hides GitHub", gh_card.isHidden())
panel._filter_cards("")

# -- connected
gh1 = FakeGitHub(connected=True)
panel1 = PluginsPanel(github=gh1, account=None, config={})
card1 = [c for c in panel1._cards if c.key == "github"][0]
check("connected card shows CONNECTED", "CONNECTED" in card1._pill.text())
panel1._open_detail("github")
d = panel1._gh_detail
check("detail hides Connect when connected", d._primary.isHidden())
check("capability rows visible", not d._caps_wrap.isHidden())
check("read capability forced on + disabled",
      d._cap_boxes["read"].isChecked() and not d._cap_boxes["read"].isEnabled())
check("review capability reflects connection", d._cap_boxes["review"].isChecked())
check("repos were fetched on show", gh1.repos_fetched)
check("repo listed", d._repos.count() == 1)

d._cap_boxes["actions"].setChecked(True)
check("ticking a capability pushes it to the controller", "actions" in gh1._caps)

review_payloads = []
panel1.review_ready.connect(review_payloads.append)
d.review_ready.emit({"repo": "a/b", "pr_number": 5, "options": {}})
check("review_ready bubbles up from the panel", review_payloads == [{"repo": "a/b", "pr_number": 5, "options": {}}])

d._on_disconnect()
check("disconnect flips the card back", "NOT CONNECTED" in card1._pill.text())

panel1.apply_theme()  # must not raise
check("apply_theme survives", True)


# ---------------------------------------------------------------------------
print("[5] Vercel card + detail (thin plugin)")

from plugin_store import VERCEL


class FakeVercel(QObject):
    connected = Signal(dict)
    disconnected = Signal()
    busy_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, connected=False):
        super().__init__()
        self._connected = connected
        self.is_busy = False
        self.login = ""
        self.started = False

    @property
    def is_connected(self):
        return self._connected

    @property
    def connection(self):
        return PluginConnection(VERCEL) if self._connected else None

    def start_connect(self):
        self.started = True
        self._connected = True
        self.connected.emit({})

    def disconnect(self):
        self._connected = False
        self.disconnected.emit()


# -- tolerates vercel=None
p_none = PluginsPanel(github=FakeGitHub(), vercel=None, account=None, config={})
p_none.resize(900, 640)
p_none.grab()
v_card_none = [c for c in p_none._cards if c.key == "vercel"][0]
check("vercel=None tolerated; card still renders", v_card_none.property("interactive") == "true")
check("vercel card shows NOT ENABLED", "NOT ENABLED" in v_card_none._pill.text())

# -- not connected
fv = FakeVercel(connected=False)
vp = PluginsPanel(github=FakeGitHub(), vercel=fv, account=None, config={})
vp.resize(900, 640)
v_card = [c for c in vp._cards if c.key == "vercel"][0]
check("vercel card interactive", v_card.property("interactive") == "true")
vp._open_detail("vercel")
check("clicking vercel opens its detail page", vp._stack.currentIndex() == 2)
vd = vp._vercel_detail
check("detail shows Connect", not vd._primary.isHidden())
check("info box hidden until connected", vd._info.isHidden())
vd._on_primary()
check("Connect calls the controller", fv.started)
check("card flips to ENABLED", "ENABLED" in v_card._pill.text() and "NOT" not in v_card._pill.text())
vd.refresh()
check("detail hides Connect when connected", vd._primary.isHidden())
check("info box (/mcp instructions) shown when connected", not vd._info.isHidden())
check("disconnect button shown", not vd._disconnect_btn.isHidden())

# -- agent-aware authorise copy (per-agent, not hard-coded to Claude)
import mcp_targets as _mt
_saved = set(_mt.OAUTH_ALLOWLIST)
_mt.OAUTH_ALLOWLIST = {"claude", "codex"}   # simulate Phase 3 widening the set
try:
    vp_multi = PluginsPanel(github=FakeGitHub(), vercel=FakeVercel(connected=True), account=None, config={},
                            agents_provider=lambda: ["claude", "codex", "gemini"])
    vp_multi._open_detail("vercel")
    vd2 = vp_multi._vercel_detail
    vd2.refresh()
    _txt = vd2._step.text()
    check("names Claude Code's /mcp", "Claude Code" in _txt and "/mcp" in _txt)
    check("names Codex's own login command", "codex mcp login vercel" in _txt)
    check("gemini (not OAuth-capable) is left out", "Gemini" not in _txt)
    check("sub line lists the wired agents", "Enabled for:" in vd2._sub.text())
finally:
    _mt.OAUTH_ALLOWLIST = _saved

# -- search filter
vp.show_catalog()
vp._filter_cards("vercel")
check("search 'vercel' keeps the card", not v_card.isHidden())
vp._filter_cards("zzz")
check("search 'zzz' hides the card", v_card.isHidden())
vp._filter_cards("")

# -- disconnect flips the card back
vd._on_disconnect()
check("disconnect flips the card back to NOT ENABLED", "NOT ENABLED" in v_card._pill.text())

# -- Pro gate
class _FreeAccount:
    plan = "free"

vp_free = PluginsPanel(github=FakeGitHub(), vercel=FakeVercel(), account=_FreeAccount(), config={})
vp_free._open_detail("vercel")
check("Free plan labels Connect (Pro) and disables it",
      vp_free._vercel_detail._primary.text().endswith("(Pro)")
      and not vp_free._vercel_detail._primary.isEnabled())


# ---------------------------------------------------------------------------
print("[6] Jira card + detail (thin plugin, same shape as Vercel)")

from plugin_store import JIRA


class FakeJira(QObject):
    connected = Signal(dict)
    disconnected = Signal()
    busy_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, connected=False):
        super().__init__()
        self._connected = connected
        self.is_busy = False
        self.login = ""
        self.started = False

    @property
    def is_connected(self):
        return self._connected

    @property
    def connection(self):
        return PluginConnection(JIRA) if self._connected else None

    def start_connect(self):
        self.started = True
        self._connected = True
        self.connected.emit({})

    def disconnect(self):
        self._connected = False
        self.disconnected.emit()


# -- tolerates jira=None
jp_none = PluginsPanel(github=FakeGitHub(), vercel=None, jira=None, account=None, config={})
jp_none.resize(900, 640)
jp_none.grab()
j_card_none = [c for c in jp_none._cards if c.key == "jira"][0]
check("jira=None tolerated; card still renders", j_card_none.property("interactive") == "true")
check("jira card shows NOT ENABLED", "NOT ENABLED" in j_card_none._pill.text())

# -- not connected
fj = FakeJira(connected=False)
jp = PluginsPanel(github=FakeGitHub(), vercel=FakeVercel(), jira=fj, account=None, config={})
jp.resize(900, 640)
j_card = [c for c in jp._cards if c.key == "jira"][0]
check("jira card interactive", j_card.property("interactive") == "true")
jp._open_detail("jira")
check("clicking jira opens its detail page (stack index 3)", jp._stack.currentIndex() == 3)
jd = jp._jira_detail
check("detail shows Connect", not jd._primary.isHidden())
check("info box hidden until connected", jd._info.isHidden())
jd._on_primary()
check("Connect calls the controller", fj.started)
check("card flips to ENABLED", "ENABLED" in j_card._pill.text() and "NOT" not in j_card._pill.text())
jd.refresh()
check("detail hides Connect when connected", jd._primary.isHidden())
check("info box (/mcp instructions) shown when connected", not jd._info.isHidden())
check("disconnect button shown", not jd._disconnect_btn.isHidden())

# -- search filter
jp.show_catalog()
jp._filter_cards("jira")
check("search 'jira' keeps the card", not j_card.isHidden())
jp._filter_cards("zzz")
check("search 'zzz' hides the card", j_card.isHidden())
jp._filter_cards("")

# -- disconnect flips the card back
jd._on_disconnect()
check("disconnect flips the card back to NOT ENABLED", "NOT ENABLED" in j_card._pill.text())

# -- Pro gate
jp_free = PluginsPanel(github=FakeGitHub(), jira=FakeJira(), account=_FreeAccount(), config={})
jp_free._open_detail("jira")
check("Free plan labels Connect (Pro) and disables it",
      jp_free._jira_detail._primary.text().endswith("(Pro)")
      and not jp_free._jira_detail._primary.isEnabled())


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
