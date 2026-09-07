"""Offline tests for linear_mcp.py -- the ~/.claude.json injector.

    .venv\\Scripts\\python.exe test_linear_mcp.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="adk-linearmcp-")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

import linear_mcp


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


def _root_linear(cfg):
    return (cfg.get("mcpServers") or {}).get("linear")


# ---------------------------------------------------------------------------
print("[1] supports_agent -- tokenless OAuth server: every MCP-capable agent")
check("claude supported", linear_mcp.supports_agent("claude"))
check("claude with args supported", linear_mcp.supports_agent("claude --dangerously-skip-permissions"))
check("opencode supported", linear_mcp.supports_agent("opencode"))
check("codex supported (all MCP agents wired)", linear_mcp.supports_agent("codex"))
check("aider not supported", not linear_mcp.supports_agent("aider"))
check("plain shell not supported", not linear_mcp.supports_agent(""))


# ---------------------------------------------------------------------------
print("[2] mcp_server_config -- tokenless remote OAuth server")
cfg = linear_mcp.mcp_server_config()
check("type http", cfg["type"] == "http")
check("points at the Linear hosted MCP", cfg["url"] == "https://mcp.linear.app/mcp")
check("marked managed", cfg["x-agentdeck-managed"] is True)
check("NO headers block", "headers" not in cfg)
check("no token anywhere", "Bearer" not in json.dumps(cfg) and "Basic" not in json.dumps(cfg))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[3] inject / remove round-trip -- user scope, server named 'linear'")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"

    changed = linear_mcp.inject(agent_command="claude", claude_config=cc)
    check("inject reports a change", changed)
    srv = _root_linear(_read(cc))
    check("linear server at the ROOT mcpServers (user scope)", srv is not None)
    check("not stashed under projects", "linear" not in str(_read(cc).get("projects", {})))
    check("url in place", srv["url"] == "https://mcp.linear.app/mcp")

    check("re-inject is a no-op", not linear_mcp.inject(claude_config=cc))

    check("remove reports a change", linear_mcp.remove(claude_config=cc))
    check("linear gone", _root_linear(_read(cc)) is None)
    check("remove idempotent", not linear_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[4] inject preserves the rest of ~/.claude.json (coexists with github + vercel + gitlab)")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({
        "numStartups": 7,
        "mcpServers": {
            "vibeflow": {"command": "vibeflow-mcp"},
            "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/",
                       "headers": {"Authorization": "Bearer gho_x"},
                       "x-agentdeck-managed": True},
            "vercel": {"type": "http", "url": "https://mcp.vercel.com",
                       "x-agentdeck-managed": True},
            "gitlab": {"type": "http", "url": "https://gitlab.com/api/v4/mcp",
                       "x-agentdeck-managed": True},
        },
        "projects": {"E:\\x": {"hasTrustDialogAccepted": True}},
    }), encoding="utf-8")
    linear_mcp.inject(claude_config=cc)
    cfg = _read(cc)
    check("root key preserved", cfg["numStartups"] == 7)
    check("vibeflow server preserved", "vibeflow" in cfg["mcpServers"])
    check("github server preserved", "github" in cfg["mcpServers"])
    check("vercel server preserved", "vercel" in cfg["mcpServers"])
    check("gitlab server preserved -- all plugins coexist", "gitlab" in cfg["mcpServers"])
    check("projects preserved", cfg["projects"]["E:\\x"]["hasTrustDialogAccepted"] is True)
    check("linear added alongside", "linear" in cfg["mcpServers"])

    linear_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("linear removed", "linear" not in cfg["mcpServers"])
    check("github left alone", "github" in cfg["mcpServers"])
    check("vercel left alone", "vercel" in cfg["mcpServers"])
    check("gitlab left alone", "gitlab" in cfg["mcpServers"])


# ---------------------------------------------------------------------------
_reset_ledger()
print("[5] inject refuses a hand-rolled root linear server")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({"mcpServers": {"linear": {"command": "my-own-linear-mcp"}}}), encoding="utf-8")
    check("inject declines", not linear_mcp.inject(claude_config=cc))
    check("their config untouched", _read(cc)["mcpServers"]["linear"]["command"] == "my-own-linear-mcp")
    check("remove declines too", not linear_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[6] unsupported agent / project-scope sweep")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    check("aider agent -> no-op (no MCP support)", not linear_mcp.inject(agent_command="aider", claude_config=cc))
    check("no file written", not cc.exists())

    cc.write_text(json.dumps({
        "mcpServers": {"linear": {"type": "http", "url": "https://mcp.linear.app/mcp",
                                     "x-agentdeck-managed": True}},
        "projects": {"E:\\old": {"mcpServers": {"linear": {"x-agentdeck-managed": True}}}},
    }), encoding="utf-8")
    linear_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("root linear removed", "linear" not in cfg["mcpServers"])
    check("stale project-scope linear also removed",
          "linear" not in (cfg["projects"]["E:\\old"].get("mcpServers") or {}))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[7] opencode -- tokenless 'linear' server in opencode.json")
_oc = Path(_SANDBOX) / "opencode.json"      # ADK_MCP_CONFIG_DIR redirects here
_oc.unlink(missing_ok=True)
check("inject writes opencode's config", linear_mcp.inject(agent_keys=["opencode"]))
_ocfg = _read(_oc)
srv = (_ocfg.get("mcp") or {}).get("linear")
check("server under the 'mcp' key as 'linear'", srv is not None)
check("type remote + enabled + tokenless", srv["type"] == "remote"
      and srv["enabled"] is True and "headers" not in srv
      and srv["url"] == "https://mcp.linear.app/mcp")
check("re-inject is a no-op", not linear_mcp.inject(agent_keys=["opencode"]))
check("remove reports a change", linear_mcp.remove(agent_keys=["opencode"]))
check("server gone", (_read(_oc).get("mcp") or {}).get("linear") is None)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
