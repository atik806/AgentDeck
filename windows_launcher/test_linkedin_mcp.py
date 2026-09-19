"""Offline tests for linkedin_mcp.py -- the local (stdio) MCP injector.

    .venv\\Scripts\\python.exe test_linkedin_mcp.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="adk-linkedinmcp-")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

import linkedin_mcp
import mcp_targets

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


def _reset_ledger():
    Path(os.environ["ADK_MCP_STATE"]).unlink(missing_ok=True)


def _read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _root_linkedin(cfg):
    return (cfg.get("mcpServers") or {}).get("linkedin")


# ---------------------------------------------------------------------------
print("[1] supports_agent -- a local server, so no OAuth capability is needed")
check("claude supported", linkedin_mcp.supports_agent("claude"))
check("claude with args supported",
      linkedin_mcp.supports_agent("claude --dangerously-skip-permissions"))
check("codex supported", linkedin_mcp.supports_agent("codex"))
check("gemini supported", linkedin_mcp.supports_agent("gemini"))
check("copilot supported", linkedin_mcp.supports_agent("copilot"))
# Held back until each one's local-server shape is actually verified -- the
# same doctrine as docs/PLUGINS.md 18's static-OAuth hold-backs.
check("opencode held back", not linkedin_mcp.supports_agent("opencode"))
check("goose held back", not linkedin_mcp.supports_agent("goose"))
check("crush held back", not linkedin_mcp.supports_agent("crush"))
check("aider not supported", not linkedin_mcp.supports_agent("aider"))
check("plain shell not supported", not linkedin_mcp.supports_agent(""))


# ---------------------------------------------------------------------------
print("[2] canonical_server / launch_argv -- no credential, ever")
canonical = linkedin_mcp.canonical_server()
check("stdio transport", canonical["transport"] == "stdio")
check("command is this interpreter or exe", bool(canonical["command"]))
check("env is empty", canonical["env"] == {})
blob = json.dumps(canonical)
check("no token/cookie/secret anywhere in the spec",
      not any(word in blob.lower()
              for word in ("bearer", "li_at", "secret", "token", "apikey", "api_key")))

command, args = linkedin_mcp.launch_argv()
check("from source: runs linkedin_server.py unbuffered",
      args[0] == "-u" and args[1].endswith("linkedin_server.py"))
check("...and the file it names exists", Path(args[1]).exists())


# ---------------------------------------------------------------------------
print("[3] the frozen build re-enters the exe through the sentinel")
_was_frozen = getattr(sys, "frozen", None)
_exe = sys.executable
try:
    sys.frozen = True                       # type: ignore[attr-defined]
    sys.executable = r"C:\AgentDeck\current\AgentDeck.exe"
    fcommand, fargs = linkedin_mcp.launch_argv()
    check("command is the app itself", fcommand.endswith("AgentDeck.exe"))
    check("args are exactly the sentinel", fargs == [linkedin_mcp.SENTINEL])
    check("sentinel is what main.py checks for", linkedin_mcp.SENTINEL == "--linkedin-mcp")
    main_py = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
    check("main.py checks the sentinel BEFORE importing Qt (REGRESSION)",
          main_py.index('"--linkedin-mcp"') < main_py.index("from PySide6"))
finally:
    sys.executable = _exe
    if _was_frozen is None:
        delattr(sys, "frozen")
    else:
        sys.frozen = _was_frozen            # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
print("[4] mcp_server_config -- Claude Code's block")
cfg = linkedin_mcp.mcp_server_config()
check("has a command", bool(cfg.get("command")))
check("has args", isinstance(cfg.get("args"), list))
check("marked managed", cfg["x-agentdeck-managed"] is True)
check("no url -- this is a local server", "url" not in cfg)
check("no headers", "headers" not in cfg)


# ---------------------------------------------------------------------------
_reset_ledger()
print("[5] inject / remove round-trip -- user scope, server named 'linkedin'")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"

    check("inject reports a change", linkedin_mcp.inject(agent_command="claude", claude_config=cc))
    entry = _root_linkedin(_read(cc))
    check("server written at the root", entry is not None)
    check("it launches our server", "linkedin_server.py" in " ".join(entry["args"])
          or entry["args"] == [linkedin_mcp.SENTINEL])
    check("re-inject is a no-op", not linkedin_mcp.inject(agent_command="claude", claude_config=cc))
    check("remove reports a change", linkedin_mcp.remove(claude_config=cc))
    check("server gone", _root_linkedin(_read(cc)) is None)
    check("removing again is a no-op", not linkedin_mcp.remove(claude_config=cc))


# ---------------------------------------------------------------------------
_reset_ledger()
print("[6] a 'linkedin' server the USER wrote is never touched")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    mine = {"command": "my-own-linkedin-server", "args": ["--serve"]}
    cc.write_text(json.dumps({"mcpServers": {"linkedin": mine}}), encoding="utf-8")

    check("inject refuses", not linkedin_mcp.inject(agent_command="claude", claude_config=cc))
    check("their entry is intact", _root_linkedin(_read(cc)) == mine)
    check("remove refuses too", not linkedin_mcp.remove(claude_config=cc))
    check("still intact", _root_linkedin(_read(cc)) == mine)


# ---------------------------------------------------------------------------
_reset_ledger()
print("[7] several agents at once -- each in its own format")
_gemini = Path(_SANDBOX) / "gemini.json"        # ADK_MCP_CONFIG_DIR redirects here
_codex = Path(_SANDBOX) / "codex.toml"
for p in (_gemini, _codex):
    p.unlink(missing_ok=True)

check("inject writes both", linkedin_mcp.inject(agent_keys=["gemini", "codex", "opencode"]))
gem = (_read(_gemini).get("mcpServers") or {}).get("linkedin")
check("gemini: command + args + env", gem is not None and "command" in gem and "args" in gem)
check("gemini: no url", "url" not in gem)
codex_text = _codex.read_text(encoding="utf-8")
check("codex: a [mcp_servers.linkedin] table", "mcp_servers" in codex_text and "linkedin" in codex_text)
check("codex: toml-safe managed key", "x_agentdeck_managed" in codex_text)
check("opencode is skipped, not half-written",
      not (Path(_SANDBOX) / "opencode.json").exists())
check("remove cleans both up", linkedin_mcp.remove(agent_keys=["gemini", "codex"]))
check("gemini entry gone", (_read(_gemini).get("mcpServers") or {}).get("linkedin") is None)


# ---------------------------------------------------------------------------
_reset_ledger()
print("[8] a moved install rewrites the entry (that's what Re-sync is for)")
with tempfile.TemporaryDirectory() as d:
    cc = Path(d) / ".claude.json"
    linkedin_mcp.inject(agent_keys=["claude"], claude_config=cc)
    stale = _read(cc)
    stale["mcpServers"]["linkedin"]["command"] = r"C:\Old\Location\AgentDeck.exe"
    cc.write_text(json.dumps(stale), encoding="utf-8")

    check("inject rewrites a stale command", linkedin_mcp.inject(agent_keys=["claude"], claude_config=cc))
    check("...to this install's", _root_linkedin(_read(cc))["command"] != r"C:\Old\Location\AgentDeck.exe")


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
