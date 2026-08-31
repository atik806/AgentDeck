"""Offline tests for github_mcp.py -- the ~/.claude.json injector + review brief.

    .venv\\Scripts\\python.exe test_github_mcp.py
"""

import json
import sys
import tempfile
from pathlib import Path

import github_mcp
from plugin_store import PluginConnection, GITHUB

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


# ---------------------------------------------------------------------------
print("[1] supports_agent")
check("claude supported", github_mcp.supports_agent("claude"))
check("claude with args supported", github_mcp.supports_agent('claude --dangerously-skip-permissions'))
check("codex not supported (v1)", not github_mcp.supports_agent("codex"))
check("plain shell not supported", not github_mcp.supports_agent(""))


# ---------------------------------------------------------------------------
print("[2] mcp_server_config")
remote = github_mcp.mcp_server_config("gho_tok", toolsets=["repos", "pull_requests"])
check("remote type http", remote["type"] == "http")
check("bearer header carries the token", remote["headers"]["Authorization"] == "Bearer gho_tok")
check("toolsets header set", remote["headers"]["X-MCP-Toolsets"] == "repos,pull_requests")
check("marked managed", remote["x-agentdeck-managed"] is True)
ro = github_mcp.mcp_server_config("t", toolsets=["repos"], read_only=True)
check("read-only header", ro["headers"].get("X-MCP-Readonly") == "true")
local = github_mcp.mcp_server_config("t", transport="local", toolsets=["actions"])
check("local transport uses a command", "command" in local and local["args"] == ["stdio"])
check("local passes token via env", local["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "t")


def _root_gh(cfg):
    return (cfg.get("mcpServers") or {}).get("github")


# ---------------------------------------------------------------------------
print("[3] inject / remove round-trip -- user scope, no repo file, folder-independent")
conn = PluginConnection(GITHUB, login="atik806", capabilities=["review"])
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    repo = Path(d) / "repo"
    repo.mkdir()

    changed = github_mcp.inject(repo, "gho_tok", conn, agent_command="claude", claude_config=cc)
    check("inject reports a change", changed)
    check("NO .mcp.json written into the folder", not (repo / ".mcp.json").exists())
    srv = _root_gh(_read(cc))
    check("github server at the ROOT mcpServers (user scope)", srv is not None)
    check("not stashed under projects", "github" not in str(_read(cc).get("projects", {})))
    check("token in place", srv["headers"]["Authorization"] == "Bearer gho_tok")

    check("folder is irrelevant -- inject(None) also works",
          github_mcp.inject(None, "gho_tok3", conn, claude_config=cc))
    check("re-inject same token is a no-op",
          not github_mcp.inject(None, "gho_tok3", conn, claude_config=cc))

    check("remove reports a change", github_mcp.remove(claude_config=cc))
    check("github gone", _root_gh(_read(cc)) is None)
    check("remove idempotent", not github_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
print("[4] inject preserves the rest of ~/.claude.json")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({
        "numStartups": 7,
        "mcpServers": {"vibeflow": {"command": "vibeflow-mcp"}},
        "projects": {"E:\\x": {"hasTrustDialogAccepted": True}},
    }), encoding="utf-8")
    github_mcp.inject(None, "gho_tok", conn, claude_config=cc)
    cfg = _read(cc)
    check("root key preserved", cfg["numStartups"] == 7)
    check("other root server preserved", "vibeflow" in cfg["mcpServers"])
    check("projects preserved", cfg["projects"]["E:\\x"]["hasTrustDialogAccepted"] is True)
    check("github added alongside vibeflow", "github" in cfg["mcpServers"])

    github_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("github removed", "github" not in cfg["mcpServers"])
    check("vibeflow still there", "vibeflow" in cfg["mcpServers"])


# ---------------------------------------------------------------------------
print("[5] inject refuses a hand-rolled root github server")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({"mcpServers": {"github": {"command": "my-own-github-mcp"}}}), encoding="utf-8")
    check("inject declines", not github_mcp.inject(None, "gho_tok", conn, claude_config=cc))
    check("their config untouched", _read(cc)["mcpServers"]["github"]["command"] == "my-own-github-mcp")
    check("remove declines too", not github_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
print("[6] unsupported agent / legacy .mcp.json cleanup / project-scope sweep")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    check("codex agent -> no-op", not github_mcp.inject(d, "t", conn, agent_command="codex", claude_config=cc))
    check("empty token -> no-op", not github_mcp.inject(d, "", conn, agent_command="claude", claude_config=cc))

    repo = Path(d) / "repo"
    repo.mkdir()
    # a token-bearing .mcp.json left by an older AgentDeck build
    (repo / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"github": {"type": "http", "url": "x",
                                  "headers": {"Authorization": "Bearer ghu_leak"},
                                  "x-agentdeck-managed": True}},
    }), encoding="utf-8")
    github_mcp.inject(repo, "gho_new", conn, agent_command="claude", claude_config=cc)
    check("legacy .mcp.json with our token is deleted on inject", not (repo / ".mcp.json").exists())
    check("token now at user scope instead", _root_gh(_read(cc))["headers"]["Authorization"] == "Bearer gho_new")

    # remove() also sweeps a stale per-project entry an older build wrote
    cfg = _read(cc)
    cfg.setdefault("projects", {})["E:\\old"] = {"mcpServers": {"github": {"x-agentdeck-managed": True}}}
    cc.write_text(json.dumps(cfg), encoding="utf-8")
    github_mcp.remove(claude_config=cc)
    cfg = _read(cc)
    check("stale project-scope github also removed",
          "github" not in (cfg["projects"]["E:\\old"].get("mcpServers") or {}))

    # a hand-rolled .mcp.json is left alone
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"github": {"command": "mine"}}}), encoding="utf-8")
    check("cleanup_legacy_mcp_json spares a user file", not github_mcp.cleanup_legacy_mcp_json(repo))
    check("user's .mcp.json still there", (repo / ".mcp.json").exists())


# ---------------------------------------------------------------------------
print("[7] review brief + startup command")
brief = github_mcp.build_review_brief("atik806/AgentDeck", 123, {"post": True, "event": "comment", "focus": ["bugs", "security"]})
check("names the repo and PR", "atik806/AgentDeck#123" in brief)
check("mentions posting a review", "post the review to GitHub" in brief)
check("event COMMENT", "event `COMMENT`" in brief)
check("no-post brief says do not post", "Do NOT post" in github_mcp.build_review_brief("a/b", 1, {"post": False}))

with tempfile.TemporaryDirectory() as d:
    bp = github_mcp.write_review_brief(d, "atik806/AgentDeck", 7, {"post": False})
    check("brief written under .agentdeck/", bp.exists() and bp.parent.name == ".agentdeck")
    cmd = github_mcp.review_startup_command("claude", "atik806/AgentDeck", 7, bp)
    check("startup command runs claude with a task", cmd.startswith('claude "') and "#7" in cmd)
    check("points at the brief path", ".agentdeck/review-7.md" in cmd)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
