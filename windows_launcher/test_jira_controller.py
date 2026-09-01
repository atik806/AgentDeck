"""Offline tests for jira_controller.py -- the Qt bridge. No network.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_jira_controller.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import jira_controller
import jira_mcp
from plugin_store import JIRA, PluginStore

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


# Never let the injector touch the real ~/.claude.json during tests.
_TEST_CC = Path(tempfile.mkdtemp()) / ".claude.json"
jira_mcp._claude_config_path = lambda: _TEST_CC


def fresh_controller(tmp):
    jc = jira_controller.JiraController(account=None, config={"agent": "claude"})
    jc._store = PluginStore(Path(tmp) / "plugins.json")
    return jc


def _atlassian_srv():
    if not _TEST_CC.exists():
        return None
    return (json.loads(_TEST_CC.read_text()).get("mcpServers") or {}).get("atlassian")


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
    check("plugins.json has the connection (key 'jira')", jc._store.is_connected(JIRA))
    check("server injected as 'atlassian' at user scope", _atlassian_srv() is not None)
    check("server is the Atlassian hosted OAuth MCP",
          _atlassian_srv() and _atlassian_srv()["url"] == "https://mcp.atlassian.com/v1/mcp/authv2")
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
    check("plugins.json entry removed", not jc._store.is_connected(JIRA))
    check("is_connected false", not jc.is_connected)
    check("managed server removed", _atlassian_srv() is None)


# ---------------------------------------------------------------------------
print("[4] ensure_wired no-ops when the agent isn't claude")
with tempfile.TemporaryDirectory() as tmp:
    jc = fresh_controller(tmp)
    jc._config = {"agent": "codex"}
    jc._store.put(jira_controller.PluginConnection(JIRA))
    check("ensure_wired declines for a non-claude agent", not jc.ensure_wired())


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
