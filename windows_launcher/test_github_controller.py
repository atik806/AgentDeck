"""Offline tests for github_controller.py -- the Qt bridge. No network.

``github_auth.requests`` / ``github_api.requests`` are swapped for scripted
fakes; the real QThread workers run against them. Run:

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_github_controller.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SANDBOX = tempfile.mkdtemp(prefix="adk-ghctrl-")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import github_auth
import github_api
import github_controller
import github_mcp
from github_auth import GitHubToken, GitHubTokenStore
from plugin_store import GITHUB, PluginStore

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


def pump(until, ms=4000):
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


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = str(payload)

    def json(self):
        return self._p


class FakeRequests:
    def __init__(self):
        self.script = {}

    def queue(self, sub, *responses):
        self.script.setdefault(sub, []).extend(responses)

    def post(self, url, data=None, headers=None, timeout=None):
        for sub, rs in self.script.items():
            if sub in url and rs:
                return rs.pop(0)
        return _Resp({"error": "unexpected"}, 400)

    def get(self, url, headers=None, params=None, timeout=None):
        for sub, rs in self.script.items():
            if sub in url and rs:
                return rs.pop(0)
        return _Resp({}, 404)

    def delete(self, *a, **k):
        return _Resp({}, 204)

    class RequestException(Exception):
        pass


github_auth.GITHUB_CLIENT_ID = "Iv1.test"

# Every agent config + the ledger live in _SANDBOX (env vars set above), so the
# injector never touches the real ~/.claude.json, ~/.codex, %APPDATA%\...
_CLAUDE_CFG = Path(_SANDBOX) / "claude.json"


def _reset_sandbox():
    for p in Path(_SANDBOX).glob("*"):
        p.unlink(missing_ok=True)


def fresh_controller(tmp):
    gc = github_controller.GitHubController(
        account=None, config={"agent": "claude", "plugins_wire_all_agents": False})
    gc._tokens = GitHubTokenStore(Path(tmp) / "github.bin")
    gc._store = PluginStore(Path(tmp) / "plugins.json")
    gc._token = None
    return gc


# ---------------------------------------------------------------------------
print("[1] construction -- nothing connected")
with tempfile.TemporaryDirectory() as tmp:
    gc = fresh_controller(tmp)
    check("not connected", not gc.is_connected)
    check("no login", gc.login == "")
    check("connection is None", gc.connection is None)


# ---------------------------------------------------------------------------
print("[2] device-flow connect end to end")
with tempfile.TemporaryDirectory() as tmp:
    fake = FakeRequests()
    github_auth.requests = fake
    github_api.requests = fake
    fake.queue("login/device/code",
               _Resp({"device_code": "dc", "user_code": "ABCD-1234",
                      "verification_uri": "https://github.com/login/device",
                      "expires_in": 900, "interval": 1}))
    fake.queue("login/oauth/access_token",
               _Resp({"error": "authorization_pending"}),
               _Resp({"access_token": "gho_live", "refresh_token": "ghr_live",
                      "expires_in": 28800, "token_type": "bearer", "scope": "repo"}))
    fake.queue("api.github.com/user",
               _Resp({"login": "atik806", "name": "Atik", "avatar_url": ""}))

    gc = fresh_controller(tmp)
    codes = []
    conns = []
    gc.device_code_ready.connect(codes.append)
    gc.connected.connect(conns.append)
    gc.start_connect()

    check("device code surfaced", pump(lambda: codes) and codes[0]["user_code"] == "ABCD-1234")
    check("connected fired", pump(lambda: conns))
    check("login learned", conns and conns[0].get("login") == "atik806")
    check("is_connected now true", gc.is_connected)
    check("token persisted to the vault", gc._tokens.load() is not None)
    check("plugins.json has the connection", gc._store.is_connected(GITHUB))
    check("default capabilities read + review", gc.connection.capabilities == ["read", "review"])
    check("controller not busy afterwards", not gc.is_busy)


# ---------------------------------------------------------------------------
print("[3] capability editing + MCP rewire")
with tempfile.TemporaryDirectory() as tmp:
    gc = fresh_controller(tmp)
    gc._token = GitHubToken(access_token="gho_x", expires_at=0)
    gc._store.put(github_controller.PluginConnection(GITHUB, login="atik806", capabilities=["read", "review"]))

    _reset_sandbox()
    gc._config = {"agent": "claude", "plugins_wire_all_agents": False}

    repo = Path(tmp) / "repo"
    repo.mkdir()

    def _gh_srv():
        import json
        if not _CLAUDE_CFG.exists():
            return None
        return (json.loads(_CLAUDE_CFG.read_text()).get("mcpServers") or {}).get("github")

    check("ensure_wired writes the user-scope server for claude",
          gc.ensure_wired(str(repo), "claude") and _gh_srv() is not None)
    check("no .mcp.json in the repo folder", not (repo / ".mcp.json").exists())
    check("ensure_wired works with no agent_command when config says claude",
          gc.ensure_wired() is not None)

    gc._config = {"agent": "none", "plugins_wire_all_agents": False}
    check("ensure_wired no-ops when there's no target agent", not gc.ensure_wired())
    gc._config = {"agent": "claude", "plugins_wire_all_agents": False}

    gc.set_capabilities(["read", "review", "actions"])
    check("capability persisted", "actions" in gc._store.get(GITHUB).capabilities)
    check("rewire pushed the actions toolset",
          "actions" in _gh_srv()["headers"]["X-MCP-Toolsets"])

    gc.unwire_all()
    check("unwire_all removed the managed server", _gh_srv() is None)


# ---------------------------------------------------------------------------
print("[4] disconnect")
with tempfile.TemporaryDirectory() as tmp:
    fake = FakeRequests()
    github_auth.requests = fake
    gc = fresh_controller(tmp)
    gc._token = GitHubToken(access_token="gho_x", expires_at=0)
    gc._tokens.save(gc._token)
    gc._store.put(github_controller.PluginConnection(GITHUB, login="atik806"))
    gone = []
    gc.disconnected.connect(lambda: gone.append(1))
    gc.disconnect()
    check("disconnected fired", pump(lambda: gone))
    check("token vault cleared", gc._tokens.load() is None)
    check("plugins.json entry removed", not gc._store.is_connected(GITHUB))
    check("is_connected false", not gc.is_connected)


# ---------------------------------------------------------------------------
print("[5] _valid_token_blocking")
with tempfile.TemporaryDirectory() as tmp:
    gc = fresh_controller(tmp)
    gc._token = GitHubToken(access_token="fresh", expires_at=0)  # 0 = never expires
    check("non-expiring token returned as-is", gc._valid_token_blocking() == "fresh")
    gc._token = None
    check("no token -> None", gc._valid_token_blocking() is None)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
