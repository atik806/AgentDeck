"""Offline tests for vercel_mcp.py -- the ~/.claude.json injector.

    .venv\\Scripts\\python.exe test_vercel_mcp.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="adk-vercelmcp-")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

import vercel_mcp


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


def _root_vercel(cfg):
    return (cfg.get("mcpServers") or {}).get("vercel")


# ---------------------------------------------------------------------------
print("[1] supports_agent -- tokenless OAuth server: only OAUTH_ALLOWLIST agents")
check("claude supported", vercel_mcp.supports_agent("claude"))
check("claude with args supported", vercel_mcp.supports_agent("claude --dangerously-skip-permissions"))
check("opencode supported (phase 3)", vercel_mcp.supports_agent("opencode"))
check("codex not yet supported (phased OAuth rollout)", not vercel_mcp.supports_agent("codex"))
check("gemini not yet supported", not vercel_mcp.supports_agent("gemini"))
check("aider not supported", not vercel_mcp.supports_agent("aider"))
check("plain shell not supported", not vercel_mcp.supports_agent(""))


# ---------------------------------------------------------------------------
print("[2] mcp_server_config -- tokenless remote OAuth server")
cfg = vercel_mcp.mcp_server_config()
check("type http", cfg["type"] == "http")
check("points at the hosted MCP", cfg["url"] == "https://mcp.vercel.com")
check("marked managed", cfg["x-agentdeck-managed"] is True)
check("NO headers block", "headers" not in cfg)
check("no token anywhere", "vercel_tok" not in json.dumps(cfg) and "Bearer" not in json.dumps(cfg))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[3] inject / remove round-trip -- user scope, folder-independent")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"

    changed = vercel_mcp.inject(agent_command="claude", claude_config=cc)
    check("inject reports a change", changed)
    srv = _root_vercel(_read(cc))
    check("vercel server at the ROOT mcpServers (user scope)", srv is not None)
    check("not stashed under projects", "vercel" not in str(_read(cc).get("projects", {})))
    check("url in place", srv["url"] == "https://mcp.vercel.com")

    check("re-inject is a no-op", not vercel_mcp.inject(claude_config=cc))

    check("remove reports a change", vercel_mcp.remove(claude_config=cc))
    check("vercel gone", _root_vercel(_read(cc)) is None)
    check("remove idempotent", not vercel_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[4] inject preserves the rest of ~/.claude.json (coexists with github)")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({
        "numStartups": 7,
        "mcpServers": {
            "vibeflow": {"command": "vibeflow-mcp"},
            "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/",
                       "headers": {"Authorization": "Bearer gho_x"},
                       "x-agentdeck-managed": True},
        },
        "projects": {"E:\\x": {"hasTrustDialogAccepted": True}},
    }), encoding="utf-8")
    vercel_mcp.inject(claude_config=cc)
    cfg = _read(cc)
    check("root key preserved", cfg["numStartups"] == 7)
    check("vibeflow server preserved", "vibeflow" in cfg["mcpServers"])
    check("github server preserved -- both plugins coexist", "github" in cfg["mcpServers"])
    check("projects preserved", cfg["projects"]["E:\\x"]["hasTrustDialogAccepted"] is True)
    check("vercel added alongside", "vercel" in cfg["mcpServers"])

    vercel_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("vercel removed", "vercel" not in cfg["mcpServers"])
    check("github left alone", "github" in cfg["mcpServers"])
    check("vibeflow still there", "vibeflow" in cfg["mcpServers"])


# ---------------------------------------------------------------------------
_reset_ledger()
print("[5] inject refuses a hand-rolled root vercel server")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({"mcpServers": {"vercel": {"command": "my-own-vercel-mcp"}}}), encoding="utf-8")
    check("inject declines", not vercel_mcp.inject(claude_config=cc))
    check("their config untouched", _read(cc)["mcpServers"]["vercel"]["command"] == "my-own-vercel-mcp")
    check("remove declines too", not vercel_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[6] unsupported agent / project-scope sweep")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    check("codex agent -> no-op", not vercel_mcp.inject(agent_command="codex", claude_config=cc))
    check("no file written", not cc.exists())

    # remove() also sweeps a stale per-project entry
    cc.write_text(json.dumps({
        "mcpServers": {"vercel": {"type": "http", "url": "https://mcp.vercel.com",
                                  "x-agentdeck-managed": True}},
        "projects": {"E:\\old": {"mcpServers": {"vercel": {"x-agentdeck-managed": True}}}},
    }), encoding="utf-8")
    vercel_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("root vercel removed", "vercel" not in cfg["mcpServers"])
    check("stale project-scope vercel also removed",
          "vercel" not in (cfg["projects"]["E:\\old"].get("mcpServers") or {}))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[7] opencode -- tokenless remote server in ~/.config/opencode/opencode.json")
_oc = Path(_SANDBOX) / "opencode.json"      # ADK_MCP_CONFIG_DIR redirects here
_oc.unlink(missing_ok=True)
check("inject writes opencode's config", vercel_mcp.inject(agent_keys=["opencode"]))
_ocfg = _read(_oc)
srv = (_ocfg.get("mcp") or {}).get("vercel")
check("server under the 'mcp' key (opencode's map)", srv is not None)
check("type remote + enabled + tokenless", srv["type"] == "remote"
      and srv["enabled"] is True and "headers" not in srv
      and srv["url"] == "https://mcp.vercel.com")
check("re-inject is a no-op", not vercel_mcp.inject(agent_keys=["opencode"]))
check("remove reports a change", vercel_mcp.remove(agent_keys=["opencode"]))
check("server gone", (_read(_oc).get("mcp") or {}).get("vercel") is None)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
