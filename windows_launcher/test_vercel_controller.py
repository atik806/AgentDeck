"""Offline tests for vercel_controller.py -- the Qt bridge. No network.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_vercel_controller.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import vercel_controller
import vercel_mcp
from plugin_store import VERCEL, PluginStore

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
vercel_mcp._claude_config_path = lambda: _TEST_CC


def fresh_controller(tmp):
    vc = vercel_controller.VercelController(account=None, config={"agent": "claude"})
    vc._store = PluginStore(Path(tmp) / "plugins.json")
    return vc


def _vercel_srv():
    if not _TEST_CC.exists():
        return None
    return (json.loads(_TEST_CC.read_text()).get("mcpServers") or {}).get("vercel")


# ---------------------------------------------------------------------------
print("[1] construction -- nothing connected")
with tempfile.TemporaryDirectory() as tmp:
    vc = fresh_controller(tmp)
    check("not connected", not vc.is_connected)
    check("login is empty", vc.login == "")
    check("connection is None", vc.connection is None)


# ---------------------------------------------------------------------------
print("[2] connect -- writes plugins.json + the user-scope MCP server")
with tempfile.TemporaryDirectory() as tmp:
    vc = fresh_controller(tmp)
    conns = []
    vc.connected.connect(conns.append)
    vc.start_connect()

    check("connected fired", pump(lambda: conns))
    check("is_connected now true", vc.is_connected)
    check("plugins.json has the connection", vc._store.is_connected(VERCEL))
    check("vercel server injected at user scope", _vercel_srv() is not None)
    check("server is the hosted OAuth MCP", _vercel_srv() and _vercel_srv()["url"] == "https://mcp.vercel.com")
    check("controller not busy afterwards", not vc.is_busy)


# ---------------------------------------------------------------------------
print("[3] disconnect -- drops the entry + the server")
with tempfile.TemporaryDirectory() as tmp:
    vc = fresh_controller(tmp)
    vc.start_connect()
    pump(lambda: vc.is_connected)

    gone = []
    vc.disconnected.connect(lambda: gone.append(1))
    vc.disconnect()
    check("disconnected fired", pump(lambda: gone))
    check("plugins.json entry removed", not vc._store.is_connected(VERCEL))
    check("is_connected false", not vc.is_connected)
    check("managed server removed", _vercel_srv() is None)


# ---------------------------------------------------------------------------
print("[4] ensure_wired no-ops when the agent isn't claude")
with tempfile.TemporaryDirectory() as tmp:
    vc = fresh_controller(tmp)
    vc._config = {"agent": "codex"}
    vc._store.put(vercel_controller.PluginConnection(VERCEL))
    check("ensure_wired declines for a non-claude agent", not vc.ensure_wired())


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
