"""Offline tests for gdrive_controller.py -- the Qt bridge. No network, no real
`claude` binary (a stub shim stands in for it).

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_gdrive_controller.py
"""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SANDBOX = tempfile.mkdtemp(prefix="adk-gdrivectrl-")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import gdrive_controller
import gdrive_mcp
from gdrive_secret import GDriveSecretStore
from plugin_store import GDRIVE, PluginStore

app = QApplication(sys.argv)

_CLIENT_ID = "1234567890-abcdefg.apps.googleusercontent.com"
_SECRET = "GOCSPX-fake-secret"

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


def pump(until, ms=5000):
    loop = QEventLoop()
    hit = {"v": False}

    def tick():
        if until():
            hit["v"] = True
            loop.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(15)
    QTimer.singleShot(ms, loop.quit)
    tick()
    if not hit["v"]:
        loop.exec()
    timer.stop()
    return hit["v"]


_CLAUDE_CFG = Path(_SANDBOX) / "claude.json"

# A stub standing in for `claude`: records its argv/env, prints what the real
# one prints on success. Keeps the whole suite offline and side-effect free.
_PROBE = Path(_SANDBOX) / "fake_claude.py"
_PROBE.write_text(
    "import json, os, sys\n"
    "calls = []\n"
    "p = os.environ['PROBE_OUT']\n"
    "if os.path.exists(p):\n"
    "    calls = json.load(open(p))\n"
    "calls.append({'argv': sys.argv[1:], 'secret': os.environ.get('MCP_CLIENT_SECRET')})\n"
    "json.dump(calls, open(p, 'w'))\n"
    "print('Added HTTP MCP server gdrive' if 'add' in sys.argv else 'Removed MCP server gdrive')\n",
    encoding="utf-8",
)
_PROBE_OUT = Path(_SANDBOX) / "probe.json"
os.environ["PROBE_OUT"] = str(_PROBE_OUT)

_SHIM = Path(_SANDBOX) / ("shim.cmd" if os.name == "nt" else "shim.sh")
if os.name == "nt":
    _SHIM.write_text(f'@echo off\r\n"{sys.executable}" "{_PROBE}" %*\r\n', encoding="utf-8")
else:
    _SHIM.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_PROBE}" "$@"\n', encoding="utf-8")
    _SHIM.chmod(_SHIM.stat().st_mode | stat.S_IEXEC)

# Point the module's subprocess calls at the shim for the whole run.
_real_seed = gdrive_mcp.seed_secret
_real_forget = gdrive_mcp.forget_secret
gdrive_mcp.seed_secret = lambda cid, sec, **kw: _real_seed(cid, sec, claude_bin=str(_SHIM))
gdrive_mcp.forget_secret = lambda **kw: _real_forget(claude_bin=str(_SHIM))


def _probe_calls():
    if not _PROBE_OUT.exists():
        return []
    return json.loads(_PROBE_OUT.read_text(encoding="utf-8"))


def _reset_sandbox():
    for p in Path(_SANDBOX).glob("*.json"):
        if p.name not in ("probe.json",):
            p.unlink(missing_ok=True)
    _PROBE_OUT.unlink(missing_ok=True)


def fresh_controller(tmp):
    gc = gdrive_controller.GDriveController(
        account=None, config={"agent": "claude", "plugins_wire_all_agents": False})
    gc._store = PluginStore(Path(tmp) / "plugins.json")
    gc._vault = GDriveSecretStore(Path(tmp) / "gdrive.bin")
    return gc


def _gdrive_srv():
    if not _CLAUDE_CFG.exists():
        return None
    return (json.loads(_CLAUDE_CFG.read_text()).get("mcpServers") or {}).get("gdrive")


# ---------------------------------------------------------------------------
print("[1] construction")
_reset_sandbox()
tmp1 = tempfile.mkdtemp(prefix="adk-gd1-")
c = fresh_controller(tmp1)
check("starts disconnected", c.is_connected is False)
check("no client id", c.client_id == "")
check("no secret stored", c.has_secret is False)
check("login is empty (no user identity for this plugin)", c.login == "")
check("not busy", c.is_busy is False)
check("connection is None", c.connection is None)


# ---------------------------------------------------------------------------
print("[2] validation refuses bad input without connecting")
errors = []
c.error.connect(errors.append)

check("empty id+secret refused", c.start_connect("", "") is False)
check("  ...with an error", len(errors) == 1 and "Client ID" in errors[-1])
check("missing secret refused", c.start_connect(_CLIENT_ID, "") is False)
check("malformed id refused", c.start_connect("not-a-google-id", _SECRET) is False)
check("  ...names the expected suffix", "apps.googleusercontent.com" in errors[-1])
check("still disconnected", c.is_connected is False)
check("nothing written to claude config", _gdrive_srv() is None)
check("no subprocess run", _probe_calls() == [])


# ---------------------------------------------------------------------------
print("[3] connect seeds the secret, stores the id, writes the server")
_reset_sandbox()
tmp3 = tempfile.mkdtemp(prefix="adk-gd3-")
c = fresh_controller(tmp3)
connected = []
c.connected.connect(connected.append)

