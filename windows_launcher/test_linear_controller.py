"""Offline tests for linear_controller.py -- the Qt bridge. No network.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_linear_controller.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SANDBOX = tempfile.mkdtemp(prefix="adk-linearctrl-")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import linear_controller
import linear_mcp
from plugin_store import LINEAR, PluginStore

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


def pump(until, ms=3000):
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


def _reset_sandbox():
    for p in Path(_SANDBOX).glob("*"):
        p.unlink(missing_ok=True)


def fresh_controller(tmp):
    jc = linear_controller.LinearController(
        account=None, config={"agent": "claude", "plugins_wire_all_agents": False})
    jc._store = PluginStore(Path(tmp) / "plugins.json")
    return jc


def _linear_srv():
    if not _CLAUDE_CFG.exists():
        return None
    return (json.loads(_CLAUDE_CFG.read_text()).get("mcpServers") or {}).get("linear")


# ---------------------------------------------------------------------------
print("[1] construction -- nothing connected")
with tempfile.TemporaryDirectory() as tmp:
    jc = fresh_controller(tmp)
    check("not connected", not jc.is_connected)
    check("login is empty", jc.login == "")
    check("connection is None", jc.connection is None)


# ---------------------------------------------------------------------------
print("[2] connect -- writes plugins.json + the user-scope MCP server")
with tempfile.TemporaryDirectory() as tmp:
    jc = fresh_controller(tmp)
    conns = []
    jc.connected.connect(conns.append)
    jc.start_connect()

    check("connected fired", pump(lambda: conns))
    check("is_connected now true", jc.is_connected)
    check("plugins.json has the connection (key 'linear')", jc._store.is_connected(LINEAR))
    check("server injected as 'linear' at user scope", _linear_srv() is not None)
    check("server is the Linear hosted OAuth MCP",
          _linear_srv() and _linear_srv()["url"] == "https://mcp.linear.app/mcp")
    check("controller not busy afterwards", not jc.is_busy)


# ---------------------------------------------------------------------------
print("[3] disconnect -- drops the entry + the server")
with tempfile.TemporaryDirectory() as tmp:
    jc = fresh_controller(tmp)
    jc.start_connect()
    pump(lambda: jc.is_connected)

    gone = []
    jc.disconnected.connect(lambda: gone.append(1))
    jc.disconnect()
    check("disconnected fired", pump(lambda: gone))
    check("plugins.json entry removed", not jc._store.is_connected(LINEAR))
    check("is_connected false", not jc.is_connected)
    check("managed server removed", _linear_srv() is None)


# ---------------------------------------------------------------------------
print("[4] ensure_wired no-ops when no target agent can run the OAuth handshake")
with tempfile.TemporaryDirectory() as tmp:
    _reset_sandbox()
    jc = fresh_controller(tmp)
    jc._config = {"agent": "aider", "plugins_wire_all_agents": False}
    jc._store.put(linear_controller.PluginConnection(LINEAR))
    check("ensure_wired declines -- aider has no MCP support", not jc.ensure_wired())

    jc._config = {"agent": "claude", "plugins_wire_all_agents": False}
    check("ensure_wired writes for claude", jc.ensure_wired() and _linear_srv() is not None)

    # opencode runs the MCP OAuth handshake itself too
    _reset_sandbox()
    jc._config = {"agent": "opencode", "plugins_wire_all_agents": False}
    check("ensure_wired writes for opencode", jc.ensure_wired())
    _oc = Path(_SANDBOX) / "opencode.json"
    check("opencode config got the tokenless 'linear' server",
          _oc.exists() and (json.loads(_oc.read_text()).get("mcp") or {}).get("linear") is not None)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
