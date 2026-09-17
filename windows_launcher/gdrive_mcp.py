"""Wire a "connected" Google Drive plugin into Claude Code.

Like ``supabase_mcp`` this writes an **MCP server** into the agent's *user* scope
(``mcpServers`` at the root of ``~/.claude.json``) so a pane gets Drive's
search / read / create tools natively, whatever folder it runs in. The endpoint
is Google's own first-party server, ``https://drivemcp.googleapis.com/mcp/v1``
(streamable HTTP): ``search_files``, ``read_file_content``,
``download_file_content``, ``create_file``, ``copy_file``, ``get_file_metadata``,
``get_file_permissions``, ``list_recent_files``.

**Not thin, and Claude-Code-only -- both for the same reason.** Every other
hosted plugin (Vercel, Jira, GitLab, Linear, Supabase) is tokenless because its
vendor implements OAuth 2.1 *Dynamic Client Registration*: the agent registers
itself and AgentDeck never handles a credential. Google does not implement DCR,
and its token endpoint demands a client secret even for a Desktop-app client. So
two things follow:

1. The user brings a **Desktop-app OAuth client** from their own Google Cloud
   project. Its id goes in the connection's ``PluginConnection.settings`` and is
   rendered into the entry's ``oauth`` block; its secret goes to
   ``gdrive_secret.GDriveSecretStore`` and is handed to Claude Code out-of-band
   (see :func:`seed_secret`) -- it is never written to ``plugins.json``, never
   passed on a command line, and never mirrored to the account.
2. Only agents with ``mcp_targets.caps()["mcp_oauth_static"]`` are wired, which
   today means **Claude Code alone**. The other CLIs shape their static-OAuth
   config differently and would fail to authorise rather than fail loudly, so
   they are held back deliberately rather than written and left broken.

Qt-free. See docs/PLUGINS.md 18.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mcp_io
import mcp_targets
from mcp_targets import McpLedger
from plugin_store import GDRIVE as _PROVIDER

__all__ = [
    "REMOTE_MCP_URL",
    "DEFAULT_SCOPES",
    "CALLBACK_PORT",
    "supports_agent",
    "canonical_server",
    "mcp_server_config",
    "foreign_server_agents",
    "seed_secret",
    "forget_secret",
    "inject",
    "remove",
    "remove_all",
]

#: Google's first-party hosted Drive MCP endpoint (streamable HTTP).
REMOTE_MCP_URL = "https://drivemcp.googleapis.com/mcp/v1"

#: ``drive.readonly`` reads anything in the user's Drive; ``drive.file`` covers
#: files this client creates or opens. Both are what Google's own Drive-MCP
#: guide specifies. Space-separated per RFC 6749 3.3, which is the form Claude
#: Code's ``oauth.scopes`` takes.
DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/drive.file"
)

#: Google requires every redirect URI to be pre-registered, so the OAuth
#: callback port cannot be ephemeral. The user registers
#: ``http://localhost:8976/callback`` on their Desktop-app client.
CALLBACK_PORT = 8976

_MANAGED = "x-agentdeck-managed"
_SERVER_NAME = "gdrive"


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
    """True when this agent can use a *static* OAuth client (id + secret we
    supply). Claude Code only today. Accepts a key or a command string."""
    return bool(mcp_targets.caps(_key_of(agent)).get("mcp_oauth_static"))


def canonical_server(settings: Optional[Dict[str, str]] = None) -> dict:
    """Transport-agnostic spec: a hosted server with a *static* OAuth client.

    Unlike every other plugin's ``oauth: True``, this is a dict -- see
    ``mcp_targets.render_entry``, which only emits it for a target flagged
    ``oauth_static``. An empty ``client_id`` is tolerated rather than raising so
    a stale connection still round-trips through ``inject``/``remove``; the
    controller validates before connecting.
    """
    settings = settings or {}
    client_id = str(settings.get("client_id") or "").strip()
    scopes = str(settings.get("scopes") or "").strip() or DEFAULT_SCOPES
    return {
        "transport": "http",
        "url": REMOTE_MCP_URL,
        "oauth": {
            "clientId": client_id,
            "callbackPort": CALLBACK_PORT,
            "scopes": scopes,
        },
    }


def mcp_server_config(settings: Optional[Dict[str, str]] = None) -> dict:
    """The concrete ``mcpServers["gdrive"]`` block for **Claude Code**."""
    return mcp_targets.render_entry(
        mcp_targets.target("claude"), _SERVER_NAME, canonical_server(settings)
    ) or {}


def _resolve_keys(agent_keys: Optional[List[str]], agent_command: Optional[str]) -> List[str]:
    if agent_keys:
        return [k for k in (_key_of(k) for k in agent_keys) if k]
    k = _key_of(agent_command)
    return [k] if k else ["claude"]


def _target_keys(keys: List[str]) -> List[str]:
    return [k for k in keys if mcp_targets.caps(k).get("mcp_oauth_static")]


def _path_override(agent_key: str, config_paths, claude_config):
    if config_paths and agent_key in config_paths:
        return Path(config_paths[agent_key])
    if agent_key == "claude" and claude_config:
        return Path(claude_config)
    return None


def _config_path(tgt, config_paths, claude_config) -> Optional[Path]:
    """Where this target's config actually lives, honouring the test sandbox."""
    path = _path_override(tgt.key, config_paths, claude_config)
    if path is not None:
        return path
    sandbox = os.environ.get("ADK_MCP_CONFIG_DIR")
    if sandbox:
        return Path(sandbox) / f"{tgt.key}.json"
    try:
        return tgt.path()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Conflict check -- must run *before* seeding