check("start_connect accepted", c.start_connect(_CLIENT_ID, _SECRET) is True)
check("connect completes", pump(lambda: bool(connected)))
check("now connected", c.is_connected is True)
check("client id stored", c.client_id == _CLIENT_ID)
check("secret in the vault", c.has_secret is True)

srv = _gdrive_srv()
check("server written to claude config", srv is not None)
check("carries the client id", srv["oauth"]["clientId"] == _CLIENT_ID)
check("marked managed", srv["x-agentdeck-managed"] is True)
check("scopes present", "drive.readonly" in srv["oauth"]["scopes"])

calls = _probe_calls()
check("claude mcp remove ran first", calls and "remove" in calls[0]["argv"])
check("claude mcp add ran", any("add" in c_["argv"] for c_ in calls))
add_call = [c_ for c_ in calls if "add" in c_["argv"]][0]
check("secret went through the env", add_call["secret"] == _SECRET)
check("secret NOT in argv", _SECRET not in " ".join(add_call["argv"]))

stored = json.loads((Path(tmp3) / "plugins.json").read_text(encoding="utf-8"))
check("secret NOT in plugins.json", _SECRET not in json.dumps(stored))
check("client id IS in plugins.json", _CLIENT_ID in json.dumps(stored))


# ---------------------------------------------------------------------------
print("[4] update_settings re-seeds and re-injects in place")
_PROBE_OUT.unlink(missing_ok=True)
new_id = "555-newclient.apps.googleusercontent.com"
again = []
c.connected.connect(again.append)
check("update accepted", c.update_settings(client_id=new_id) is True)
check("update completes", pump(lambda: bool(again)))
check("new id stored", c.client_id == new_id)
check("new id in the config", _gdrive_srv()["oauth"]["clientId"] == new_id)
check("secret preserved when not re-entered", c.has_secret is True)
check("stored secret reused for the re-seed",
      [x for x in _probe_calls() if "add" in x["argv"]][0]["secret"] == _SECRET)

errs4 = []
c.error.connect(errs4.append)
check("malformed id rejected on update", c.update_settings(client_id="bogus") is False)
check("  ...id unchanged", c.client_id == new_id)


# ---------------------------------------------------------------------------
print("[5] disconnect clears everything")
_PROBE_OUT.unlink(missing_ok=True)
gone = []
c.disconnected.connect(lambda: gone.append(True))
c.disconnect()
check("disconnected signal", bool(gone))
check("no longer connected", c.is_connected is False)
check("server removed from claude config", _gdrive_srv() is None)
check("vault cleared", c.has_secret is False)
check("claude mcp remove ran", any("remove" in x["argv"] for x in _probe_calls()))


# ---------------------------------------------------------------------------
print("[6] a failed seed leaves nothing behind")
_reset_sandbox()
tmp6 = tempfile.mkdtemp(prefix="adk-gd6-")
_QUIET = Path(_SANDBOX) / ("quiet.cmd" if os.name == "nt" else "quiet.sh")
if os.name == "nt":
    _QUIET.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
else:
    _QUIET.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    _QUIET.chmod(_QUIET.stat().st_mode | stat.S_IEXEC)
gdrive_mcp.seed_secret = lambda cid, sec, **kw: _real_seed(cid, sec, claude_bin=str(_QUIET))

c6 = fresh_controller(tmp6)
errs6 = []
c6.error.connect(errs6.append)
check("start_connect accepted", c6.start_connect(_CLIENT_ID, _SECRET) is True)
check("failure surfaces", pump(lambda: bool(errs6)))
check("error carries the manual command", "claude mcp add" in errs6[-1])
check("NOT connected", c6.is_connected is False)
check("vault rolled back", c6.has_secret is False)
check("no server written", _gdrive_srv() is None)

gdrive_mcp.seed_secret = lambda cid, sec, **kw: _real_seed(cid, sec, claude_bin=str(_SHIM))


# ---------------------------------------------------------------------------
print("[7] ensure_wired targets Claude Code only")
_reset_sandbox()
tmp7 = tempfile.mkdtemp(prefix="adk-gd7-")
c7 = fresh_controller(tmp7)
done7 = []
c7.connected.connect(done7.append)
c7.start_connect(_CLIENT_ID, _SECRET)
pump(lambda: bool(done7))

c7._config = {"agent": "opencode", "plugins_wire_all_agents": False}
check("declines opencode", c7.ensure_wired() is False)
check("opencode config never created", not (Path(_SANDBOX) / "opencode.json").exists())
c7._config = {"agent": "aider", "plugins_wire_all_agents": False}
check("declines aider", c7.ensure_wired() is False)
c7._config = {"agent": "claude", "plugins_wire_all_agents": False}
_CLAUDE_CFG.unlink(missing_ok=True)
check("writes for claude", c7.ensure_wired() is True)
check("server back", _gdrive_srv() is not None)
c7.disconnect()


# ---------------------------------------------------------------------------
print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
