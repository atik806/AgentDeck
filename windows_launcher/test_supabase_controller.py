"""Offline tests for supabase_controller.py -- the Qt bridge. No network.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_supabase_controller.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SANDBOX = tempfile.mkdtemp(prefix="adk-supabasectrl-")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import supabase_controller
import supabase_mcp
from plugin_store import SUPABASE, PluginStore

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
_REF = "abcdefghijklmnopqrst"


def _reset_sandbox():
    for p in Path(_SANDBOX).glob("*"):
        p.unlink(missing_ok=True)


def fresh_controller(tmp):
    sc = supabase_controller.SupabaseController(
        account=None, config={"agent": "claude", "plugins_wire_all_agents": False})
    sc._store = PluginStore(Path(tmp) / "plugins.json")
    return sc


def _supabase_srv():
    if not _CLAUDE_CFG.exists():
        return None
    return (json.loads(_CLAUDE_CFG.read_text()).get("mcpServers") or {}).get("supabase")


# ---------------------------------------------------------------------------
print("[1] construction -- nothing connected")
with tempfile.TemporaryDirectory() as tmp:
    sc = fresh_controller(tmp)
    check("not connected", not sc.is_connected)
    check("login is empty", sc.login == "")
    check("connection is None", sc.connection is None)
    check("project_ref is empty", sc.project_ref == "")


# ---------------------------------------------------------------------------
print("[2] start_connect refuses an empty / invalid project_ref")
with tempfile.TemporaryDirectory() as tmp:
    sc = fresh_controller(tmp)
    errs = []
    sc.error.connect(errs.append)

    check("empty ref refused", not sc.start_connect(""))
    check("error emitted", pump(lambda: errs))
    check("still not connected", not sc.is_connected)

    errs.clear()
    check("whitespace-only ref refused", not sc.start_connect("   "))
    check("garbage-with-spaces ref refused", not sc.start_connect("not a ref!"))
    check("nothing wired", _supabase_srv() is None)


# ---------------------------------------------------------------------------
print("[3] connect -- writes plugins.json (with settings) + the user-scope MCP server")
with tempfile.TemporaryDirectory() as tmp:
    _reset_sandbox()
    sc = fresh_controller(tmp)
    conns = []
    sc.connected.connect(conns.append)
    check("start_connect succeeds", sc.start_connect(_REF))

    check("connected fired", pump(lambda: conns))
    check("is_connected now true", sc.is_connected)
    check("plugins.json has the connection (key 'supabase')", sc._store.is_connected(SUPABASE))
    check("project_ref recorded", sc.project_ref == _REF)
    check("read_only recorded true by default", sc.connection.settings.get("read_only") == "true")
    check("server injected as 'supabase' at user scope", _supabase_srv() is not None)
    check("url is scoped to the project + read-only",
          _supabase_srv()["url"] == f"https://mcp.supabase.com/mcp?project_ref={_REF}&read_only=true")
    check("controller not busy afterwards", not sc.is_busy)


# ---------------------------------------------------------------------------
print("[4] update_settings -- changing project_ref re-injects with the new URL")
with tempfile.TemporaryDirectory() as tmp:
    _reset_sandbox()
    sc = fresh_controller(tmp)
    sc.start_connect(_REF)
    pump(lambda: sc.is_connected)

    new_ref = "zzzzzzzzzzzzzzzzzzzz"
    check("update_settings accepts a new ref", sc.update_settings(project_ref=new_ref))
    check("project_ref property reflects it", sc.project_ref == new_ref)
    check("wired server now points at the new project", new_ref in _supabase_srv()["url"])

    check("update_settings refuses a bad ref", not sc.update_settings(project_ref="!!"))
    check("old (valid) ref left in place after a refused update", sc.project_ref == new_ref)

    check("update_settings can flip read_only off", sc.update_settings(read_only=False))
    check("url now says read_only=false", "read_only=false" in _supabase_srv()["url"])

    with tempfile.TemporaryDirectory() as tmp2:
        check("update_settings on a disconnected controller is a no-op",
              not fresh_controller(tmp2).update_settings(project_ref=_REF))


# ---------------------------------------------------------------------------
print("[5] disconnect -- drops the entry + the server")
with tempfile.TemporaryDirectory() as tmp:
    _reset_sandbox()
    sc = fresh_controller(tmp)
    sc.start_connect(_REF)
    pump(lambda: sc.is_connected)

    gone = []
    sc.disconnected.connect(lambda: gone.append(1))
    sc.disconnect()
    check("disconnected fired", pump(lambda: gone))
    check("plugins.json entry removed", not sc._store.is_connected(SUPABASE))
    check("is_connected false", not sc.is_connected)
    check("managed server removed", _supabase_srv() is None)


# ---------------------------------------------------------------------------
print("[6] ensure_wired no-ops when no target agent can run the OAuth handshake")
with tempfile.TemporaryDirectory() as tmp:
    _reset_sandbox()
    sc = fresh_controller(tmp)
    sc._config = {"agent": "aider", "plugins_wire_all_agents": False}
    sc._store.put(supabase_controller.PluginConnection(SUPABASE, settings={"project_ref": _REF}))
    check("ensure_wired declines -- aider has no MCP support", not sc.ensure_wired())

    sc._config = {"agent": "claude", "plugins_wire_all_agents": False}
    check("ensure_wired writes for claude", sc.ensure_wired() and _supabase_srv() is not None)

    # opencode runs the MCP OAuth handshake itself too
    _reset_sandbox()
    sc._config = {"agent": "opencode", "plugins_wire_all_agents": False}
    check("ensure_wired writes for opencode", sc.ensure_wired())
    _oc = Path(_SANDBOX) / "opencode.json"
    check("opencode config got the scoped, tokenless 'supabase' server",
          _oc.exists() and (json.loads(_oc.read_text()).get("mcp") or {}).get("supabase") is not None)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
