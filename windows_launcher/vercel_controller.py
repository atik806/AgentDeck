"""Qt bridge for the Vercel plugin -- the surface ``plugins_panel`` talks to.

Mirrors ``github_controller.GitHubController`` but much thinner: Vercel's MCP
server is hosted and OAuth-only, and Claude Code owns the OAuth credentials (the
user runs ``/mcp`` in a pane). So there is no device flow, no token vault, no
capability model here. "Connecting" means: record the connection in
``plugins.json``, drop a tokenless server entry into ``~/.claude.json``, and
best-effort mirror the *metadata* to the account's ``plugin_connections`` row.

``vercel_mcp`` / ``plugin_store`` do the real work and stay Qt-free.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import vercel_mcp
from plugin_store import VERCEL, PluginConnection, PluginStore

__all__ = ["VercelController"]


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


class VercelController(QObject):
    """The Vercel-plugin surface. Safe to construct unconditionally."""

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
        return self._store.is_connected(VERCEL)

    @property
    def connection(self) -> Optional[PluginConnection]:
        return self._store.get(VERCEL)

    @property
    def login(self) -> str:
        return ""

    @property
    def is_busy(self) -> bool:
        return self._busy

    # -- connect / disconnect ------------------------------------------------

    def start_connect(self) -> None:
        if self._busy:
            return
        conn = PluginConnection(VERCEL)
        self._store.put(conn)
        self.ensure_wired()
        self._mirror_up(conn)
        self.connected.emit({})

    def disconnect(self) -> None:
        if self._busy:
            return
        self.unwire_all()
        self._store.remove(VERCEL)
        self._mirror_delete()
        self.disconnected.emit()

    # -- MCP wiring ----------------------------------------------------

    def _target_agent_keys(self, agent_command: Optional[str] = None) -> list[str]:
        """Which agents to write the Vercel MCP server into -- the workspace's
        agent plus, unless ``plugins_wire_all_agents`` is off, every installed
        agent. ``vercel_mcp.inject`` further filters to the OAuth-capable set."""
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
        """Add the Vercel MCP server to every OAuth-capable target agent's
        user-scope config. Best-effort; returns True if any config changed."""
        if not self.is_connected:
            return False
        keys = self._target_agent_keys(agent_command)
        if not keys:
            return False
        try:
            return vercel_mcp.inject(agent_keys=keys)
        except Exception:  # noqa: BLE001
            return False

    #: Back-compat alias -- older name for the same thing.
    def wire_if_connected(self) -> bool:
        return self.ensure_wired()

    def unwire_all(self) -> None:
        try:
            vercel_mcp.remove()
        except Exception:  # noqa: BLE001
            pass

    # -- Supabase mirror (metadata only, best-effort) ------------------

    def _mirror_up(self, conn: PluginConnection) -> None:
        acc = self._account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return
        session = getattr(acc, "session", None)
        if session is None:
            return
        row = {
            "provider": VERCEL,
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
                params={"provider": f"eq.{VERCEL}"},
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
