"""Wire a "connected" Jira plugin into the coding agent running in a pane.

Like ``vercel_mcp`` this writes an **MCP server** into Claude Code's *user* scope
(the root ``mcpServers`` block of ``~/.claude.json``) so a Claude Code pane gets
Jira / Confluence tools natively, whatever folder it runs in.

**Thin, on purpose.** The plugin talks to Atlassian's official **Rovo Remote MCP
Server** -- hosted, Cloud-only, **OAuth 2.1** at
``https://mcp.atlassian.com/v1/mcp/authv2`` (transport ``http``). One connection
covers Jira, Confluence, Jira Service Management, Bitbucket and Compass. It does
not take a plain bearer token in v1 (an API-token path exists but needs an org
admin to enable it), and there is no official local binary. Claude Code is a
supported client and performs the OAuth itself: the user runs ``/mcp`` in a pane
once and Claude Code stores and owns those credentials. So AgentDeck never
handles an Atlassian token -- "connecting" the plugin just drops a tokenless
server entry into the config; authorising happens in the pane.

The server is named ``atlassian`` in the config (Atlassian's own convention, and
what ``/mcp`` shows); the AgentDeck-side provider key is ``jira``.

Qt-free. v1 supports **Claude Code**; :func:`supports_agent` gates everything.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

__all__ = [
    "REMOTE_MCP_URL",
    "supports_agent",
    "mcp_server_config",
    "inject",
    "remove",
    "remove_all",
]

#: Atlassian's committed ``.mcp.json`` uses this endpoint. Their docs also
#: mention ``https://mcp.atlassian.com/v2/mcp``; if ``/mcp`` in a pane rejects
#: this URL, switch the constant. (``.../v1/sse`` is deprecated since 2026-06-30.)
REMOTE_MCP_URL = "https://mcp.atlassian.com/v1/mcp/authv2"

#: Marks the server entry as ours, so :func:`remove` only ever deletes a block
#: AgentDeck wrote -- never an ``atlassian`` server the user configured by hand.
_MANAGED = "x-agentdeck-managed"
_SERVER_NAME = "atlassian"


def _is_claude(agent_command: str) -> bool:
    try:
        from agents import is_claude_command

        return is_claude_command(agent_command)
    except Exception:  # noqa: BLE001
        first = (agent_command or "").strip().split()[:1]
        return bool(first) and Path(first[0].strip('"\'')).stem.lower() == "claude"


def supports_agent(agent_command: Optional[str]) -> bool:
    """True when AgentDeck knows how to inject MCP config for this agent (v1: Claude Code)."""
    return _is_claude(agent_command or "")


# ---------------------------------------------------------------------------
# Server config
# ---------------------------------------------------------------------------

def mcp_server_config() -> dict:
    """The ``mcpServers["atlassian"]`` block written into Claude Code's config.

    Tokenless -- Claude Code does the OAuth (``/mcp``) and owns the credentials.
    """
    return {"type": "http", "url": REMOTE_MCP_URL, _MANAGED: True}


def _claude_config_path() -> Path:
    return Path.home() / ".claude.json"


def _load_json(path: Path) -> tuple[dict, bool]:
    """Return ``(config, existed)``. A malformed file reads as empty-but-existed
    so we never blow away something we can't parse."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, False
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}, True
    return (data if isinstance(data, dict) else {}), True


def _atomic_write_json(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.adk{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _strip_managed(config: dict) -> bool:
    """Remove our managed ``atlassian`` server from the root and every project.
    Mutates ``config``; returns True if anything was removed."""
    changed = False
    scopes = [config]
    projects = config.get("projects")
    if isinstance(projects, dict):
        scopes += [e for e in projects.values() if isinstance(e, dict)]
    for scope in scopes:
        servers = scope.get("mcpServers")
        if not isinstance(servers, dict):
            continue
        entry = servers.get(_SERVER_NAME)
        if isinstance(entry, dict) and entry.get(_MANAGED):
            del servers[_SERVER_NAME]
            changed = True
    return changed


def inject(
    *,
    agent_command: Optional[str] = None,
    claude_config: Optional[str | Path] = None,
) -> bool:
    """Add the Atlassian MCP server to Claude Code's **user-scope** config.

    Writes the root ``mcpServers.atlassian`` block in ``~/.claude.json``. A
    user-scope server is trusted automatically (no ``/mcp`` approval prompt for
    the *entry* -- the user still authorises with Atlassian via OAuth once).

    * No-op (returns False) when ``agent_command`` is given and isn't Claude Code.
    * Idempotent.
    * Refuses to touch a root ``atlassian`` server the user configured themselves.
    """
    if agent_command is not None and not supports_agent(agent_command):
        return False

    desired = mcp_server_config()

    path = Path(claude_config) if claude_config else _claude_config_path()
    config, _existed = _load_json(path)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        config["mcpServers"] = servers

    existing = servers.get(_SERVER_NAME)
    if isinstance(existing, dict) and not existing.get(_MANAGED):
        return False  # an atlassian server the user set up themselves
    if existing == desired:
        return False

    servers[_SERVER_NAME] = desired
    return _atomic_write_json(path, config)


def remove(*, claude_config: Optional[str | Path] = None) -> bool:
    """Drop the AgentDeck-managed Atlassian server from ``~/.claude.json`` -- the
    root entry plus any per-project entries. Leaves everything else alone."""
    path = Path(claude_config) if claude_config else _claude_config_path()
    config, existed = _load_json(path)
    if not existed:
        return False
    return _atomic_write_json(path, config) if _strip_managed(config) else False


#: Alias -- kept so call sites can read either name.
remove_all = remove
