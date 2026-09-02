"""Offline tests for mcp_targets.py -- the per-agent MCP config adapters + ledger.

    .venv\\Scripts\\python.exe test_mcp_targets.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import mcp_io
import mcp_targets
from mcp_targets import McpLedger

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


GH = {"transport": "http", "url": "https://api.githubcopilot.com/mcp/",
      "headers": {"Authorization": "Bearer gho_x", "X-MCP-Toolsets": "repos"},
      "bearer": "gho_x", "oauth": False}
OAUTH = {"transport": "http", "url": "https://mcp.vercel.com", "oauth": True}


# ---------------------------------------------------------------------------
print("[1] registry + capabilities")
keys = mcp_targets.all_keys()
check("11 supported agents", len(keys) == 11)
check("aider is absent", "aider" not in keys)
check("caps(aider) all-False", mcp_targets.caps("aider") ==
      {"mcp": False, "mcp_remote_headers": False, "mcp_oauth": False, "format": None})
check("caps(unknown) all-False", not mcp_targets.caps("nope")["mcp"])
check("every agent can bear a remote token (GitHub)",
      all(mcp_targets.caps(k)["mcp_remote_headers"] for k in keys))
check("OAuth-capable set is claude + opencode (phased rollout)",
      [k for k in keys if mcp_targets.caps(k)["mcp_oauth"]] == ["claude", "opencode"])
check("a non-allowlisted agent stays OAuth-incapable", not mcp_targets.caps("codex")["mcp_oauth"]
      and not mcp_targets.caps("gemini")["mcp_oauth"])
check("codex format toml, goose yaml", mcp_targets.caps("codex")["format"] == "toml"
      and mcp_targets.caps("goose")["format"] == "yaml")


# ---------------------------------------------------------------------------
print("[2] path() honours ~ and each agent's env override")
with tempfile.TemporaryDirectory() as home:
    old = dict(os.environ)
    try:
        os.environ.pop("ADK_MCP_CONFIG_DIR", None)
        for var in ("CODEX_HOME", "COPILOT_HOME", "AMP_SETTINGS_FILE", "XDG_CONFIG_HOME"):
            os.environ.pop(var, None)
        # HOME-relative
        import importlib
        # patch Path.home via env is unreliable; just check the shape
        p = mcp_targets.target("claude").path()
        check("claude path ends with .claude.json", p.name == ".claude.json")
        p = mcp_targets.target("codex").path()
        check("codex path is ~/.codex/config.toml", p.parent.name == ".codex" and p.name == "config.toml")

        os.environ["CODEX_HOME"] = str(Path(home) / "cx")
        check("CODEX_HOME override respected",
              str(mcp_targets.target("codex").path()).startswith(str(Path(home) / "cx")))
        os.environ["AMP_SETTINGS_FILE"] = str(Path(home) / "amp.json")
        check("AMP_SETTINGS_FILE override respected",
              mcp_targets.target("amp").path() == Path(home) / "amp.json")
        os.environ["XDG_CONFIG_HOME"] = str(Path(home) / "xdg")
        check("XDG_CONFIG_HOME override respected (opencode)",
              str(mcp_targets.target("opencode").path()).startswith(str(Path(home) / "xdg")))
    finally:
        os.environ.clear()
        os.environ.update(old)


# ---------------------------------------------------------------------------
print("[3] render_entry -- each agent's own field names")
def r(key, canonical=GH):
    return mcp_targets.render_entry(mcp_targets.target(key), "github", canonical)

check("claude: type http + Authorization header", r("claude")["type"] == "http"
      and r("claude")["headers"]["Authorization"] == "Bearer gho_x")
check("gemini: httpUrl, no url, no type", "httpUrl" in r("gemini")
      and "url" not in r("gemini") and "type" not in r("gemini"))
check("antigravity: serverUrl", r("antigravity")["serverUrl"] == GH["url"])
check("qwen: httpUrl (gemini fork)", "httpUrl" in r("qwen"))
check("opencode: type remote + enabled", r("opencode")["type"] == "remote"
      and r("opencode")["enabled"] is True)
_oc_oauth = r("opencode", OAUTH)
check("opencode: tokenless OAuth entry -- url + remote + enabled, no headers/bearer",
      _oc_oauth["url"] == OAUTH["url"] and _oc_oauth["type"] == "remote"
      and _oc_oauth["enabled"] is True and "headers" not in _oc_oauth
      and "bearer_token" not in _oc_oauth and _oc_oauth["x-agentdeck-managed"] is True)
check("crush: type http", r("crush")["type"] == "http")
check("copilot: type http + tools ['*']", r("copilot")["tools"] == ["*"])
codex = r("codex")
check("codex: bearer_token, no Authorization header, no type",
      codex["bearer_token"] == "gho_x" and "headers" not in codex and "type" not in codex)
check("codex: managed key is the toml-safe spelling",
      "x_agentdeck_managed" in codex and "x-agentdeck-managed" not in codex)
goose = r("goose")
check("goose: uri + type streamable_http + name + enabled + bundled",
      goose["uri"] == GH["url"] and goose["type"] == "streamable_http"
      and goose["name"] == "github" and goose["enabled"] is True and goose["bundled"] is False)
check("stdio spec only renders for claude", r("claude", {"transport": "stdio",
      "command": "x", "args": ["stdio"], "env": {}}) is not None
      and r("gemini", {"transport": "stdio", "command": "x", "args": [], "env": {}}) is None)


# ---------------------------------------------------------------------------
print("[4] write_server / remove_server -- markers, idempotency, user servers")
with tempfile.TemporaryDirectory() as d:
    gp = Path(d) / "gemini.json"
    tgt = mcp_targets.target("gemini")
    changed, _ = mcp_targets.write_server(tgt, "github", GH, path_override=gp)
    check("first write changes the file", changed and gp.exists())
    changed, _ = mcp_targets.write_server(tgt, "github", GH, path_override=gp)
    check("second identical write is a no-op", not changed)

    # a hand-rolled server of the same name is left alone
    data = json.loads(gp.read_text())
    data["mcpServers"]["github"] = {"httpUrl": "mine"}
    gp.write_text(json.dumps(data))
    changed, _ = mcp_targets.write_server(tgt, "github", GH, path_override=gp)
    check("refuses to clobber a user server", not changed
          and json.loads(gp.read_text())["mcpServers"]["github"] == {"httpUrl": "mine"})
    check("remove_server also spares it",
          not mcp_targets.remove_server(tgt, "github", path_override=gp))


# ---------------------------------------------------------------------------
print("[5] codex root flag -- added and removed only when we added it")
with tempfile.TemporaryDirectory() as d:
    cp = Path(d) / "config.toml"
    tgt = mcp_targets.target("codex")
    _c, wrote = mcp_targets.write_server(tgt, "github", GH, path_override=cp)
    check("we added experimental_use_rmcp_client", wrote
          and "experimental_use_rmcp_client = true" in cp.read_text())
    mcp_targets.remove_server(tgt, "github", path_override=cp, drop_root_extra=True)
    check("flag removed with the last managed server",
          "experimental_use_rmcp_client" not in cp.read_text())

    # if the user already had the flag, we must NOT strip it
    cp.write_text('experimental_use_rmcp_client = true\n')
    _c, wrote = mcp_targets.write_server(tgt, "github", GH, path_override=cp)
    check("we did not add it (already present)", not wrote)
    mcp_targets.remove_server(tgt, "github", path_override=cp, drop_root_extra=False)
    check("user's flag survives", "experimental_use_rmcp_client = true" in cp.read_text())


# ---------------------------------------------------------------------------
print("[6] McpLedger")
with tempfile.TemporaryDirectory() as d:
    led = McpLedger(Path(d) / "state.json")
    led.record("github", "claude", "github", wrote_root_extra=False)
    led.record("github", "codex", "github", wrote_root_extra=True)
    check("has() works", led.has("github", "claude") and led.has("github", "codex"))
    check("wrote_root_extra tracked per agent",
          not led.wrote_root_extra("github", "claude") and led.wrote_root_extra("github", "codex"))
    check("agents_for lists both", set(led.agents_for("github")) == {"claude", "codex"})
    led.forget("github", "claude")
    check("forget one agent", not led.has("github", "claude") and led.has("github", "codex"))
    led.forget("github")
    check("forget the provider", led.agents_for("github") == {})

    # backfill from an existing marked ~/.claude.json entry
    cc = Path(d) / ".claude.json"
    cc.write_text(json.dumps({"mcpServers": {"vercel": {"type": "http", "url": "u",
                  "x-agentdeck-managed": True}}}))
    led2 = McpLedger(Path(d) / "state2.json")
    led2.backfill_claude("vercel", "vercel", claude_path=cc)
    check("backfill seeds claude from a marked entry", led2.has("vercel", "claude"))
    led2.backfill_claude("vercel", "vercel", claude_path=cc)
    check("backfill is idempotent", list(led2.agents_for("vercel")) == ["claude"])


# ---------------------------------------------------------------------------
print("[7] oauth_hint")
check("claude -> /mcp", "/mcp" in mcp_targets.oauth_hint("claude", "vercel"))
check("codex -> codex mcp login", "codex mcp login vercel" in mcp_targets.oauth_hint("codex", "vercel"))
check("unknown -> generic", "authorisation prompt" in mcp_targets.oauth_hint("???", "x"))


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
