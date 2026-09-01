"""Local, non-secret state for the AgentDeck plugins system.

Qt-free. One JSON file, ``%APPDATA%\\multi-terminal\\plugins.json``, holding the
*metadata* of each plugin connection -- which account it's linked to, which
capabilities the user opted into, and whether each is `ask` or `auto`. The
actual OAuth token lives elsewhere, encrypted (``github_auth.GitHubTokenStore``).

This file is safe to read without decryption, so the Plugins catalog can render
connection status instantly at startup.

The Supabase mirror (``public.plugin_connections``) is written by
``github_controller.py`` using the same REST helpers ``account.py`` uses; this
module only owns the on-disk half and the capability model both sides share.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

__all__ = [
    "GITHUB",
    "VERCEL",
    "JIRA",
    "CAPABILITIES",
    "CAPABILITY_LABELS",
    "DEFAULT_CAPABILITIES",
    "REQUIRED_CAPABILITY",
    "toolsets_for",
    "normalise_capabilities",
    "PluginStore",
    "PluginConnection",
]

GITHUB = "github"

#: The Vercel plugin is "thin" -- no capability model. It reuses this store only
#: for its presence flag (connected / not) and the ``plugins.json`` row that lets
#: the catalog render status at startup. See ``vercel_controller`` / ``vercel_mcp``.
VERCEL = "vercel"

#: The Jira plugin is thin too (Atlassian Rovo MCP -- hosted, OAuth-only). The
#: MCP server it writes into ``~/.claude.json`` is named ``atlassian``; this
#: provider key stays ``jira``. See ``jira_controller`` / ``jira_mcp``.
JIRA = "jira"

#: Ordered capability keys. Each maps to one or more GitHub MCP toolsets and a
#: tier of GitHub App permissions -- see docs/PLUGINS.md §5.
CAPABILITIES: List[str] = ["read", "review", "issues", "write", "actions", "admin"]

CAPABILITY_LABELS: Dict[str, str] = {
    "read": "Read code & pull requests",
    "review": "Review pull requests — post comments & reviews",
    "issues": "Manage issues — label, comment, close",
    "write": "Write code — branches, commits, open PRs",
    "actions": "Run GitHub Actions — dispatch workflows, read logs",
    "admin": "Admin — create / delete repositories",
}

#: "read" is implicit whenever a connection exists; "review" is the v1 default.
REQUIRED_CAPABILITY = "read"
DEFAULT_CAPABILITIES: List[str] = ["read", "review"]

#: capability -> GitHub MCP server toolsets it unlocks.
_TOOLSETS: Dict[str, List[str]] = {
    "read": ["context", "repos", "pull_requests"],
    "review": ["pull_requests"],
    "issues": ["issues"],
    "write": ["repos"],
    "actions": ["actions"],
    "admin": ["repos"],
}


def normalise_capabilities(caps: object) -> List[str]:
    """Clean a capability list: known keys only, ``read`` forced on, dedup,
    canonical order."""
    given = set()
    if isinstance(caps, (list, tuple, set)):
        given = {str(c).strip().lower() for c in caps}
    given.add(REQUIRED_CAPABILITY)
    return [c for c in CAPABILITIES if c in given]


def toolsets_for(caps: object) -> List[str]:
    """The de-duplicated GitHub MCP toolset list for a capability selection."""
    out: List[str] = []
    for cap in normalise_capabilities(caps):
        for ts in _TOOLSETS.get(cap, []):
            if ts not in out:
                out.append(ts)
    return out


# ---------------------------------------------------------------------------
# Connection record
# ---------------------------------------------------------------------------

class PluginConnection:
    """One connected provider's local metadata."""

    def __init__(
        self,
        provider: str,
        *,
        login: str = "",
        capabilities: Optional[List[str]] = None,
        automation: Optional[Dict[str, str]] = None,
        transport: str = "remote",
        connected_at: float = 0.0,
    ):
        self.provider = provider
        self.login = login
        self.capabilities = normalise_capabilities(capabilities or DEFAULT_CAPABILITIES)
        self.automation = {
            k: ("auto" if str(v).lower() == "auto" else "ask")
            for k, v in (automation or {}).items()
            if k in CAPABILITIES
        }
        self.transport = "local" if transport == "local" else "remote"
        self.connected_at = float(connected_at or time.time())

    def automation_mode(self, capability: str) -> str:
        """`ask` (default) or `auto` for a capability."""
        return self.automation.get(capability, "ask")

    def to_dict(self) -> dict:
        return {
            "login": self.login,
            "capabilities": list(self.capabilities),
            "automation": dict(self.automation),
            "transport": self.transport,
            "connected_at": _iso(self.connected_at),
        }

    @classmethod
    def from_dict(cls, provider: str, data: dict) -> "PluginConnection":
        if not isinstance(data, dict):
            data = {}
        return cls(
            provider,
            login=str(data.get("login") or ""),
            capabilities=data.get("capabilities"),
            automation=data.get("automation") if isinstance(data.get("automation"), dict) else {},
            transport=str(data.get("transport") or "remote"),
            connected_at=_epoch(data.get("connected_at")),
        )


def _iso(epoch: float) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    except (OSError, ValueError, OverflowError):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        text = value.strip().replace("Z", "+00:00")
        try:
            from datetime import datetime

            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return time.time()
    return time.time()


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def _default_path() -> Path:
    try:
        from config import _get_config_dir

        return _get_config_dir() / "plugins.json"
    except Exception:  # noqa: BLE001
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "multi-terminal" / "plugins.json"


class PluginStore:
    """Read/write ``plugins.json``. Every method is best-effort and never raises."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else _default_path()

    # -- raw ---------------------------------------------------------------

    def _read(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(text) if text.strip() else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    # -- typed API -------------------------------------------------------

    def get(self, provider: str) -> Optional[PluginConnection]:
        entry = self._read().get(provider)
        if not isinstance(entry, dict):
            return None
        return PluginConnection.from_dict(provider, entry)

    def is_connected(self, provider: str) -> bool:
        return self.get(provider) is not None

    def put(self, conn: PluginConnection) -> bool:
        data = self._read()
        data[conn.provider] = conn.to_dict()
        return self._write(data)

    def update(self, provider: str, **changes) -> Optional[PluginConnection]:
        """Patch an existing connection's capabilities / automation / transport."""
        conn = self.get(provider)
        if conn is None:
            return None
        if "capabilities" in changes:
            conn.capabilities = normalise_capabilities(changes["capabilities"])
        if "automation" in changes and isinstance(changes["automation"], dict):
            conn.automation = {
                k: ("auto" if str(v).lower() == "auto" else "ask")
                for k, v in changes["automation"].items()
                if k in CAPABILITIES
            }
        if "login" in changes:
            conn.login = str(changes["login"] or "")
        if "transport" in changes:
            conn.transport = "local" if changes["transport"] == "local" else "remote"
        self.put(conn)
        return conn

    def remove(self, provider: str) -> bool:
        data = self._read()
        if provider in data:
            del data[provider]
            return self._write(data)
        return True