# ---------------------------------------------------------------------------

def foreign_server_agents(
    *,
    agent_keys: Optional[List[str]] = None,
    claude_config: Optional[str | Path] = None,
    config_paths: Optional[dict] = None,
) -> List[str]:
    """Agent keys that already have a ``gdrive`` server **we don't own**.

    The other plugins get this guarantee for free: ``mcp_targets.write_server``
    refuses to clobber an unmarked entry. This plugin has to ask first, because
    :func:`seed_secret` shells out to ``claude mcp add``, which would happily
    overwrite a hand-rolled server before ``write_server`` ever gets a look in.
    """
    ledger = McpLedger()
    found: List[str] = []
    for key in _target_keys(_resolve_keys(agent_keys, None)):
        tgt = mcp_targets.target(key)
        if tgt is None or ledger.has(_PROVIDER, key):
            continue
        path = _config_path(tgt, config_paths, claude_config)
        if path is None:
            continue
        try:
            data, existed = mcp_io.load(path, tgt.fmt)
        except Exception:  # noqa: BLE001
            continue
        if not existed:
            continue
        servers = mcp_io.get_in(data, tgt.server_map, create=False)
        entry = servers.get(_SERVER_NAME) if hasattr(servers, "get") else None
        if entry is not None and not (hasattr(entry, "get") and entry.get(tgt.managed_key)):
            found.append(key)
    return found


# ---------------------------------------------------------------------------
# Handing the client secret to Claude Code
# ---------------------------------------------------------------------------

def _no_window_kwargs() -> dict:
    """Keep the subprocess from flashing a console window on Windows."""
    if os.name != "nt":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


def _claude_env(client_secret: Optional[str]) -> dict:
    env = dict(os.environ)
    if client_secret is not None:
        env["MCP_CLIENT_SECRET"] = client_secret
    # Tests (and a future portable mode) keep Claude Code's own config out of
    # the real home dir, the way ADK_MCP_CONFIG_DIR does for our own writes.
    sandbox = os.environ.get("ADK_CLAUDE_CONFIG_DIR")
    if sandbox:
        env["CLAUDE_CONFIG_DIR"] = sandbox
    return env


def _manual_command(client_id: str) -> str:
    return (
        "MCP_CLIENT_SECRET=<your client secret> claude mcp add --transport http "
        f"--scope user --client-id {client_id} --client-secret "
        f"--callback-port {CALLBACK_PORT} {_SERVER_NAME} {REMOTE_MCP_URL}"
    )


