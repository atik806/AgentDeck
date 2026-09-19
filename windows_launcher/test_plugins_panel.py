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

    def ensure_wired(self, *a, **k):
        self.rewired = True
        return True

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
check("re-sync button shown when connected", not vd._resync_btn.isHidden())
vd._on_resync()
check("re-sync button calls ensure_wired on the controller", getattr(fv, "rewired", False))

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

    def ensure_wired(self, *a, **k):
        self.rewired = True
        return True

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
check("re-sync button shown when connected", not jd._resync_btn.isHidden())
jd._on_resync()
check("re-sync button calls ensure_wired on the controller", getattr(fj, "rewired", False))

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


# ---------------------------------------------------------------------------
print("[7] GitLab card + detail (thin plugin, same shape as Vercel)")

from plugin_store import GITLAB


class FakeGitLab(QObject):
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
        return PluginConnection(GITLAB) if self._connected else None

    def start_connect(self):
        self.started = True
        self._connected = True
        self.connected.emit({})

    def ensure_wired(self, *a, **k):
        self.rewired = True
        return True

    def disconnect(self):
        self._connected = False
        self.disconnected.emit()


# -- tolerates gitlab=None
glp_none = PluginsPanel(github=FakeGitHub(), gitlab=None, account=None, config={})
glp_none.resize(900, 640)
glp_none.grab()
gl_card_none = [c for c in glp_none._cards if c.key == "gitlab"][0]
check("gitlab=None tolerated; card still renders", gl_card_none.property("interactive") == "true")
check("gitlab card shows NOT ENABLED", "NOT ENABLED" in gl_card_none._pill.text())

# -- not connected
fgl = FakeGitLab(connected=False)
glp = PluginsPanel(github=FakeGitHub(), gitlab=fgl, account=None, config={})
glp.resize(900, 640)
gl_card = [c for c in glp._cards if c.key == "gitlab"][0]
check("gitlab card interactive", gl_card.property("interactive") == "true")
glp._open_detail("gitlab")
check("clicking gitlab opens its detail page (stack index 4)", glp._stack.currentIndex() == 4)
gld = glp._gitlab_detail
check("detail shows Connect", not gld._primary.isHidden())
check("info box hidden until connected", gld._info.isHidden())
gld._on_primary()
check("Connect calls the controller", fgl.started)
check("card flips to ENABLED", "ENABLED" in gl_card._pill.text() and "NOT" not in gl_card._pill.text())
gld.refresh()
check("detail hides Connect when connected", gld._primary.isHidden())
check("info box (authorise instructions) shown when connected", not gld._info.isHidden())
check("disconnect button shown", not gld._disconnect_btn.isHidden())
check("re-sync button shown when connected", not gld._resync_btn.isHidden())
gld._on_resync()
check("re-sync button calls ensure_wired on the controller", getattr(fgl, "rewired", False))

# -- search filter
glp.show_catalog()
glp._filter_cards("gitlab")
check("search 'gitlab' keeps the card", not gl_card.isHidden())
glp._filter_cards("zzz")
check("search 'zzz' hides the card", gl_card.isHidden())
glp._filter_cards("")

# -- disconnect flips the card back
gld._on_disconnect()
check("disconnect flips the card back to NOT ENABLED", "NOT ENABLED" in gl_card._pill.text())

# -- Pro gate
glp_free = PluginsPanel(github=FakeGitHub(), gitlab=FakeGitLab(), account=_FreeAccount(), config={})
glp_free._open_detail("gitlab")
check("Free plan labels Connect (Pro) and disables it",
      glp_free._gitlab_detail._primary.text().endswith("(Pro)")
      and not glp_free._gitlab_detail._primary.isEnabled())


# ---------------------------------------------------------------------------
print("[8] Linear card + detail (thin plugin, same shape as Vercel)")

from plugin_store import LINEAR


class FakeLinear(QObject):
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
        return PluginConnection(LINEAR) if self._connected else None

    def start_connect(self):
        self.started = True
        self._connected = True
        self.connected.emit({})

    def ensure_wired(self, *a, **k):
        self.rewired = True
        return True

    def disconnect(self):
        self._connected = False
        self.disconnected.emit()


