"""Offline tests for supabase_mcp.py -- the ~/.claude.json injector.

    .venv\\Scripts\\python.exe test_supabase_mcp.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="adk-supabasemcp-")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

import supabase_mcp


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


def _root_supabase(cfg):
    return (cfg.get("mcpServers") or {}).get("supabase")


# ---------------------------------------------------------------------------
print("[1] supports_agent -- tokenless OAuth server: every MCP-capable agent")
check("claude supported", supabase_mcp.supports_agent("claude"))
check("claude with args supported", supabase_mcp.supports_agent("claude --dangerously-skip-permissions"))
check("opencode supported", supabase_mcp.supports_agent("opencode"))
check("codex supported (all MCP agents wired)", supabase_mcp.supports_agent("codex"))
check("aider not supported", not supabase_mcp.supports_agent("aider"))
check("plain shell not supported", not supabase_mcp.supports_agent(""))


# ---------------------------------------------------------------------------
print("[2] canonical_server -- URL scoping from settings")
c = supabase_mcp.canonical_server({"project_ref": "abcdefghijklmnopqrst"})
check("defaults to read_only=true with no explicit setting",
      c["url"] == "https://mcp.supabase.com/mcp?project_ref=abcdefghijklmnopqrst&read_only=true")
check("oauth flagged", c["oauth"] is True)
check("transport http", c["transport"] == "http")

c2 = supabase_mcp.canonical_server({"project_ref": "abcxyz123", "read_only": "false"})
check("read_only can be turned off explicitly", "read_only=false" in c2["url"])

c3 = supabase_mcp.canonical_server({"project_ref": "abcxyz123", "features": "database,docs"})
check("features param appended", c3["url"].endswith("&features=database,docs"))

c4 = supabase_mcp.canonical_server({})
check("empty project_ref tolerated (no project_ref param, still read-only)",
      c4["url"] == "https://mcp.supabase.com/mcp?read_only=true")

c5 = supabase_mcp.canonical_server(None)
check("None settings -> same as empty dict", c5["url"] == c4["url"])


# ---------------------------------------------------------------------------
print("[3] mcp_server_config -- tokenless remote OAuth server, no headers/token")
cfg = supabase_mcp.mcp_server_config({"project_ref": "abcdefghijklmnopqrst"})
check("type http", cfg["type"] == "http")
check("points at the Supabase hosted MCP, scoped", cfg["url"].startswith("https://mcp.supabase.com/mcp?project_ref="))
check("marked managed", cfg["x-agentdeck-managed"] is True)
check("NO headers block", "headers" not in cfg)
check("no token anywhere", "Bearer" not in json.dumps(cfg) and "Basic" not in json.dumps(cfg))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[4] inject / remove round-trip -- user scope, server named 'supabase'")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    settings = {"project_ref": "abcdefghijklmnopqrst"}

    changed = supabase_mcp.inject(settings=settings, agent_command="claude", claude_config=cc)
    check("inject reports a change", changed)
    srv = _root_supabase(_read(cc))
    check("supabase server at the ROOT mcpServers (user scope)", srv is not None)
    check("not stashed under projects", "supabase" not in str(_read(cc).get("projects", {})))
    check("url scoped to the project + read-only",
          srv["url"] == "https://mcp.supabase.com/mcp?project_ref=abcdefghijklmnopqrst&read_only=true")

    check("re-inject with the same settings is a no-op",
          not supabase_mcp.inject(settings=settings, claude_config=cc))

    check("re-inject with a DIFFERENT project_ref overwrites the entry",
          supabase_mcp.inject(settings={"project_ref": "zzzzzzzzzzzzzzzzzzzz"}, claude_config=cc))
    check("url now points at the new project",
          "zzzzzzzzzzzzzzzzzzzz" in _root_supabase(_read(cc))["url"])

    check("remove reports a change", supabase_mcp.remove(claude_config=cc))
    check("supabase gone", _root_supabase(_read(cc)) is None)
    check("remove idempotent", not supabase_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[5] inject preserves the rest of ~/.claude.json (coexists with other plugins)")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({
        "numStartups": 7,
        "mcpServers": {
            "vibeflow": {"command": "vibeflow-mcp"},
            "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/",
                       "headers": {"Authorization": "Bearer gho_x"},
                       "x-agentdeck-managed": True},
            "gitlab": {"type": "http", "url": "https://gitlab.com/api/v4/mcp",
                       "x-agentdeck-managed": True},
        },
        "projects": {"E:\\x": {"hasTrustDialogAccepted": True}},
    }), encoding="utf-8")
    supabase_mcp.inject(settings={"project_ref": "abcdefghijklmnopqrst"}, claude_config=cc)
    cfg = _read(cc)
    check("root key preserved", cfg["numStartups"] == 7)
    check("vibeflow server preserved", "vibeflow" in cfg["mcpServers"])
    check("github server preserved", "github" in cfg["mcpServers"])
    check("gitlab server preserved -- all plugins coexist", "gitlab" in cfg["mcpServers"])
    check("projects preserved", cfg["projects"]["E:\\x"]["hasTrustDialogAccepted"] is True)
    check("supabase added alongside", "supabase" in cfg["mcpServers"])

    supabase_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("supabase removed", "supabase" not in cfg["mcpServers"])
    check("github left alone", "github" in cfg["mcpServers"])
    check("gitlab left alone", "gitlab" in cfg["mcpServers"])


# ---------------------------------------------------------------------------
_reset_ledger()
print("[6] inject refuses a hand-rolled root supabase server")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({"mcpServers": {"supabase": {"command": "my-own-supabase-mcp"}}}), encoding="utf-8")
    check("inject declines", not supabase_mcp.inject(settings={"project_ref": "abcdefghijklmnopqrst"}, claude_config=cc))
    check("their config untouched", _read(cc)["mcpServers"]["supabase"]["command"] == "my-own-supabase-mcp")
    check("remove declines too", not supabase_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[7] unsupported agent / project-scope sweep")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    check("aider agent -> no-op (no MCP support)",
          not supabase_mcp.inject(agent_command="aider", claude_config=cc))
    check("no file written", not cc.exists())

    cc.write_text(json.dumps({
        "mcpServers": {"supabase": {"type": "http",
                                     "url": "https://mcp.supabase.com/mcp?read_only=true",
                                     "x-agentdeck-managed": True}},
        "projects": {"E:\\old": {"mcpServers": {"supabase": {"x-agentdeck-managed": True}}}},
    }), encoding="utf-8")
    supabase_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("root supabase removed", "supabase" not in cfg["mcpServers"])
    check("stale project-scope supabase also removed",
          "supabase" not in (cfg["projects"]["E:\\old"].get("mcpServers") or {}))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[8] opencode -- tokenless 'supabase' server in opencode.json")
_oc = Path(_SANDBOX) / "opencode.json"      # ADK_MCP_CONFIG_DIR redirects here
_oc.unlink(missing_ok=True)
check("inject writes opencode's config",
      supabase_mcp.inject(settings={"project_ref": "abcdefghijklmnopqrst"}, agent_keys=["opencode"]))
_ocfg = _read(_oc)
srv = (_ocfg.get("mcp") or {}).get("supabase")
check("server under the 'mcp' key as 'supabase'", srv is not None)
check("type remote + enabled + tokenless + scoped", srv["type"] == "remote"
      and srv["enabled"] is True and "headers" not in srv
      and srv["url"] == "https://mcp.supabase.com/mcp?project_ref=abcdefghijklmnopqrst&read_only=true")
check("re-inject with same settings is a no-op",
      not supabase_mcp.inject(settings={"project_ref": "abcdefghijklmnopqrst"}, agent_keys=["opencode"]))
check("remove reports a change", supabase_mcp.remove(agent_keys=["opencode"]))
check("server gone", (_read(_oc).get("mcp") or {}).get("supabase") is None)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
