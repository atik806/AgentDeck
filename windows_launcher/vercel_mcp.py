"""Wire a "connected" Vercel plugin into the coding agent(s) running in a pane.

Like ``github_mcp`` this writes an **MCP server** into each target agent's *user*
scope (Claude Code's root ``mcpServers`` in ``~/.claude.json``, etc.) so a pane gets
Vercel's deployment / logs / analytics tools natively, whatever folder it runs in.

**Thin, on purpose.** Vercel's official MCP server is *hosted* and *OAuth-only*
(``https://mcp.vercel.com`` -- the MCP Authorization spec: PKCE + Dynamic Client
Registration). It does not take an API bearer token, and there is no official local
binary. Approved MCP clients perform the OAuth themselves: the user authorises once
in the pane (``/mcp`` for Claude, ``codex mcp login vercel`` for Codex, …). So
AgentDeck never handles a Vercel token -- "connecting" the plugin just drops a
tokenless server entry into each agent's config; authorising happens in the pane.

That is why this module has no ``token`` / ``connection`` / ``folder`` params, no
capability→toolset mapping and no review-brief builders -- compare ``github_mcp``.

Qt-free. A tokenless OAuth server is only wired to agents that can run the OAuth
handshake themselves -- see ``mcp_targets.OAUTH_ALLOWLIST`` / ``caps()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import mcp_targets
from mcp_targets import McpLedger
from plugin_store import VERCEL as _PROVIDER

__all__ = [
    "REMOTE_MCP_URL",
    "supports_agent",
    "canonical_server",
    "mcp_server_config",
    "inject",
    "remove",
    "remove_all",
]

REMOTE_MCP_URL = "https://mcp.vercel.com"

_MANAGED = "x-agentdeck-managed"
_SERVER_NAME = "vercel"


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
    """True when this agent can authorise a hosted OAuth MCP server itself (so the
    Vercel plugin can wire it). Accepts a key or a command string."""
    return bool(mcp_targets.caps(_key_of(agent)).get("mcp_oauth"))


def canonical_server() -> dict:
    """Transport-agnostic spec: a tokenless hosted OAuth server."""
    return {"transport": "http", "url": REMOTE_MCP_URL, "oauth": True}


def mcp_server_config() -> dict:
    """The concrete ``mcpServers["vercel"]`` block for **Claude Code**."""
    return mcp_targets.render_entry(mcp_targets.target("claude"), _SERVER_NAME, canonical_server()) or {}


def _resolve_keys(agent_keys: Optional[List[str]], agent_command: Optional[str]) -> List[str]:
    if agent_keys:
        return [k for k in (_key_of(k) for k in agent_keys) if k]
    k = _key_of(agent_command)
    return [k] if k else ["claude"]


def _target_keys(keys: List[str]) -> List[str]:
    return [k for k in keys if mcp_targets.caps(k).get("mcp_oauth")]


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
    """Add the Vercel MCP server to each OAuth-capable target agent's config.

    * No-op (returns False) when no target could be written.
    * Idempotent per agent.
    * Refuses to touch a ``vercel`` server the user configured themselves.
    """
    canonical = canonical_server()
    ledger = McpLedger()
    changed = False
    for key in _target_keys(_resolve_keys(agent_keys, agent_command)):
        tgt = mcp_targets.target(key)
        if tgt is None:
            continue
        did, _root = mcp_targets.write_server(
            tgt, _SERVER_NAME, canonical,
            path_override=_path_override(key, config_paths, claude_config),
            ledger_managed=ledger.has(_PROVIDER, key),
        )
        if did:
            ledger.record(_PROVIDER, key, _SERVER_NAME, wrote_root_extra=False)
            changed = True
    return changed


def remove(
    *,
    agent_keys: Optional[List[str]] = None,
    claude_config: Optional[str | Path] = None,
    config_paths: Optional[dict] = None,
) -> bool:
    """Drop the AgentDeck-managed Vercel server from every agent config we wrote it
    into (per the ledger). Leaves everything else alone."""
    ledger = McpLedger()
    ledger.backfill_claude(
        _PROVIDER, _SERVER_NAME, _MANAGED,
        claude_path=_path_override("claude", config_paths, claude_config),
    )
    keys = agent_keys or list(ledger.agents_for(_PROVIDER).keys()) or ["claude"]

    changed = False
    for key in keys:
        tgt = mcp_targets.target(key)
        if tgt is None:
            continue
        did = mcp_targets.remove_server(
            tgt, _SERVER_NAME,
            path_override=_path_override(key, config_paths, claude_config),
            ledger_managed=ledger.has(_PROVIDER, key),
        )
        ledger.forget(_PROVIDER, key)
        changed = changed or did
    return changed


remove_all = remove