# -- tolerates linear=None
lnp_none = PluginsPanel(github=FakeGitHub(), linear=None, account=None, config={})
lnp_none.resize(900, 640)
lnp_none.grab()
ln_card_none = [c for c in lnp_none._cards if c.key == "linear"][0]
check("linear=None tolerated; card still renders", ln_card_none.property("interactive") == "true")
check("linear card shows NOT ENABLED", "NOT ENABLED" in ln_card_none._pill.text())

# -- not connected
fln = FakeLinear(connected=False)
lnp = PluginsPanel(github=FakeGitHub(), linear=fln, account=None, config={})
lnp.resize(900, 640)
ln_card = [c for c in lnp._cards if c.key == "linear"][0]
check("linear card interactive", ln_card.property("interactive") == "true")
lnp._open_detail("linear")
check("clicking linear opens its detail page (stack index 5)", lnp._stack.currentIndex() == 5)
lnd = lnp._linear_detail
check("detail shows Connect", not lnd._primary.isHidden())
check("info box hidden until connected", lnd._info.isHidden())
lnd._on_primary()
check("Connect calls the controller", fln.started)
check("card flips to ENABLED", "ENABLED" in ln_card._pill.text() and "NOT" not in ln_card._pill.text())
lnd.refresh()
check("detail hides Connect when connected", lnd._primary.isHidden())
check("info box (authorise instructions) shown when connected", not lnd._info.isHidden())
check("disconnect button shown", not lnd._disconnect_btn.isHidden())
check("re-sync button shown when connected", not lnd._resync_btn.isHidden())
lnd._on_resync()
check("re-sync button calls ensure_wired on the controller", getattr(fln, "rewired", False))

# -- search filter
lnp.show_catalog()
lnp._filter_cards("linear")
check("search 'linear' keeps the card", not ln_card.isHidden())
lnp._filter_cards("zzz")
check("search 'zzz' hides the card", ln_card.isHidden())
lnp._filter_cards("")

# -- disconnect flips the card back
lnd._on_disconnect()
check("disconnect flips the card back to NOT ENABLED", "NOT ENABLED" in ln_card._pill.text())

# -- Pro gate
lnp_free = PluginsPanel(github=FakeGitHub(), linear=FakeLinear(), account=_FreeAccount(), config={})
lnp_free._open_detail("linear")
check("Free plan labels Connect (Pro) and disables it",
      lnp_free._linear_detail._primary.text().endswith("(Pro)")
      and not lnp_free._linear_detail._primary.isEnabled())


# ---------------------------------------------------------------------------
print("[9] Supabase card + detail (project_ref required, read-only badge fixed)")

from plugin_store import SUPABASE

_REF = "abcdefghijklmnopqrst"


class FakeSupabase(QObject):
    connected = Signal(dict)
    disconnected = Signal()
    busy_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, connected=False, project_ref=""):
        super().__init__()
        self._connected = connected
        self._ref = project_ref
        self.is_busy = False
        self.login = ""
        self.started_with = None
        self.updated_with = None

    @property
    def is_connected(self):
        return self._connected

    @property
    def project_ref(self):
        return self._ref if self._connected else ""

    @property
    def connection(self):
        return PluginConnection(SUPABASE, settings={"project_ref": self._ref}) if self._connected else None

    def start_connect(self, project_ref, *, read_only=True, features=""):
        ref = (project_ref or "").strip()
        if not ref:
            self.error.emit("Enter a Supabase project reference before connecting.")
            return False
        self.started_with = ref
        self._ref = ref
        self._connected = True
        self.connected.emit({})
        return True

    def update_settings(self, *, project_ref=None, read_only=None, features=None):
        if project_ref is not None:
            self.updated_with = project_ref
            self._ref = project_ref
        return True

    def ensure_wired(self, *a, **k):
        self.rewired = True
        return True

    def disconnect(self):
        self._connected = False
        self._ref = ""
        self.disconnected.emit()


# -- tolerates supabase=None
sbp_none = PluginsPanel(github=FakeGitHub(), supabase=None, account=None, config={})
sbp_none.resize(900, 640)
sbp_none.grab()
sb_card_none = [c for c in sbp_none._cards if c.key == "supabase"][0]
check("supabase=None tolerated; card still renders", sb_card_none.property("interactive") == "true")
check("supabase card shows NOT CONNECTED", "NOT CONNECTED" in sb_card_none._pill.text())

