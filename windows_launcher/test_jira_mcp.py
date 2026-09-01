"""Offline tests for jira_mcp.py -- the ~/.claude.json injector.

    .venv\\Scripts\\python.exe test_jira_mcp.py
"""

import json
import sys
import tempfile
from pathlib import Path

import jira_mcp

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


def _root_atlassian(cfg):
    return (cfg.get("mcpServers") or {}).get("atlassian")


# ---------------------------------------------------------------------------
print("[1] supports_agent")
check("claude supported", jira_mcp.supports_agent("claude"))
check("claude with args supported", jira_mcp.supports_agent("claude --dangerously-skip-permissions"))
check("codex not supported (v1)", not jira_mcp.supports_agent("codex"))
check("plain shell not supported", not jira_mcp.supports_agent(""))


# ---------------------------------------------------------------------------
print("[2] mcp_server_config -- tokenless remote OAuth server")
cfg = jira_mcp.mcp_server_config()
check("type http", cfg["type"] == "http")
check("points at the Atlassian hosted MCP", cfg["url"] == "https://mcp.atlassian.com/v1/mcp/authv2")
check("marked managed", cfg["x-agentdeck-managed"] is True)
check("NO headers block", "headers" not in cfg)
check("no token anywhere", "Bearer" not in json.dumps(cfg) and "Basic" not in json.dumps(cfg))


# ---------------------------------------------------------------------------
print("[3] inject / remove round-trip -- user scope, server named 'atlassian'")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"

    changed = jira_mcp.inject(agent_command="claude", claude_config=cc)
    check("inject reports a change", changed)
    srv = _root_atlassian(_read(cc))
    check("atlassian server at the ROOT mcpServers (user scope)", srv is not None)
    check("not stashed under projects", "atlassian" not in str(_read(cc).get("projects", {})))
    check("url in place", srv["url"] == "https://mcp.atlassian.com/v1/mcp/authv2")

    check("re-inject is a no-op", not jira_mcp.inject(claude_config=cc))

    check("remove reports a change", jira_mcp.remove(claude_config=cc))
    check("atlassian gone", _root_atlassian(_read(cc)) is None)
    check("remove idempotent", not jira_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
print("[4] inject preserves the rest of ~/.claude.json (coexists with github + vercel)")
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
        },
        "projects": {"E:\\x": {"hasTrustDialogAccepted": True}},
    }), encoding="utf-8")
    jira_mcp.inject(claude_config=cc)
    cfg = _read(cc)
    check("root key preserved", cfg["numStartups"] == 7)
    check("vibeflow server preserved", "vibeflow" in cfg["mcpServers"])
    check("github server preserved", "github" in cfg["mcpServers"])
    check("vercel server preserved -- all three plugins coexist", "vercel" in cfg["mcpServers"])
    check("projects preserved", cfg["projects"]["E:\\x"]["hasTrustDialogAccepted"] is True)
    check("atlassian added alongside", "atlassian" in cfg["mcpServers"])

    jira_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("atlassian removed", "atlassian" not in cfg["mcpServers"])
    check("github left alone", "github" in cfg["mcpServers"])
    check("vercel left alone", "vercel" in cfg["mcpServers"])


# ---------------------------------------------------------------------------
print("[5] inject refuses a hand-rolled root atlassian server")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({"mcpServers": {"atlassian": {"command": "my-own-atlassian-mcp"}}}), encoding="utf-8")
    check("inject declines", not jira_mcp.inject(claude_config=cc))
    check("their config untouched", _read(cc)["mcpServers"]["atlassian"]["command"] == "my-own-atlassian-mcp")
    check("remove declines too", not jira_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
print("[6] unsupported agent / project-scope sweep")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    check("codex agent -> no-op", not jira_mcp.inject(agent_command="codex", claude_config=cc))
    check("no file written", not cc.exists())

    cc.write_text(json.dumps({
        "mcpServers": {"atlassian": {"type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2",
                                     "x-agentdeck-managed": True}},
        "projects": {"E:\\old": {"mcpServers": {"atlassian": {"x-agentdeck-managed": True}}}},
    }), encoding="utf-8")
    jira_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("root atlassian removed", "atlassian" not in cfg["mcpServers"])
    check("stale project-scope atlassian also removed",
          "atlassian" not in (cfg["projects"]["E:\\old"].get("mcpServers") or {}))


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
