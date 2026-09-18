"""Qt bridge for the Supabase plugin -- the surface ``plugins_panel`` talks to.

Mirrors ``gitlab_controller.GitLabController``: Supabase's hosted MCP server is
OAuth-only, and the agent owns the OAuth credentials (the user runs ``/mcp`` in a
pane). So there is no device flow, no token vault, no GitHub-style capability
model here. "Connecting" means: record the connection (with its ``project_ref`` /
``read_only`` / ``features`` settings) in ``plugins.json``, drop a scoped,
tokenless server entry into ``~/.claude.json``, and best-effort mirror the
*metadata* to the account's ``plugin_connections`` row.

The one thing this controller does that Vercel/Jira/GitLab/Linear's don't:
validate ``project_ref`` before connecting, and re-inject (not just re-wire) when
settings change, since those settings are baked into the server's URL.

**Not to be confused with** ``supabase_auth.py`` / ``account.py``. Those talk to
AgentDeck's *own* backend Supabase project (accounts, cloud sync). This
controller talks to the *user's* Supabase project, purely for database review in
a pane -- see docs/PLUGINS.md §17.

``supabase_mcp`` / ``plugin_store`` do the real work and stay Qt-free.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import supabase_mcp
from plugin_store import SUPABASE, PluginConnection, PluginStore

__all__ = ["SupabaseController"]

#: Supabase project refs are short lowercase-alnum ids (typically 20 chars,
#: e.g. ``abcdefghijklmnopqrst``). Kept loose on purpose -- we don't want to
#: reject a valid ref because Supabase changes the exact length; we just want to
#: catch "empty" / "pasted the wrong thing" before it goes out as a URL param.
_PROJECT_REF_RE = re.compile(r"^[a-zA-Z0-9]{6,60}$")


def valid_project_ref(value: str) -> bool:
    return bool(_PROJECT_REF_RE.match((value or "").strip()))


class _Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        self.done.emit(result)


class SupabaseController(QObject):
    """The Supabase-plugin surface. Safe to construct unconditionally."""

    connected = Signal(dict)      # {} -- dict kept so _on_* lambdas match GitHub
    disconnected = Signal()
    busy_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, account=None, config: Optional[dict] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._account = account
        self._config = config if config is not None else {}
        self._store = PluginStore()
        self._busy = False
        self._workers: set = set()

        # Already connected from a previous run? Make sure the MCP server is in
        # every target agent's config now. Same-file writers are serialised by
        # mcp_io.locked(), so no stagger is needed.
        if self.is_connected:
            QTimer.singleShot(0, self.ensure_wired)

    # -- state -----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._store.is_connected(SUPABASE)

    @property
    def connection(self) -> Optional[PluginConnection]:
        return self._store.get(SUPABASE)

    @property
    def login(self) -> str:
        return ""

    @property
    def project_ref(self) -> str:
        conn = self.connection
        return conn.settings.get("project_ref", "") if conn else ""

    @property
    def is_busy(self) -> bool:
        return self._busy

    # -- connect / disconnect ------------------------------------------------

    def start_connect(self, project_ref: str, *, read_only: bool = True,
                       features: str = "") -> bool:
        """Connect, scoped to one Supabase project. Refuses an empty/invalid
        ``project_ref`` -- see docs/PLUGINS.md §17 on why scoping is required,
        not optional. Returns False (and emits ``error``) without connecting."""
        if self._busy:
            return False
        ref = (project_ref or "").strip()
        if not valid_project_ref(ref):
            self.error.emit(
                "Enter a Supabase project reference (Project Settings → General "
                "→ Reference ID) before connecting."
            )
            return False

        settings = {"project_ref": ref, "read_only": "true" if read_only else "false"}
        if features.strip():
            settings["features"] = features.strip()

        conn = PluginConnection(SUPABASE, settings=settings)
        self._store.put(conn)
        self.ensure_wired()
        self._mirror_up(conn)
        self.connected.emit({})
        return True

    def update_settings(self, *, project_ref: Optional[str] = None,
                         read_only: Optional[bool] = None,
                         features: Optional[str] = None) -> bool:
        """Change the connected project's scoping and re-inject -- no
        disconnect/reconnect needed, ``supabase_mcp.inject`` overwrites the
        managed entry in place (same pattern as a GitHub capability edit)."""
        conn = self.connection
        if conn is None or self._busy:
            return False
        if project_ref is not None:
            ref = project_ref.strip()
            if not valid_project_ref(ref):
                self.error.emit("That doesn't look like a valid Supabase project reference.")
                return False
            conn.settings["project_ref"] = ref
        if read_only is not None:
            conn.settings["read_only"] = "true" if read_only else "false"
        if features is not None:
            if features.strip():
                conn.settings["features"] = features.strip()
            else:
                conn.settings.pop("features", None)
        self._store.put(conn)
        self.ensure_wired()
        self._mirror_up(conn)
        return True

    def disconnect(self) -> None:
        if self._busy:
            return
        self.unwire_all()
        self._store.remove(SUPABASE)
        self._mirror_delete()
        self.disconnected.emit()

    # -- MCP wiring ----------------------------------------------------

    def _target_agent_keys(self, agent_command: Optional[str] = None) -> list[str]:
        """Which agents to write the Supabase MCP server into -- the workspace's
        agent plus, unless ``plugins_wire_all_agents`` is off, every installed
        agent. ``supabase_mcp.inject`` further filters to the OAuth-capable set."""
        import agents

        keys: set[str] = set()
        k = agents.agent_key_for_command(agent_command) if agent_command else ""
        if not k:
            k = str(self._config.get("agent", "")).strip().lower()
        if k and k not in ("none", "custom"):
            keys.add(k)
        if self._config.get("plugins_wire_all_agents", True):
            keys |= set(agents.installed_agent_keys())
        return sorted(keys)

    def ensure_wired(self, folder: Optional[str] = None,
                     agent_command: Optional[str] = None) -> bool:
        """Add the Supabase MCP server to every OAuth-capable target agent's
        user-scope config, scoped by the connection's settings. Best-effort;
        returns True if any config changed."""
        conn = self.connection
        if conn is None:
            return False
        keys = self._target_agent_keys(agent_command)
        if not keys:
            return False
        try:
            return supabase_mcp.inject(settings=conn.settings, agent_keys=keys)
        except Exception:  # noqa: BLE001
            return False

    #: Back-compat alias -- older name for the same thing.
    def wire_if_connected(self) -> bool:
        return self.ensure_wired()

    def unwire_all(self) -> None:
        try:
            supabase_mcp.remove()
        except Exception:  # noqa: BLE001
            pass

    # -- Supabase mirror (metadata only, best-effort) ------------------
    #
    # NOTE: this is AgentDeck's *own* backend Supabase project (via
    # supabase_auth.rest_upsert), not the user's project this plugin connects
    # to. Only the connection's presence/login is mirrored -- project_ref stays
    # local, same trust boundary as a GitHub token (see docs/PLUGINS.md §17).

    def _mirror_up(self, conn: PluginConnection) -> None:
        acc = self._account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return
        session = getattr(acc, "session", None)
        if session is None:
            return
        row = {
            "provider": SUPABASE,
            "external_login": "",
            "capabilities": [],
            "automation": {},
        }
        uid = getattr(session, "user_id", "")
        if uid:
            row["user_id"] = uid

        def _do():
            import supabase_auth

            return supabase_auth.rest_upsert("plugin_connections", row, session.access_token)

        self._run(_do, lambda _r: None, on_fail=lambda _m: None)

    def _mirror_delete(self) -> None:
        acc = self._account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return
        session = getattr(acc, "session", None)
        if session is None:
            return

        def _do():
            import requests
            import supabase_auth

            requests.delete(
                f"{supabase_auth.SUPABASE_URL}/rest/v1/plugin_connections",
                headers={
                    "apikey": supabase_auth.SUPABASE_KEY,
                    "Authorization": f"Bearer {session.access_token}",
                },
                params={"provider": f"eq.{SUPABASE}"},
                timeout=15,
            )
            return None

        self._run(_do, lambda _r: None, on_fail=lambda _m: None)

    # -- worker plumbing -----------------------------------------------

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busy_changed.emit(value)

    def _run(self, fn, on_done, *, on_fail=None, busy: bool = False) -> _Worker:
        worker = _Worker(fn, parent=self)

        def _cleanup():
            worker.deleteLater()
            self._workers.discard(worker)
            if busy:
                self._set_busy(False)

        def _handle_done(result):
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001
                self.error.emit(str(exc))

        def _handle_fail(message: str):
            if on_fail is not None:
                on_fail(message)
            else:
                self.error.emit(message)

        worker.done.connect(_handle_done)
        worker.failed.connect(_handle_fail)
        worker.finished.connect(_cleanup)
        self._workers.add(worker)
        if busy:
            self._set_busy(True)
        worker.start()
        return worker

    def shutdown(self) -> None:
        for worker in list(self._workers):
            try:
                worker.requestInterruption()
                if not worker.wait(2000):
                    worker.terminate()
                    worker.wait(500)
            except Exception:  # noqa: BLE001
                pass
        self._workers.clear()