# -- not connected: Connect is refused without a project_ref
fsb = FakeSupabase(connected=False)
sbp = PluginsPanel(github=FakeGitHub(), supabase=fsb, account=None, config={})
sbp.resize(900, 640)
sb_card = [c for c in sbp._cards if c.key == "supabase"][0]
check("supabase card interactive", sb_card.property("interactive") == "true")
sbp._open_detail("supabase")
check("clicking supabase opens its detail page (stack index 6)", sbp._stack.currentIndex() == 6)
sbd = sbp._supabase_detail
check("detail shows Connect", not sbd._primary.isHidden())
check("read-only badge always shown", not sbd._ro_badge.isHidden())
check("info box hidden until connected", sbd._info.isHidden())

sbd._ref_field.setText("")
sbd._on_primary()
check("empty ref -- controller refuses, nothing started", fsb.started_with is None)
check("still not connected", not fsb.is_connected)

sbd._ref_field.setText(_REF)
sbd._on_primary()
check("Connect calls the controller with the ref", fsb.started_with == _REF)
check("card flips to CONNECTED · <ref>", _REF in sb_card._pill.text())
sbd.refresh()
check("detail hides Connect when connected", sbd._primary.isHidden())
check("ref field shows the connected project", sbd._ref_field.text() == _REF)
check("info box (authorise instructions) shown when connected", not sbd._info.isHidden())
check("disconnect button shown", not sbd._disconnect_btn.isHidden())
check("re-sync button shown when connected", not sbd._resync_btn.isHidden())
check("save-ref button shown when connected", not sbd._save_ref_btn.isHidden())

# -- editing the ref while connected re-injects via update_settings, no disconnect
new_ref = "zzzzzzzzzzzzzzzzzzzz"
sbd._ref_field.setText(new_ref)
sbd._on_save_ref()
check("Save calls update_settings with the new ref", fsb.updated_with == new_ref)
check("still connected (no disconnect/reconnect needed)", fsb.is_connected)

sbd._on_resync()
check("re-sync button calls ensure_wired on the controller", getattr(fsb, "rewired", False))

# -- search filter
sbp.show_catalog()
sbp._filter_cards("supabase")
check("search 'supabase' keeps the card", not sb_card.isHidden())
sbp._filter_cards("zzz")
check("search 'zzz' hides the card", sb_card.isHidden())
sbp._filter_cards("")

# -- disconnect flips the card back
sbd._on_disconnect()
check("disconnect flips the card back to NOT CONNECTED", "NOT CONNECTED" in sb_card._pill.text())

# -- Pro gate
sbp_free = PluginsPanel(github=FakeGitHub(), supabase=FakeSupabase(), account=_FreeAccount(), config={})
sbp_free._open_detail("supabase")
check("Free plan labels Connect (Pro) and disables it",
      sbp_free._supabase_detail._primary.text().endswith("(Pro)")
      and not sbp_free._supabase_detail._primary.isEnabled())

from PySide6.QtWidgets import QLineEdit

# ---------------------------------------------------------------------------
print("[10] Google Drive card + detail (static OAuth client: id + secret)")

from plugin_store import GDRIVE

_GD_ID = "1234567890-abcdefg.apps.googleusercontent.com"
_GD_SECRET = "GOCSPX-fake"


class FakeGDrive(QObject):
    connected = Signal(dict)
    disconnected = Signal()
    busy_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, connected=False, client_id="", has_secret=False):
        super().__init__()
        self._connected = connected
        self._id = client_id
        self._has_secret = has_secret
        self.is_busy = False
        self.login = ""
        self.started_with = None
        self.updated_with = None
        self.rewired = False

    @property
    def is_connected(self):
        return self._connected

    @property
    def client_id(self):
        return self._id if self._connected else ""

    @property
    def has_secret(self):
        return self._has_secret

    @property
    def connection(self):
        return PluginConnection(GDRIVE, settings={"client_id": self._id}) if self._connected else None

    def start_connect(self, client_id, client_secret):
        cid = (client_id or "").strip()
        secret = (client_secret or "").strip()
        if not cid or not secret:
            self.error.emit("Paste both the Client ID and the Client secret.")
            return False
        self.started_with = (cid, secret)
        self._id = cid
        self._has_secret = True
        self._connected = True
        self.connected.emit({})
        return True

    def update_settings(self, *, client_id=None, client_secret=None):
        self.updated_with = (client_id, client_secret)
        if client_id:
            self._id = client_id
        self.connected.emit({})
        return True

    def ensure_wired(self, folder=None, agent_command=None, **kw):
        self.rewired = True
        return True

    def disconnect(self):
        self._connected = False
        self._id = ""
        self._has_secret = False
        self.disconnected.emit()