def seed_secret(
    client_id: str,
    client_secret: str,
    *,
    claude_bin: Optional[str] = None,
    timeout: float = 60.0,
) -> Tuple[bool, str]:
    """Store ``client_secret`` where Claude Code will look for it.

    Claude Code keeps an MCP server's OAuth client secret in its own credential
    store (``~/.claude/.credentials.json`` under ``mcpOAuthClientConfig``), not
    in ``.claude.json``, and the only supported way in is ``claude mcp add
    --client-secret``, which reads ``MCP_CLIENT_SECRET`` from the environment.
    So this is the one plugin that shells out instead of only writing JSON.

    The secret is passed **in the environment only** -- never in ``argv``, which
    any other process on the machine can read.

    ``claude mcp add`` refuses an existing server name (and still exits 0, so the
    exit code alone proves nothing), hence the remove-first and the check of what
    it actually printed.

    Returns ``(ok, message)`` and never raises; ``message`` is user-facing on
    failure and carries the command to run by hand.
    """
    exe = claude_bin or shutil.which("claude")
    manual = _manual_command(client_id)
    if not exe:
        return False, (
            "Couldn't find the `claude` command on your PATH, so the client "
            "secret wasn't handed to Claude Code. Run this in a pane, then use "
            f"Re-sync to agents:\n\n{manual}"
        )

    env = _claude_env(client_secret)
    try:
        # Best-effort: a missing server here is the normal first-connect case.
        subprocess.run(
            [exe, "mcp", "remove", _SERVER_NAME, "-s", "user"],
            env=env, capture_output=True, text=True, timeout=timeout,
            **_no_window_kwargs(),
        )
        proc = subprocess.run(
            [
                exe, "mcp", "add", "--transport", "http", "--scope", "user",
                "--client-id", client_id, "--client-secret",
                "--callback-port", str(CALLBACK_PORT),
                _SERVER_NAME, REMOTE_MCP_URL,
            ],
            env=env, capture_output=True, text=True, timeout=timeout,
            **_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return False, f"`claude mcp add` timed out. Run it by hand:\n\n{manual}"
    except OSError as exc:
        return False, f"Couldn't run `claude mcp add` ({exc}). Run it by hand:\n\n{manual}"

    out = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    if proc.returncode == 0 and "Added" in out:
        return True, ""
    return False, (
        "Claude Code didn't accept the client secret"
        + (f":\n\n{out}" if out else ".")
        + f"\n\nRun this by hand, then use Re-sync to agents:\n\n{manual}"
    )


def forget_secret(*, claude_bin: Optional[str] = None, timeout: float = 60.0) -> bool:
    """Drop Claude Code's copy of the client secret on disconnect.

    ``claude mcp remove`` clears the ``mcpOAuthClientConfig`` entry along with
    the server. Best-effort: :func:`remove` deletes our own entry regardless, so
    a missing ``claude`` binary must not strand the disconnect.
    """
    exe = claude_bin or shutil.which("claude")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "mcp", "remove", _SERVER_NAME, "-s", "user"],
            env=_claude_env(None), capture_output=True, text=True, timeout=timeout,
            **_no_window_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Inject / remove
# ---------------------------------------------------------------------------

def inject(
    *,
    settings: Optional[Dict[str, str]] = None,
    agent_keys: Optional[List[str]] = None,
    agent_command: Optional[str] = None,
    claude_config: Optional[str | Path] = None,
    config_paths: Optional[dict] = None,
    seeded: bool = False,
) -> bool:
    """Add the Drive MCP server to each static-OAuth-capable agent's config.

    * No-op (returns False) when no target could be written.
    * Idempotent per agent; a changed ``client_id`` overwrites in place.
    * Refuses to touch a ``gdrive`` server the user configured themselves.

    ``seeded=True`` says :func:`seed_secret` has just run, so the entry sitting
    there is the one ``claude mcp add`` wrote a moment ago rather than a
    stranger's -- claim it and restamp it with our marker and scopes. The
    controller only passes that after :func:`foreign_server_agents` came back
    empty, so the "don't clobber the user's own" guarantee still holds.
    """
    canonical = canonical_server(settings)
    ledger = McpLedger()
    changed = False
    for key in _target_keys(_resolve_keys(agent_keys, agent_command)):
        tgt = mcp_targets.target(key)
        if tgt is None:
            continue
        did, _root = mcp_targets.write_server(
            tgt, _SERVER_NAME, canonical,
            path_override=_path_override(key, config_paths, claude_config),
            ledger_managed=ledger.has(_PROVIDER, key) or seeded,
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
    """Drop the AgentDeck-managed Drive server from every agent config we wrote
    it into (per the ledger). Leaves everything else alone.

    Claude Code's copy of the client secret is dropped separately by
    :func:`forget_secret`, which the controller calls on disconnect.
    """
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
