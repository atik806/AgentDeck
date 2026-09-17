"""Offline tests for gdrive_mcp.py -- the ~/.claude.json injector.

    .venv\\Scripts\\python.exe test_gdrive_mcp.py

Google Drive is the one plugin whose server is NOT tokenless: Google has no
Dynamic Client Registration, so the entry carries a static ``oauth`` block and
only Claude Code is wired. Several checks here are the deliberate inverse of
test_linear_mcp.py's -- that is the point of the plugin, not an oversight.
"""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="adk-gdrivemcp-")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

import gdrive_mcp
import linear_mcp
import supabase_mcp
import vercel_mcp

_CLIENT_ID = "1234567890-abcdefg.apps.googleusercontent.com"
_SETTINGS = {"client_id": _CLIENT_ID}


def _reset_ledger():
    Path(os.environ["ADK_MCP_STATE"]).unlink(missing_ok=True)


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


def _read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _claude_path():
    return Path(_SANDBOX) / "claude.json"


def _root_gdrive(cfg):
    return (cfg.get("mcpServers") or {}).get("gdrive")


# ---------------------------------------------------------------------------
print("[1] supports_agent -- static OAuth client: Claude Code ONLY")
check("claude supported", gdrive_mcp.supports_agent("claude"))
check("claude with args supported",
      gdrive_mcp.supports_agent("claude --dangerously-skip-permissions"))
check("opencode NOT supported", not gdrive_mcp.supports_agent("opencode"))
check("codex NOT supported", not gdrive_mcp.supports_agent("codex"))
check("gemini NOT supported", not gdrive_mcp.supports_agent("gemini"))
check("goose NOT supported", not gdrive_mcp.supports_agent("goose"))
check("aider not supported", not gdrive_mcp.supports_agent("aider"))
check("plain shell not supported", not gdrive_mcp.supports_agent(""))


# ---------------------------------------------------------------------------
print("[2] mcp_server_config -- hosted server with a static OAuth client")
cfg = gdrive_mcp.mcp_server_config(_SETTINGS)
blob = json.dumps(cfg)
check("type http", cfg["type"] == "http")
check("points at Google's hosted Drive MCP",
      cfg["url"] == "https://drivemcp.googleapis.com/mcp/v1")
check("marked managed", cfg["x-agentdeck-managed"] is True)
check("oauth block present", isinstance(cfg.get("oauth"), dict))
check("carries the client id", cfg["oauth"]["clientId"] == _CLIENT_ID)
check("callbackPort 8976", cfg["oauth"]["callbackPort"] == 8976)
check("drive.readonly scope", "drive.readonly" in cfg["oauth"]["scopes"])
check("drive.file scope", "drive.file" in cfg["oauth"]["scopes"])
check("NO headers block", "headers" not in cfg)
check("NO client secret anywhere", "clientSecret" not in blob)
check("no Bearer/Basic credential", "Bearer" not in blob and "Basic" not in blob)
check("empty client_id tolerated, not raised",
      gdrive_mcp.mcp_server_config({})["oauth"]["clientId"] == "")
check("custom scopes honoured",
      gdrive_mcp.mcp_server_config({"client_id": _CLIENT_ID, "scopes": "a b"})
      ["oauth"]["scopes"] == "a b")


# ---------------------------------------------------------------------------
print("[3] REGRESSION -- the dict-oauth render must be inert for every other plugin")
check("linear entry unchanged (no oauth key)", "oauth" not in linear_mcp.mcp_server_config())
check("vercel entry unchanged (no oauth key)", "oauth" not in vercel_mcp.mcp_server_config())
check("supabase entry unchanged (no oauth key)",
      "oauth" not in supabase_mcp.mcp_server_config({"project_ref": "abcdefghijkl"}))
check("linear still tokenless http",
      linear_mcp.mcp_server_config()["type"] == "http")


# ---------------------------------------------------------------------------
print("[4] inject / remove round-trip at the root")
_reset_ledger()
_claude_path().unlink(missing_ok=True)