# -- tolerates a missing controller
check("panel tolerates gdrive=None", PluginsPanel(github=FakeGitHub(), gdrive=None, config={}) is not None)

fgd = FakeGDrive()
gdp = PluginsPanel(github=FakeGitHub(), gdrive=fgd, config={})
gd_card = [c for c in gdp._cards if c.key == "gdrive"][0]
check("Google Drive card exists", gd_card is not None)
check("card is interactive (live)", gd_card.property("interactive") == "true")
check("card starts NOT ENABLED", "NOT ENABLED" in gd_card._pill.text())

gdp._open_detail("gdrive")
check("opens stack index 7", gdp._stack.currentIndex() == 7)
gdd = gdp._gdrive_detail

# -- the console link shows while disconnected, hides once connected
check("console link visible before connecting", gdd._console_btn.isVisibleTo(gdd))
check("save button hidden before connecting", not gdd._save_btn.isVisibleTo(gdd))
check("secret field is a password box", gdd._secret_field.echoMode() == QLineEdit.Password)
check("Claude-Code-only badge present", gdd._claude_badge.text() == "Claude Code only")
check("badge explains why in its tooltip",
      "dynamic client registration" in gdd._claude_badge.toolTip())

# -- connect needs BOTH fields
gdd._id_field.setText(_GD_ID)
gdd._secret_field.setText("")
gdd._on_primary()
check("connect with no secret is refused", fgd.is_connected is False)
check("  ...and the error lands in the subtitle", "Client ID" in gdd._sub.text())

gdd._secret_field.setText(_GD_SECRET)
gdd._on_primary()
check("connect passes both fields to the controller",
      fgd.started_with == (_GD_ID, _GD_SECRET))
check("card flips to ENABLED", "ENABLED" in gd_card._pill.text())
check("info box shows once connected", gdd._info.isVisibleTo(gdd))
check("save button appears once connected", gdd._save_btn.isVisibleTo(gdd))
check("console link hides once connected", not gdd._console_btn.isVisibleTo(gdd))
check("client id shown back", gdd._id_field.text() == _GD_ID)
check("secret box cleared after connect", gdd._secret_field.text() == "")
check("secret box says it is stored", "stored" in gdd._secret_field.placeholderText())

# -- authorise instructions name Claude Code only
check("authorise text mentions Claude Code", "Claude Code" in gdd._step.text())
for other in ("opencode", "Codex", "Gemini", "Goose"):
    check(f"authorise text does NOT mention {other}", other not in gdd._step.text())
check("subtitle lists only Claude Code", gdd._sub.text().startswith("Enabled for:"))

# -- saving only the id keeps the stored secret (empty secret box)
gdd._id_field.setText("999-other.apps.googleusercontent.com")
gdd._secret_field.setText("")
gdd._on_save()
check("save passes the new id", fgd.updated_with[0] == "999-other.apps.googleusercontent.com")
check("save passes an empty secret (= keep the stored one)", not fgd.updated_with[1])

# -- re-sync
fgd.rewired = False
gdd._on_resync()
check("re-sync button calls ensure_wired on the controller", fgd.rewired)

# -- search filter
gdp.show_catalog()
gdp._filter_cards("drive")
check("search 'drive' keeps the card", not gd_card.isHidden())
gdp._filter_cards("google")
check("search 'google' keeps the card", not gd_card.isHidden())
gdp._filter_cards("zzz")
check("search 'zzz' hides the card", gd_card.isHidden())
gdp._filter_cards("")

# -- disconnect flips the card back
gdd._on_disconnect()
check("disconnect flips the card back to NOT ENABLED", "NOT ENABLED" in gd_card._pill.text())

