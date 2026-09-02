"""Where and how each coding agent stores its MCP servers.

``github_mcp`` / ``vercel_mcp`` / ``jira_mcp`` decide *what* server to wire (a URL,
maybe a bearer token, the toolset scoping). This module owns *where it goes* -- one
:class:`McpTarget` adapter per agent describing its config file (path, format), the
key path its server map lives under, and the field names that agent expects
(``url`` vs ``httpUrl`` vs ``serverUrl`` vs ``uri``; ``mcpServers`` vs ``mcp`` vs
``extensions``; whether a ``type`` sibling is required; etc.).

It also owns:

* the **capability model** -- can this agent take an MCP server at all, a
  header-authenticated remote one (GitHub), a tokenless OAuth one it authorises
  itself (Vercel / Jira)?
* a small **ledger** (``%APPDATA%\\multi-terminal\\mcp_state.json``) recording which
  ``(provider, agent)`` config files AgentDeck actually wrote, so disconnect touches
  exactly those and nothing else -- even if a strict parser dropped our inline
  ``x-agentdeck-managed`` marker.
* per-agent **OAuth hint** strings for the Plugins UI ("run ``/mcp``" for Claude,
  "run ``codex mcp login vercel``" for Codex, …).

Qt-free. Must not import ``agents`` at module load (cycle) -- lazy-imports it inside
the two functions that need a label / the installed set.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import mcp_io

__all__ = [
    "McpTarget",
    "all_keys",
    "target",
    "caps",
    "render_entry",
    "write_server",
    "remove_server",
    "oauth_hint",
    "McpLedger",
    "OAUTH_ALLOWLIST",
]


def _home() -> Path:
    return Path.home()


def _env_dir(var: str, default: Path) -> Path:
    val = os.environ.get(var)
    return Path(val) if val else default


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or _home())


def _localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or _home())


def _xdg_config() -> Path:
    return _env_dir("XDG_CONFIG_HOME", _home() / ".config")


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class McpTarget:
    key: str
    label: str
    fmt: str                                    # "json" | "toml" | "yaml"
    path: Callable[[], Optional[Path]]

    server_map: Tuple[str, ...]                  # ("mcpServers",) / ("mcp",) / ("amp.mcpServers",) / ("mcp_servers",) / ("extensions",)
    url_field: str = "url"                       # "url" | "httpUrl" | "serverUrl" | "uri"
    headers_field: Optional[str] = "headers"     # None => this agent gets no header-auth path
    type_field: Optional[str] = "type"
    type_value: Optional[str] = None             # "http" | "remote" | "streamable_http"

    server_extra: Dict[str, object] = field(default_factory=dict)   # {"enabled": True}, {"tools": ["*"]}
    root_extra: Dict[str, object] = field(default_factory=dict)     # {"experimental_use_rmcp_client": True}

    bearer_style: str = "header"                 # "header" | "toml_bearer_token"
    name_extra: Optional[str] = None             # goose wants entry[name_extra] == server_name
    managed_key: str = "x-agentdeck-managed"     # toml/yaml can't take a hyphen -> "x_agentdeck_managed"

    # capability flags
    mcp: bool = True
    header_auth: bool = True
    oauth: bool = True


# Vercel / Jira are tokenless -- the agent runs the OAuth handshake itself. Until
# each agent's in-pane flow is verified, only these agents get those two. Phase 3
# of docs/PLUGINS.md §14 widens this set one agent at a time:
#   * claude   -- `/mcp` in the pane, browser authorise (verified)
#   * opencode -- native remote-MCP auto-DCR OAuth, opens the browser on first
#                 tool use (verified 2026-09-02)
OAUTH_ALLOWLIST = {"claude", "opencode"}


_TARGETS: Dict[str, McpTarget] = {
    "claude": McpTarget(
        key="claude", label="Claude Code", fmt="json",
        path=lambda: _home() / ".claude.json",
        server_map=("mcpServers",), type_value="http",
    ),
    "codex": McpTarget(
        key="codex", label="Codex", fmt="toml",
        path=lambda: _env_dir("CODEX_HOME", _home() / ".codex") / "config.toml",
        server_map=("mcp_servers",), type_field=None, headers_field=None,
        bearer_style="toml_bearer_token",
        root_extra={"experimental_use_rmcp_client": True},
        managed_key="x_agentdeck_managed",
    ),
    "copilot": McpTarget(
        key="copilot", label="GitHub Copilot CLI", fmt="json",
        path=lambda: _env_dir("COPILOT_HOME", _home() / ".copilot") / "mcp-config.json",
        server_map=("mcpServers",), type_value="http",
        server_extra={"tools": ["*"]},
        oauth=False,   # Copilot CLI OAuth-for-remote-MCP support unconfirmed
    ),
    "gemini": McpTarget(
        key="gemini", label="Gemini CLI", fmt="json",
        path=lambda: _home() / ".gemini" / "settings.json",
        server_map=("mcpServers",), url_field="httpUrl", type_field=None,
    ),
    "cursor-agent": McpTarget(
        key="cursor-agent", label="Cursor Agent", fmt="json",
        path=lambda: _home() / ".cursor" / "mcp.json",
        server_map=("mcpServers",), type_field=None,
    ),
    "opencode": McpTarget(
        key="opencode", label="opencode", fmt="json",
        path=lambda: _xdg_config() / "opencode" / "opencode.json",
        server_map=("mcp",), type_value="remote",
        server_extra={"enabled": True},
    ),
    "amp": McpTarget(
        key="amp", label="Amp", fmt="json",
        path=lambda: (
            Path(os.environ["AMP_SETTINGS_FILE"]) if os.environ.get("AMP_SETTINGS_FILE")
            else _xdg_config() / "amp" / "settings.json"
        ),
        server_map=("amp.mcpServers",), type_field=None,
    ),
    "antigravity": McpTarget(
        key="antigravity", label="Antigravity CLI", fmt="json",
        path=lambda: _home() / ".gemini" / "config" / "mcp_config.json",
        server_map=("mcpServers",), url_field="serverUrl", type_field=None,
    ),
    "qwen": McpTarget(
        key="qwen", label="Qwen Code", fmt="json",
        path=lambda: _home() / ".qwen" / "settings.json",
        server_map=("mcpServers",), url_field="httpUrl", type_field=None,
    ),
    "crush": McpTarget(
        key="crush", label="Crush", fmt="json",
        path=lambda: _localappdata() / "crush" / "crush.json",
        server_map=("mcp",), type_value="http",
    ),
    "goose": McpTarget(
        key="goose", label="Goose", fmt="yaml",
        path=lambda: _appdata() / "Block" / "goose" / "config" / "config.yaml",
        server_map=("extensions",), url_field="uri", type_value="streamable_http",
        server_extra={"enabled": True, "bundled": False},
        name_extra="name", managed_key="x_agentdeck_managed",
    ),
    # aider: intentionally absent -- no native MCP support.
}


def all_keys() -> List[str]:
    """Every agent key this module can wire, in registry order."""
    return list(_TARGETS.keys())


def target(key: Optional[str]) -> Optional[McpTarget]:
    return _TARGETS.get((key or "").strip().lower())


def caps(key: Optional[str]) -> Dict[str, object]:
    """``{"mcp", "mcp_remote_headers", "mcp_oauth", "format"}`` for an agent key.

    All-False / ``None`` for an unknown key or one with no MCP support (aider).
    ``mcp_oauth`` also respects :data:`OAUTH_ALLOWLIST`.
    """
    tgt = target(key)
    if tgt is None:
        return {"mcp": False, "mcp_remote_headers": False, "mcp_oauth": False, "format": None}
    return {
        "mcp": tgt.mcp,
        # can carry a bearer credential on a remote server -- via an Authorization
        # header (most agents) or a dedicated field (Codex's ``bearer_token``).
        "mcp_remote_headers": tgt.mcp and tgt.header_auth,
        "mcp_oauth": tgt.mcp and tgt.oauth and tgt.key in OAUTH_ALLOWLIST,
        "format": tgt.fmt,
    }


# ---------------------------------------------------------------------------
# Rendering a canonical server spec into one agent's entry
# ---------------------------------------------------------------------------

def render_entry(tgt: McpTarget, server_name: str, canonical: dict) -> Optional[dict]:
    """The dict to store at ``server_map[server_name]`` for ``tgt``.

    ``canonical`` is the transport-agnostic spec the ``*_mcp`` injectors build:

        {"transport": "http"|"stdio",
         "url": str, "headers": {str: str}, "bearer": str | None,   # http
         "command": str, "args": [str], "env": {str: str},          # stdio
         "oauth": bool}

    Returns ``None`` if this target can't represent the spec (e.g. a stdio spec for
    an agent we only wire remotely).
    """
    transport = canonical.get("transport", "http")
    if transport == "stdio":
        # Only Claude uses the local github-mcp-server binary today; keep it simple.
        if tgt.key != "claude":
            return None
        entry: dict = {
            "command": canonical.get("command", ""),
            "args": list(canonical.get("args", [])),
            "env": dict(canonical.get("env", {})),
        }
        entry[tgt.managed_key] = True
        return entry

    entry = {tgt.url_field: canonical["url"]}
    if tgt.type_field and tgt.type_value:
        entry[tgt.type_field] = tgt.type_value

    headers = dict(canonical.get("headers") or {})
    bearer = canonical.get("bearer")
    if tgt.bearer_style == "toml_bearer_token":
        headers.pop("Authorization", None)
        if bearer:
            entry["bearer_token"] = bearer
    if headers and tgt.headers_field:
        entry[tgt.headers_field] = headers

    for k, v in tgt.server_extra.items():
        entry[k] = list(v) if isinstance(v, list) else v
    if tgt.name_extra:
        entry[tgt.name_extra] = server_name

    entry[tgt.managed_key] = True
    return entry


def _plain(obj: object) -> object:
    """Normalise a tomlkit/ruamel node to plain python for equality checks."""
    if hasattr(obj, "items"):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _is_ours(entry: object, managed_key: str, ledger_managed: bool) -> bool:
    if ledger_managed:
        return True
    return bool(hasattr(entry, "get") and entry.get(managed_key))


# ---------------------------------------------------------------------------
# Write / remove one server in a target's config file
# ---------------------------------------------------------------------------

_FMT_EXT = {"json": "json", "toml": "toml", "yaml": "yaml"}


def _resolve_path(tgt: McpTarget, path_override: Optional[Path]) -> Optional[Path]:
    if path_override is not None:
        return Path(path_override)
    # Tests (and a future "portable" mode) redirect every agent's config into one
    # sandbox dir so nothing touches the real ~/.codex, ~/.gemini, …
    sandbox = os.environ.get("ADK_MCP_CONFIG_DIR")
    if sandbox:
        return Path(sandbox) / f"{tgt.key}.{_FMT_EXT.get(tgt.fmt, 'json')}"
    try:
        return tgt.path()
    except Exception:  # noqa: BLE001
        return None


def write_server(
    tgt: McpTarget,
    server_name: str,
    canonical: dict,
    *,
    path_override: Optional[Path] = None,
    ledger_managed: bool = False,
) -> Tuple[bool, bool]:
    """Write ``server_name`` into ``tgt``'s config file.

    Returns ``(changed, wrote_root_extra)``:
    * ``changed`` -- the file was rewritten.
    * ``wrote_root_extra`` -- a ``root_extra`` key (e.g. Codex's
      ``experimental_use_rmcp_client``) was *added by us* (was absent before), so
      disconnect knows it may remove it.

    No-op ``(False, False)`` when: the path can't be resolved; the format's writer
    is unavailable; the spec can't be rendered for this agent; or an unmarked
    server of the same name is already there (the user's own).
    """
    path = _resolve_path(tgt, path_override)
    if path is None:
        return False, False
    if tgt.fmt == "toml" and not mcp_io.toml_ok():
        return False, False
    if tgt.fmt == "yaml" and not mcp_io.yaml_ok():
        return False, False

    desired = render_entry(tgt, server_name, canonical)
    if desired is None:
        return False, False

    with mcp_io.locked(path):
        data, _existed = mcp_io.load(path, tgt.fmt)
        servers = mcp_io.get_in(data, tgt.server_map, create=True)
        if servers is None:
            return False, False

        existing = servers.get(server_name)
        if existing is not None and not _is_ours(existing, tgt.managed_key, ledger_managed):
            return False, False
        if existing is not None and _plain(existing) == _plain(desired):
            # already exactly what we want -- but still make sure root_extra is set
            if _needs_root_extra(data, tgt):
                _apply_root_extra(data, tgt)
                return mcp_io.dump(path, data, tgt.fmt), True
            return False, False

        servers[server_name] = mcp_io.as_item(desired, tgt.fmt)
        wrote_root_extra = _needs_root_extra(data, tgt)
        if wrote_root_extra:
            _apply_root_extra(data, tgt)
        return mcp_io.dump(path, data, tgt.fmt), wrote_root_extra


def _needs_root_extra(data: object, tgt: McpTarget) -> bool:
    return bool(tgt.root_extra) and any(
        (not hasattr(data, "get")) or data.get(k) != v for k, v in tgt.root_extra.items()
    )


def _apply_root_extra(data: object, tgt: McpTarget) -> None:
    for k, v in tgt.root_extra.items():
        data[k] = v


def remove_server(
    tgt: McpTarget,
    server_name: str,
    *,
    path_override: Optional[Path] = None,
    ledger_managed: bool = False,
    drop_root_extra: bool = False,
) -> bool:
    """Delete ``server_name`` from ``tgt``'s config file if it's ours.

    ``drop_root_extra`` also removes any ``root_extra`` key we added -- pass it only
    when the ledger says we added it *and* no other AgentDeck-managed server remains
    in this file.
    """
    path = _resolve_path(tgt, path_override)
    if path is None or not Path(path).exists():
        return False
    if tgt.fmt == "toml" and not mcp_io.toml_ok():
        return False
    if tgt.fmt == "yaml" and not mcp_io.yaml_ok():
        return False

    with mcp_io.locked(path):
        data, existed = mcp_io.load(path, tgt.fmt)
        if not existed:
            return False
        servers = mcp_io.get_in(data, tgt.server_map, create=False)
        changed = False
        if servers is not None and server_name in servers:
            entry = servers.get(server_name)
            if _is_ours(entry, tgt.managed_key, ledger_managed):
                del servers[server_name]
                changed = True

        # Claude's older builds also stashed entries per-project.
        if tgt.key == "claude":
            projects = data.get("projects") if hasattr(data, "get") else None
            if isinstance(projects, dict):
                for scope in projects.values():
                    if not isinstance(scope, dict):
                        continue
                    ps = scope.get("mcpServers")
                    if isinstance(ps, dict) and isinstance(ps.get(server_name), dict) \
                            and ps[server_name].get(tgt.managed_key):
                        del ps[server_name]
                        changed = True

        if drop_root_extra and tgt.root_extra:
            still_managed = any(
                hasattr(e, "get") and e.get(tgt.managed_key)
                for e in (servers.values() if servers else [])
            )
            if not still_managed:
                for k in tgt.root_extra:
                    if hasattr(data, "get") and k in data:
                        del data[k]
                        changed = True

        if not changed:
            return False
        return mcp_io.dump(path, data, tgt.fmt)


# ---------------------------------------------------------------------------
# OAuth hint text for the Plugins UI
# ---------------------------------------------------------------------------

_OAUTH_HINTS: Dict[str, str] = {
    "claude": "run `/mcp` in the pane and authorise {server} in your browser",
    "codex": "run `codex mcp login {server}` once",
    "gemini": "run `/mcp auth {server}` in the pane",
    "qwen": "run `/mcp auth {server}` in the pane",
    "opencode": "opencode opens your browser to authorise on first use",
    "cursor-agent": "approve {server} when the agent prompts on first use",
    "amp": "approve {server} when Amp prompts on first use",
    "crush": "approve {server} when Crush prompts on first use",
    "goose": "run `goose configure` or approve when prompted on first use",
    "antigravity": "approve {server} when the agent prompts on first use",
    "copilot": "run `/mcp` in the pane and authorise {server}",
}


def oauth_hint(agent_key: str, server_name: str) -> str:
    tmpl = _OAUTH_HINTS.get((agent_key or "").strip().lower(),
                            "start the agent and follow its MCP authorisation prompt")
    return tmpl.format(server=server_name)


# ---------------------------------------------------------------------------
# The ledger -- which (provider, agent) config files we actually wrote
# ---------------------------------------------------------------------------

def _ledger_path() -> Path:
    override = os.environ.get("ADK_MCP_STATE")   # tests redirect the ledger here
    if override:
        return Path(override)
    try:
        from config import _get_config_dir

        return _get_config_dir() / "mcp_state.json"
    except Exception:  # noqa: BLE001
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "multi-terminal" / "mcp_state.json"


class McpLedger:
    """``mcp_state.json``: ``{provider: {agent_key: {"server", "wrote_root_extra"}}}``.

    Best-effort, never raises. The authoritative record of what disconnect should
    undo -- more reliable than the inline marker if an agent's strict parser drops
    unknown keys.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else _ledger_path()

    def _read(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
            data = json.loads(text) if text.strip() else {}
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # PID-scoped temp name (matches ``mcp_io.dump``) so two AgentDeck
            # instances -- or the staggered github/vercel/jira controllers --
            # flushing the ledger at once can't clobber each other's temp file.
            tmp = self.path.with_name(f"{self.path.name}.adk{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def record(self, provider: str, agent_key: str, server: str, *, wrote_root_extra: bool) -> None:
        # Hold the per-path lock across the whole read-modify-write so two
        # controllers recording different agents don't lose one another's entry.
        with mcp_io.locked(self.path):
            data = self._read()
            prov = data.setdefault(provider, {})
            prev = prov.get(agent_key) if isinstance(prov.get(agent_key), dict) else {}
            prov[agent_key] = {
                "server": server,
                # sticky: once we've recorded that we added root_extra, keep it until forgotten
                "wrote_root_extra": bool(wrote_root_extra or prev.get("wrote_root_extra")),
            }
            self._write(data)

    def forget(self, provider: str, agent_key: Optional[str] = None) -> None:
        with mcp_io.locked(self.path):
            data = self._read()
            if provider not in data:
                return
            if agent_key is None:
                del data[provider]
            else:
                data[provider].pop(agent_key, None)
                if not data[provider]:
                    del data[provider]
            self._write(data)

    def agents_for(self, provider: str) -> Dict[str, dict]:
        prov = self._read().get(provider)
        return prov if isinstance(prov, dict) else {}

    def has(self, provider: str, agent_key: str) -> bool:
        return agent_key in self.agents_for(provider)

    def wrote_root_extra(self, provider: str, agent_key: str) -> bool:
        entry = self.agents_for(provider).get(agent_key)
        return bool(isinstance(entry, dict) and entry.get("wrote_root_extra"))

    def backfill_claude(self, provider: str, server_name: str,
                        managed_key: str = "x-agentdeck-managed",
                        claude_path: Optional[Path] = None) -> None:
        """Seed ``ledger[provider]["claude"]`` from an existing marked entry in
        ``~/.claude.json`` -- so users who connected before the ledger existed can
        still disconnect cleanly."""
        if self.has(provider, "claude"):
            return
        try:
            cc = Path(claude_path) if claude_path else _home() / ".claude.json"
            data = json.loads(cc.read_text(encoding="utf-8"))
            entry = (data.get("mcpServers") or {}).get(server_name)
        except (OSError, ValueError, AttributeError):
            return
        if isinstance(entry, dict) and entry.get(managed_key):
            self.record(provider, "claude", server_name, wrote_root_extra=False)
