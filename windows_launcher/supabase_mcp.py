"""Wire a "connected" Supabase plugin into the coding agent(s) running in a pane.

Like ``gitlab_mcp`` / ``linear_mcp`` this writes an **MCP server** into each
target agent's *user* scope (``mcpServers`` at the root of ``~/.claude.json``,
etc.) so a pane gets Supabase's database-review tools natively, whatever folder
it runs in.

**Not to be confused with** ``supabase_auth.py``. This module talks to the
*user's own* Supabase project (whatever database their app runs on) via
Supabase's hosted MCP server. ``supabase_auth.py`` is unrelated: it is
AgentDeck's *own backend* Supabase project, used for accounts and cloud sync.
Nothing here reads or writes that project.

**Hosted, OAuth-only, tokenless -- like GitLab/Linear.** Supabase's official
remote MCP server lives at ``https://mcp.supabase.com/mcp``, dynamic client
registration, no manual personal-access-token needed. AgentDeck never handles a
Supabase token; the agent runs the OAuth itself (``/mcp`` for Claude, per-agent
``mcp_targets.oauth_hint`` for the rest).

**Not fully thin, though.** Unlike Vercel/Jira/GitLab/Linear, Supabase's
endpoint takes URL query parameters that meaningfully scope what the agent can
do -- ``project_ref`` (restrict to one project), ``read_only`` (run all SQL
under read-only Postgres permissions) and ``features`` (comma-separated tool
groups). :func:`canonical_server` builds the URL from a connection's
``PluginConnection.settings`` dict instead of a bare constant. AgentDeck's
default is **read-only, one required project** -- see docs/PLUGINS.md §17 for
why that is hard-defaulted rather than a checkbox in v1.

Qt-free. A tokenless OAuth server is only wired to agents that can run the
OAuth handshake themselves -- see ``mcp_targets.OAUTH_ALLOWLIST`` / ``caps()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import mcp_targets
from mcp_targets import McpLedger
from plugin_store import SUPABASE as _PROVIDER

__all__ = [
    "REMOTE_MCP_URL",
    "supports_agent",
    "canonical_server",
    "mcp_server_config",
    "inject",
    "remove",
    "remove_all",
]

#: Supabase's hosted MCP endpoint. Self-hosted / local Supabase serves the same
#: protocol at ``http://localhost:54321/mcp`` -- out of scope for v1 (mirrors
#: GitLab's "hosted only" call for self-managed instances).
REMOTE_MCP_URL = "https://mcp.supabase.com/mcp"

_MANAGED = "x-agentdeck-managed"
_SERVER_NAME = "supabase"


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
    Supabase plugin can wire it). Accepts a key or a command string."""
    return bool(mcp_targets.caps(_key_of(agent)).get("mcp_oauth"))


def _truthy(value: object, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in ("false", "0", "no", "off", "")


def canonical_server(settings: Optional[Dict[str, str]] = None) -> dict:
    """Transport-agnostic spec: a tokenless hosted OAuth server, scoped by the
    connection's ``project_ref`` / ``read_only`` / ``features`` settings.

    ``read_only`` defaults **on** -- database review, not database editing.
    ``project_ref`` is expected non-empty (the controller validates this before
    connecting) but an empty one is tolerated here rather than raising, so a
    stale/incomplete connection still round-trips through ``inject``/``remove``.
    """
    settings = settings or {}
    project_ref = str(settings.get("project_ref") or "").strip()
    read_only = _truthy(settings.get("read_only"), default=True)
    features = str(settings.get("features") or "").strip()

    params: List[str] = []
    if project_ref:
        params.append(f"project_ref={project_ref}")
    params.append(f"read_only={'true' if read_only else 'false'}")
    if features:
        params.append(f"features={features}")

    url = REMOTE_MCP_URL + "?" + "&".join(params)
    return {"transport": "http", "url": url, "oauth": True}


def mcp_server_config(settings: Optional[Dict[str, str]] = None) -> dict:
    """The concrete ``mcpServers["supabase"]`` block for **Claude Code**."""
    return mcp_targets.render_entry(
        mcp_targets.target("claude"), _SERVER_NAME, canonical_server(settings)
    ) or {}


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
    settings: Optional[Dict[str, str]] = None,
    agent_keys: Optional[List[str]] = None,
    agent_command: Optional[str] = None,
    claude_config: Optional[str | Path] = None,
    config_paths: Optional[dict] = None,
) -> bool:
    """Add the Supabase MCP server to each OAuth-capable target agent's config.

    * No-op (returns False) when no target could be written.
    * Idempotent per agent -- re-injecting the same ``settings`` changes nothing;
      injecting *different* ``settings`` (e.g. a new ``project_ref``) overwrites
      the managed entry in place, same as a capability change for GitHub.
    * Refuses to touch a ``supabase`` server the user configured themselves.
    """
    canonical = canonical_server(settings)
    ledger = McpLedger()
    changed = False
    for key in _target_keys(_resolve_keys(agent_keys, agent_command)):
        tgt = mcp_targets.target(key)
        if tgt is None:
            continue
        did, wrote_root_extra = mcp_targets.write_server(
            tgt, _SERVER_NAME, canonical,
            path_override=_path_override(key, config_paths, claude_config),
            ledger_managed=ledger.has(_PROVIDER, key),
        )
        if did:
            ledger.record(_PROVIDER, key, _SERVER_NAME,
                          wrote_root_extra=wrote_root_extra)
            changed = True
    return changed


def remove(
    *,
    agent_keys: Optional[List[str]] = None,
    claude_config: Optional[str | Path] = None,
    config_paths: Optional[dict] = None,
) -> bool:
    """Drop the AgentDeck-managed Supabase server from every agent config we
    wrote it into (per the ledger). Leaves everything else alone."""
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
            drop_root_extra=ledger.wrote_root_extra(_PROVIDER, key),
        )
        ledger.forget(_PROVIDER, key)
        changed = changed or did
    return changed


remove_all = remove
