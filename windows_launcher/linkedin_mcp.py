"""Wire a "connected" LinkedIn plugin into the coding agent(s) running in a pane.

The first plugin whose MCP server is **local and ours**. Vercel, Jira, GitLab,
Linear and Supabase all point an agent at a vendor's hosted endpoint; Google
Drive points it at Google's. LinkedIn publishes no MCP server at all and gates
job data behind its partner program, so ``linkedin_server`` runs here instead
and this module writes a **stdio** entry that launches it.

Two things follow from that, both good:

* **No credential is ever written into an agent's config.** A hosted plugin has
  to hand the agent something (a token, an OAuth client); a local server reads
  its own vault in-process. The entry below is just a command line.
* **No OAuth capability is needed**, so this isn't limited to the agents that
  can run a DCR or static-client handshake. It reaches every agent whose
  *local-server* shape has been verified -- ``mcp_targets.caps()["mcp_stdio"]``,
  which today is Claude Code, Codex, Copilot CLI, Gemini, Qwen, Cursor Agent,
  Amp and Antigravity. opencode, Crush and Goose shape local servers
  differently and are held back until each is checked, exactly as §18 held back
  the static-OAuth agents rather than writing entries that fail to start.

**The frozen-executable trap.** In a PyInstaller build ``sys.executable`` is
``AgentDeck.exe``, not a Python interpreter, so the entry cannot be
``python -m linkedin_server``. It re-enters the app through an argv sentinel --
``AgentDeck.exe --linkedin-mcp`` -- which ``main.py`` checks before it imports
Qt. Change :data:`SENTINEL` here and you must change that check too.

Qt-free. See docs/PLUGINS.md 19.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

import mcp_targets
from mcp_targets import McpLedger
from plugin_store import LINKEDIN as _PROVIDER

__all__ = [
    "SENTINEL",
    "SERVER_NAME",
    "supports_agent",
    "canonical_server",
    "mcp_server_config",
    "launch_argv",
    "inject",
    "remove",
    "remove_all",
]

#: The argv flag a frozen AgentDeck.exe recognises as "be the MCP server".
#: Mirrored in ``main.py`` -- keep the two in step.
SENTINEL = "--linkedin-mcp"

SERVER_NAME = "linkedin"
_MANAGED = "x-agentdeck-managed"


def _key_of(agent: Optional[str]) -> str:
    if not agent:
        return ""
    if mcp_targets.target(agent) is not None:
        return agent.strip().lower()
    try:
        from agents import agent_key_for_command

        return agent_key_for_command(agent)
    except Exception:  # noqa: BLE001
        return ""


def supports_agent(agent: Optional[str]) -> bool:
    """True when this agent can run a local stdio MCP server in a shape we've
    verified. Accepts a key or a command string."""
    return bool(mcp_targets.caps(_key_of(agent)).get("mcp_stdio"))


def launch_argv() -> tuple:
    """``(command, args)`` that starts the MCP server on this install.

    Frozen: the app's own exe plus :data:`SENTINEL`. From source: the running
    interpreter and ``linkedin_server.py`` beside this file, unbuffered so the
    agent sees replies immediately rather than when a 8 KiB buffer fills.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, [SENTINEL]
    return sys.executable, ["-u", str(Path(__file__).resolve().with_name("linkedin_server.py"))]


def canonical_server() -> dict:
    """Transport-agnostic spec: a local stdio server, no credentials attached."""
    command, args = launch_argv()
    return {"transport": "stdio", "command": command, "args": args, "env": {}}


def mcp_server_config() -> dict:
    """The concrete ``mcpServers["linkedin"]`` block for **Claude Code**."""
    return mcp_targets.render_entry(
        mcp_targets.target("claude"), SERVER_NAME, canonical_server()) or {}


def _resolve_keys(agent_keys: Optional[List[str]], agent_command: Optional[str]) -> List[str]:
    if agent_keys:
        return [k for k in (_key_of(k) for k in agent_keys) if k]
    k = _key_of(agent_command)
    return [k] if k else ["claude"]


def _target_keys(keys: List[str]) -> List[str]:
    return [k for k in keys if mcp_targets.caps(k).get("mcp_stdio")]


def _path_override(agent_key: str, config_paths, claude_config):
    if config_paths and agent_key in config_paths:
        return Path(config_paths[agent_key])
    if agent_key == "claude" and claude_config:
        return Path(claude_config)
    return None


def inject(
    *,
    agent_keys: Optional[List[str]] = None,
    agent_command: Optional[str] = None,
    claude_config: Optional[str | Path] = None,
    config_paths: Optional[dict] = None,
) -> bool:
    """Add the LinkedIn MCP server to each stdio-capable target agent's config.

    * No-op (returns False) when no target could be written.
    * Idempotent per agent -- but note the entry embeds this install's
      executable path, so a *moved or updated* install rewrites it, which is
      what makes "Re-sync to agents" worth having.
    * Refuses to touch a ``linkedin`` server the user configured themselves.
    """
    canonical = canonical_server()
    ledger = McpLedger()
    changed = False
    for key in _target_keys(_resolve_keys(agent_keys, agent_command)):
        tgt = mcp_targets.target(key)
        if tgt is None:
            continue
        did, wrote_root_extra = mcp_targets.write_server(
            tgt, SERVER_NAME, canonical,
            path_override=_path_override(key, config_paths, claude_config),
            ledger_managed=ledger.has(_PROVIDER, key),
        )
        if did:
            ledger.record(_PROVIDER, key, SERVER_NAME,
                          wrote_root_extra=wrote_root_extra)
            changed = True
    return changed


def remove(
    *,
    agent_keys: Optional[List[str]] = None,
    claude_config: Optional[str | Path] = None,
    config_paths: Optional[dict] = None,
) -> bool:
    """Drop the AgentDeck-managed LinkedIn server from every agent config we
    wrote it into (per the ledger). Leaves everything else alone."""
    ledger = McpLedger()
    ledger.backfill_claude(
        _PROVIDER, SERVER_NAME, _MANAGED,
        claude_path=_path_override("claude", config_paths, claude_config),
    )
    keys = agent_keys or list(ledger.agents_for(_PROVIDER).keys()) or ["claude"]

    changed = False
    for key in keys:
        tgt = mcp_targets.target(key)
        if tgt is None:
            continue
        did = mcp_targets.remove_server(
            tgt, SERVER_NAME,
            path_override=_path_override(key, config_paths, claude_config),
            ledger_managed=ledger.has(_PROVIDER, key),
            drop_root_extra=ledger.wrote_root_extra(_PROVIDER, key),
        )
        ledger.forget(_PROVIDER, key)
        changed = changed or did
    return changed


remove_all = remove