check("inject writes", gdrive_mcp.inject(settings=_SETTINGS, agent_keys=["claude"]) is True)
cfg = _read(_claude_path())
entry = _root_gdrive(cfg)
check("server at root mcpServers", entry is not None)
check("entry carries the client id", entry["oauth"]["clientId"] == _CLIENT_ID)
check("not stashed under projects", "projects" not in cfg or not cfg["projects"])
check("re-inject is a no-op",
      gdrive_mcp.inject(settings=_SETTINGS, agent_keys=["claude"]) is False)

new_id = "9999-zzz.apps.googleusercontent.com"
check("changed client id overwrites in place",
      gdrive_mcp.inject(settings={"client_id": new_id}, agent_keys=["claude"]) is True)
check("new id landed", _root_gdrive(_read(_claude_path()))["oauth"]["clientId"] == new_id)

check("remove reports a change", gdrive_mcp.remove(agent_keys=["claude"]) is True)
check("server gone", _root_gdrive(_read(_claude_path())) is None)
check("remove is idempotent", gdrive_mcp.remove(agent_keys=["claude"]) is False)


# ---------------------------------------------------------------------------
print("[5] coexistence -- every other server survives inject AND remove")
_reset_ledger()
_claude_path().write_text(json.dumps({
    "mcpServers": {
        "vibeflow": {"command": "node", "args": ["server.js"]},
        "linear": {"type": "http", "url": "https://mcp.linear.app/mcp",
                   "x-agentdeck-managed": True},
        "supabase": {"type": "http", "url": "https://mcp.supabase.com/mcp?read_only=true",
                     "x-agentdeck-managed": True},
        "github": {"command": "github-mcp-server", "args": ["stdio"]},
    },
    "projects": {"E:\\\\some\\\\repo": {"allowedTools": ["Bash"]}},
}), encoding="utf-8")

gdrive_mcp.inject(settings=_SETTINGS, agent_keys=["claude"])
cfg = _read(_claude_path())
servers = cfg["mcpServers"]
for name in ("vibeflow", "linear", "supabase", "github"):
    check(f"{name} survives inject", name in servers)
check("gdrive added alongside", "gdrive" in servers)
check("projects untouched", cfg["projects"]["E:\\\\some\\\\repo"]["allowedTools"] == ["Bash"])

gdrive_mcp.remove(agent_keys=["claude"])
servers = _read(_claude_path())["mcpServers"]
for name in ("vibeflow", "linear", "supabase", "github"):
    check(f"{name} survives remove", name in servers)
check("only gdrive removed", "gdrive" not in servers)


# ---------------------------------------------------------------------------
print("[6] refuses a hand-rolled gdrive server")
_reset_ledger()
_claude_path().write_text(json.dumps({
    "mcpServers": {"gdrive": {"type": "http", "url": "https://example.invalid/mine"}}
}), encoding="utf-8")

check("foreign server detected", gdrive_mcp.foreign_server_agents(agent_keys=["claude"]) == ["claude"])
check("inject declines", gdrive_mcp.inject(settings=_SETTINGS, agent_keys=["claude"]) is False)
check("user's server untouched",
      _root_gdrive(_read(_claude_path()))["url"] == "https://example.invalid/mine")
check("seeded=True claims it (post-seed path)",
      gdrive_mcp.inject(settings=_SETTINGS, agent_keys=["claude"], seeded=True) is True)
check("now ours", _root_gdrive(_read(_claude_path()))["x-agentdeck-managed"] is True)

_reset_ledger()
_claude_path().unlink(missing_ok=True)
check("no foreign server on a clean config",
      gdrive_mcp.foreign_server_agents(agent_keys=["claude"]) == [])
gdrive_mcp.inject(settings=_SETTINGS, agent_keys=["claude"])
check("our own server is not 'foreign'",
      gdrive_mcp.foreign_server_agents(agent_keys=["claude"]) == [])