# -- Pro gate
gdp_free = PluginsPanel(github=FakeGitHub(), gdrive=FakeGDrive(), account=_FreeAccount(), config={})
gdp_free._open_detail("gdrive")
check("Free plan labels Connect (Pro) and disables it",
      gdp_free._gdrive_detail._primary.text().endswith("(Pro)")
      and not gdp_free._gdrive_detail._primary.isEnabled())


# ---------------------------------------------------------------------------
print("[11] LinkedIn card + detail (three tiers, one of them risky)")

from plugin_store import LINKEDIN


class FakeLinkedIn(QObject):
    connected = Signal(dict)
    disconnected = Signal()
    busy_changed = Signal(bool)
    error = Signal(str)
    tiers_changed = Signal()

    def __init__(self, connected=False, tiers=("official",)):
        super().__init__()
        self._connected = connected
        self._tiers = list(tiers)
        self.is_busy = False
        self.login = "Jane Dev" if connected else ""
        self.client_id = "client-abc" if connected else ""
        self.provider = "apify"
        self.actor = ""
        self.resume_path = ""
        self.has_secret = connected
        self.has_provider_key = False
        self.has_session_cookie = False
        self.token_expired = False
        self.started_with = None
        self.provider_set = None
        self.cookie_set = None
        self.cleared_cookie = False
        self.rewired = False

    @property
    def is_connected(self):
        return self._connected

    @property
    def tiers(self):
        return list(self._tiers)

    def tier_on(self, tier):
        return tier in self._tiers

    def start_connect(self, client_id, client_secret):
        cid = (client_id or "").strip()
        secret = (client_secret or "").strip()
        if not cid or not secret:
            self.error.emit("Paste both the Client ID and the Client Secret.")
            return False
        self.started_with = (cid, secret)
        self.client_id = cid
        self.has_secret = True
        self._connected = True
        self._tiers = ["official"]
        self.connected.emit({})
        return True

    def set_provider(self, provider, api_key="", actor=""):
        self.provider_set = (provider, api_key, actor)
        self.provider = provider
        self.actor = actor
        if api_key:
            self.has_provider_key = True
            if "jobs" not in self._tiers:
                self._tiers.append("jobs")
        self.tiers_changed.emit()
        return True

    def clear_provider_key(self):
        self.has_provider_key = False
        self._tiers = [t for t in self._tiers if t != "jobs"]
        self.tiers_changed.emit()

    def set_session_cookie(self, li_at):
        if not (li_at or "").strip():
            self.error.emit("Paste the li_at cookie value.")
            return False
        self.cookie_set = li_at
        self.has_session_cookie = True
        if "session" not in self._tiers:
            self._tiers.append("session")
        self.tiers_changed.emit()
        return True

    def clear_session_cookie(self):
        self.cleared_cookie = True
        self.has_session_cookie = False
        self._tiers = [t for t in self._tiers if t != "session"]
        self.tiers_changed.emit()

    def set_resume_path(self, path):
        self.resume_path = path
        self.tiers_changed.emit()

    def ensure_wired(self, folder=None, agent_command=None, **kw):
        self.rewired = True
        return True

    def disconnect(self):
        self._connected = False
        self._tiers = []
        self.has_secret = False
        self.disconnected.emit()


# -- tolerates a missing controller
check("panel tolerates linkedin=None",
      PluginsPanel(github=FakeGitHub(), linkedin=None, config={}) is not None)

fli = FakeLinkedIn()
lip = PluginsPanel(github=FakeGitHub(), linkedin=fli, config={})
li_card = [c for c in lip._cards if c.key == "linkedin"][0]
check("LinkedIn card exists", li_card is not None)
check("card is interactive (live)", li_card.property("interactive") == "true")
check("card starts NOT ENABLED", "NOT ENABLED" in li_card._pill.text())

lip._open_detail("linkedin")
check("opens stack index 8", lip._stack.currentIndex() == 8)
lid = lip._linkedin_detail

# -- disconnected: the setup hint is what's on screen
check("developer-portal link shows while disconnected", lid._portal_btn.isVisibleTo(lid))
check("the redirect URL is spelled out", "8977" in lid._hint.text())
check("job data is hidden until connected", not lid._jobs_box.isVisibleTo(lid))
check("session box is hidden until connected", not lid._session_box.isVisibleTo(lid))