gdrive_mcp.remove(agent_keys=["claude"])


# ---------------------------------------------------------------------------
print("[7] unsupported agents are never written")
_reset_ledger()
for key in ("opencode", "codex", "gemini", "goose", "aider"):
    ext = "toml" if key == "codex" else ("yaml" if key == "goose" else "json")
    path = Path(_SANDBOX) / f"{key}.{ext}"
    path.unlink(missing_ok=True)
    check(f"{key}: inject no-op", gdrive_mcp.inject(settings=_SETTINGS, agent_keys=[key]) is False)
    check(f"{key}: no config file created", not path.exists())

check("mixed agent list still writes claude only",
      gdrive_mcp.inject(settings=_SETTINGS, agent_keys=["claude", "opencode", "codex"]) is True)
check("opencode untouched", not (Path(_SANDBOX) / "opencode.json").exists())
check("codex untouched", not (Path(_SANDBOX) / "codex.toml").exists())
gdrive_mcp.remove(agent_keys=["claude"])


# ---------------------------------------------------------------------------
print("[8] seed_secret -- secret travels in the env, never in argv")
_probe = Path(_SANDBOX) / "fake_claude.py"
_probe.write_text(
    "import json, os, sys\n"
    "json.dump({'argv': sys.argv[1:], 'secret': os.environ.get('MCP_CLIENT_SECRET'),\n"
    "           'claude_config_dir': os.environ.get('CLAUDE_CONFIG_DIR')},\n"
    "          open(os.environ['PROBE_OUT'], 'w'))\n"
    "print('Added HTTP MCP server gdrive')\n",
    encoding="utf-8",
)
_probe_out = Path(_SANDBOX) / "probe.json"
os.environ["PROBE_OUT"] = str(_probe_out)

# a tiny shim so `claude_bin` is a real executable taking `mcp add ...`
_shim = Path(_SANDBOX) / ("shim.cmd" if os.name == "nt" else "shim.sh")
if os.name == "nt":
    _shim.write_text(f'@echo off\r\n"{sys.executable}" "{_probe}" %*\r\n', encoding="utf-8")
else:
    _shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_probe}" "$@"\n', encoding="utf-8")
    _shim.chmod(_shim.stat().st_mode | stat.S_IEXEC)

ok, message = gdrive_mcp.seed_secret(_CLIENT_ID, "SUPER-SECRET", claude_bin=str(_shim))
check("seed_secret reports success", ok is True)
check("no error message on success", message == "")
probe = json.loads(_probe_out.read_text(encoding="utf-8"))
check("secret reached the child env", probe["secret"] == "SUPER-SECRET")
check("secret NOT in argv", "SUPER-SECRET" not in " ".join(probe["argv"]))
check("--client-secret flag passed", "--client-secret" in probe["argv"])
check("client id passed in argv", _CLIENT_ID in probe["argv"])
check("callback port passed", "8976" in probe["argv"])
check("scope user", "user" in probe["argv"])

# a binary that prints nothing must be treated as a failure, with the manual command
_quiet = Path(_SANDBOX) / ("quiet.cmd" if os.name == "nt" else "quiet.sh")
if os.name == "nt":
    _quiet.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
else:
    _quiet.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    _quiet.chmod(_quiet.stat().st_mode | stat.S_IEXEC)
ok, message = gdrive_mcp.seed_secret(_CLIENT_ID, "S", claude_bin=str(_quiet))
check("silent 'success' treated as failure", ok is False)
check("failure message has the manual command", "claude mcp add" in message)
check("manual command carries no real secret", "SUPER-SECRET" not in message)

ok, message = gdrive_mcp.seed_secret(_CLIENT_ID, "S", claude_bin=str(Path(_SANDBOX) / "nope.exe"))
check("missing binary is a clean failure", ok is False and "claude mcp add" in message)


# ---------------------------------------------------------------------------
print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