# -- connect refuses a half-filled form
lid._id_field.setText("")
lid._secret_field.setText("nope")
lid._on_primary()
check("connect with no id is refused", not fli.is_connected)
check("...and the card says why", "Client ID" in lid._sub.text())

lid._id_field.setText("client-abc")
lid._secret_field.setText("s3cret")
lid._on_primary()
check("connect with both fields works", fli.is_connected)
check("the controller got both", fli.started_with == ("client-abc", "s3cret"))
check("card flips to ENABLED", "ENABLED" in li_card._pill.text()
      and "NOT" not in li_card._pill.text())
check("the secret box never shows the stored secret (REGRESSION)",
      lid._secret_field.text() == "" and "stored" in lid._secret_field.placeholderText())
check("no OAuth-authorise instructions -- the server is local (REGRESSION)",
      "authorise" not in lid._step.text() or "Nothing to authorise" in lid._step.text())
check("...it says to restart the agent instead", "restart" in lid._step.text().lower())

# -- job data
check("job data shows once connected", lid._jobs_box.isVisibleTo(lid))
check("the actor field shows for apify", lid._actor_field.isVisibleTo(lid))
lid._key_field.setText("rapid-key")
lid._on_save_provider()
check("saving the key reaches the controller", fli.provider_set[1] == "rapid-key")
check("the jobs tier is on", fli.tier_on("jobs"))
check("the key box is cleared after saving (REGRESSION)", lid._key_field.text() == "")
lid._on_clear_provider()
check("turning it off drops the tier", not fli.tier_on("jobs"))

# -- session: the confirm is mandatory
lid._confirm = lambda title, body: False        # the user says no
lid._cookie_field.setText("AQEDA-cookie")
lid._on_enable_session()
check("declining the warning leaves the tier OFF (REGRESSION)", not fli.tier_on("session"))
check("...and the cookie is never handed over", fli.cookie_set is None)

_asked = {}
def _yes(title, body):
    _asked["title"], _asked["body"] = title, body
    return True

lid._confirm = _yes
lid._on_enable_session()
check("accepting turns the tier on", fli.tier_on("session"))
check("the cookie reached the controller", fli.cookie_set == "AQEDA-cookie")
check("the cookie box is cleared afterwards", lid._cookie_field.text() == "")
check("the warning names the User Agreement", "User Agreement" in _asked["body"])
check("...and the actual consequence", "restricted" in _asked["body"])
lid._on_disable_session()
check("turning it off clears the cookie", fli.cleared_cookie and not fli.tier_on("session"))

# -- the routine offer
_routines = []
lip.routine_requested.connect(_routines.append)
lid._on_create_routine()
check("the panel re-emits the routine request", len(_routines) == 1)
check("it is a weekday-morning schedule",
      _routines[0]["days"] == [0, 1, 2, 3, 4] and _routines[0]["time"] == "09:00")
check("the prompt tells the agent NOT to apply (REGRESSION)",
      "Do not apply" in _routines[0]["prompt"])

_skills = []
lip.skill_requested.connect(_skills.append)
lid._on_create_skill()
check("the panel re-emits the skill request", len(_skills) == 1)
check("it is a SKILL.md with frontmatter", _skills[0].startswith("---")
      and "name: job-hunt" in _skills[0])
check("the skill repeats the no-apply rule (REGRESSION)",
      "cannot submit applications" in _skills[0])

# -- CV + re-sync + disconnect
lid._cv_field.setText(r"C:\cv.md")
lid._on_save_cv()
check("the CV path reaches the controller", fli.resume_path == r"C:\cv.md")
lid._on_resync()
check("re-sync rewires", fli.rewired)
lid._on_disconnect()
check("disconnect flips the card back", "NOT ENABLED" in li_card._pill.text())

# -- Free plan
lip_free = PluginsPanel(github=FakeGitHub(), linkedin=FakeLinkedIn(),
                        account=_FreeAccount(), config={})
check("Free plan labels Connect (Pro) and disables it",
      "Pro" in lip_free._linkedin_detail._primary.text()
      and not lip_free._linkedin_detail._primary.isEnabled())



print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
