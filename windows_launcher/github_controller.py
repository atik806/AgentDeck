"""Qt bridge for the GitHub plugin -- the surface ``plugins_panel`` talks to.

Mirrors ``account.AccountController`` exactly: signals up top, blocking work on
short-lived ``QThread`` workers, results back as queued signals on the GUI
thread, every failure surfaced as :attr:`error` and swallowed.

``github_auth`` / ``github_api`` / ``github_mcp`` / ``plugin_store`` do the real
work and stay Qt-free. This object owns:

* the connect (device-flow) / disconnect lifecycle,
* the local token vault + ``plugins.json`` metadata,
* a best-effort mirror of the *metadata* (never the token) to the account's
  ``public.plugin_connections`` row,
* wiring a connected token into a workspace folder's agent config on demand
  (:meth:`ensure_wired`) and tearing it out again (:meth:`unwire_all`).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import github_auth
import github_mcp
from github_auth import DeviceFlow, GitHubAuthError, GitHubToken, GitHubTokenStore
from plugin_store import (
    DEFAULT_CAPABILITIES,
    GITHUB,
    PluginConnection,
    PluginStore,
    normalise_capabilities,
)

__all__ = ["GitHubController"]


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


class _ConnectWorker(QThread):
    """Runs one device-flow connect: emit the user code, then poll to authorised."""

    code_ready = Signal(dict)
    done = Signal(object)          # GitHubToken
    failed = Signal(str)

    def __init__(self, flow: DeviceFlow, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._flow = flow
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            device = self._flow.start()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or "GitHub sign-in couldn't start.")
            return
        self.code_ready.emit(
            {
                "user_code": device.user_code,
                "verification_uri": device.verification_uri,
                "expires_in": device.expires_in,
            }
        )
        deadline = time.monotonic() + min(self._flow.timeout, device.expires_in)
        interval = max(1, device.interval)
        while time.monotonic() < deadline:
            for _ in range(interval * 4):
                if self._cancel or self.isInterruptionRequested():
                    self.failed.emit("Sign-in was cancelled.")
                    return
                self.msleep(250)
            try:
                token = self._flow.poll_once()
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(str(exc) or "GitHub sign-in failed.")
                return
            if token is not None:
                self.done.emit(token)
                return
        self.failed.emit("Timed out waiting for the GitHub authorisation.")


class GitHubController(QObject):
    """The GitHub-plugin surface. Safe to construct unconditionally."""

    connected = Signal(dict)          # {"login": ...}
    disconnected = Signal()
    busy_changed = Signal(bool)
    error = Signal(str)
    device_code_ready = Signal(dict)  # {"user_code", "verification_uri", "expires_in"}
    repos_ready = Signal(list)
    state_changed = Signal()          # capabilities / automation edited

    def __init__(self, account=None, config: Optional[dict] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._account = account
        self._config = config if config is not None else {}
        self._tokens = GitHubTokenStore()
        self._store = PluginStore()
        self._token: Optional[GitHubToken] = None
        self._busy = False
        self._workers: set = set()
        self._connect_worker: Optional[_ConnectWorker] = None
        #: folders we've injected MCP config into this session, so disconnect can
        #: pull it back out.
        self._wired: set[str] = set()
        #: last successful list_repos() payload -- the review dialog reads it.
        self._last_repos: list = []

        try:
            self._token = self._tokens.load()
        except Exception:  # noqa: BLE001
            self._token = None

        # Already connected from a previous run? Make sure the MCP server is in
        # Claude Code's config now, so a pane started this session has the tools
        # without the user reconnecting. Deferred so __init__ never blocks.
        if self.is_connected:
            QTimer.singleShot(0, self.ensure_wired)

    # -- state -----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._token is not None and self._store.is_connected(GITHUB)

    @property
    def connection(self) -> Optional[PluginConnection]:
        return self._store.get(GITHUB)

    @property
    def login(self) -> str:
        conn = self.connection
        return conn.login if conn else ""

    @property
    def is_busy(self) -> bool:
        return self._busy

    # -- connect -------------------------------------------------------------

    def start_connect(self) -> None:
        if self._busy:
            return
        if not github_auth.GITHUB_CLIENT_ID:
            self.error.emit(
                "GitHub isn't configured for this AgentDeck build yet "
                "(no GitHub App client id — see docs/PLUGINS.md)."
            )
            return
        self._set_busy(True)
        flow = DeviceFlow()
        worker = _ConnectWorker(flow, parent=self)
        self._connect_worker = worker
        worker.code_ready.connect(self.device_code_ready.emit)
        worker.done.connect(self._on_connect_token)
        worker.failed.connect(self._on_connect_failed)
        worker.finished.connect(lambda: self._retire_connect_worker(worker))
        worker.start()

    def cancel_connect(self) -> None:
        if self._connect_worker is not None:
            self._connect_worker.cancel()
            self._connect_worker.requestInterruption()

    def _retire_connect_worker(self, worker: _ConnectWorker) -> None:
        worker.deleteLater()
        if self._connect_worker is worker:
            self._connect_worker = None
        self._set_busy(False)

    def _on_connect_failed(self, message: str) -> None:
        if "cancel" not in message.lower():
            self.error.emit(message)

    def _on_connect_token(self, token: GitHubToken) -> None:
        # Learn the login, persist, mirror -- all off the GUI thread.
        def _do():
            from github_api import whoami

            info = {}
            try:
                info = whoami(token.access_token)
            except GitHubAuthError:
                pass
            return info

        def _done(info):
            self._token = token
            self._tokens.save(token)
            login = (info or {}).get("login") or ""
            existing = self._store.get(GITHUB)
            caps = existing.capabilities if existing else list(DEFAULT_CAPABILITIES)
            auto = existing.automation if existing else {}
            conn = PluginConnection(GITHUB, login=login, capabilities=caps, automation=auto)
            self._store.put(conn)
            self._mirror_up(conn)
            self.ensure_wired()
            self.connected.emit({"login": login})

        self._run(_do, _done, on_fail=lambda m: self._finish_after_token(token))

    def _finish_after_token(self, token: GitHubToken) -> None:
        # whoami failed but we still have a valid token -- connect anyway.
        self._token = token
        self._tokens.save(token)
        conn = self._store.get(GITHUB) or PluginConnection(GITHUB)
        self._store.put(conn)
        self.ensure_wired()
        self.connected.emit({"login": conn.login})

    # -- disconnect --------------------------------------------------------

    def disconnect(self) -> None:
        if self._busy:
            return
        token = self._token
        self.unwire_all()

        def _do():
            if token is not None:
                github_auth.revoke(token)
            return None

        def _finish(_r=None):
            self._token = None
            self._tokens.clear()
            self._store.remove(GITHUB)
            self._mirror_delete()
            self.disconnected.emit()

        self._run(_do, _finish, on_fail=lambda _m: _finish(), busy=True)

    # -- capability editing ----------------------------------------------

    def set_capabilities(self, caps) -> None:
        conn = self._store.update(GITHUB, capabilities=normalise_capabilities(caps))
        if conn is not None:
            self._mirror_up(conn)
            self._rewire()
            self.state_changed.emit()

    def set_automation(self, capability: str, mode: str) -> None:
        conn = self.connection
        if conn is None:
            return
        auto = dict(conn.automation)
        auto[capability] = "auto" if mode == "auto" else "ask"
        updated = self._store.update(GITHUB, automation=auto)
        if updated is not None:
            self._mirror_up(updated)
            self.state_changed.emit()

    # -- repos ----------------------------------------------------------

    def fetch_repos(self) -> None:
        token = self._token
        if token is None:
            return

        def _do():
            from github_api import list_repos

            return list_repos(self._valid_token_blocking() or token.access_token)

        def _done(repos):
            if isinstance(repos, list):
                self._last_repos = repos
            self.repos_ready.emit(repos)

        self._run(_do, _done, on_fail=lambda _m: self.repos_ready.emit([]))

    # -- MCP wiring ----------------------------------------------------

    def _valid_token_blocking(self, timeout: float = 6.0) -> Optional[str]:
        """A non-expired access token, refreshing once if needed. Bounded; may
        return the stale token rather than block forever."""
        token = self._token
        if token is None:
            return None
        if not token.is_expired():
            return token.access_token
        if not token.refresh_token:
            return token.access_token

        box: dict = {}

        def _work():
            try:
                box["token"] = github_auth.refresh(token)
            except Exception:  # noqa: BLE001
                pass

        th = threading.Thread(target=_work, name="gh-refresh", daemon=True)
        th.start()
        th.join(max(0.5, timeout))
        fresh = box.get("token")
        if fresh is not None:
            self._token = fresh
            self._tokens.save(fresh)
            return fresh.access_token
        return token.access_token

    def _agent_is_claude(self, agent_command: Optional[str]) -> bool:
        """Whether AgentDeck's agent for this session is Claude Code.

        ``agent_command`` may be empty when ``claude`` isn't on the *packaged
        app's* PATH even though the user runs it fine in a pane -- so fall back to
        the configured agent key.
        """
        if agent_command and github_mcp.supports_agent(agent_command):
            return True
        return str(self._config.get("agent", "")).strip().lower() == "claude"

    def ensure_wired(self, folder: Optional[str] = None,
                     agent_command: Optional[str] = None) -> bool:
        """Add the GitHub MCP server to Claude Code's user-scope config.

        Folder-independent now (the server is user-scope), but ``folder`` is
        still passed through for the legacy ``.mcp.json`` cleanup. Best-effort;
        returns True if the config changed.
        """
        if not self.is_connected or not self._agent_is_claude(agent_command):
            return False
        conn = self.connection
        if conn is None:
            return False
        token = self._valid_token_blocking()
        if not token:
            return False
        try:
            changed = github_mcp.inject(folder, token, conn)
        except Exception:  # noqa: BLE001
            return False
        if folder:
            self._wired.add(str(folder))
        return changed

    #: Back-compat alias -- older name for the same thing.
    def wire_if_connected(self) -> bool:
        return self.ensure_wired()

    def _rewire(self) -> None:
        """Re-inject after a capability change (toolsets differ)."""
        self.ensure_wired()

    def unwire_all(self) -> None:
        self._wired.clear()
        try:
            github_mcp.remove()
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
            "provider": GITHUB,
            "external_login": conn.login,
            "capabilities": conn.capabilities,
            "automation": conn.automation,
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
                params={"provider": f"eq.{GITHUB}"},
                timeout=15,
            )
            return None

        self._run(_do, lambda _r: None, on_fail=lambda _m: None)

    def log_run(self, action: str, target: str = "", summary: str = "") -> None:
        """Append a row to ``public.plugin_runs`` -- the automation audit trail."""
        acc = self._account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return
        session = getattr(acc, "session", None)
        if session is None:
            return
        try:
            from version import __version__ as ver
        except Exception:  # noqa: BLE001
            ver = ""
        row = {
            "user_id": getattr(session, "user_id", ""),
            "provider": GITHUB,
            "action": action,
            "target": target,
            "summary": summary[:2000],
            "app_version": ver,
        }

        def _do():
            import supabase_auth

            return supabase_auth.rest_insert("plugin_runs", row, session.access_token)

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
        if self._connect_worker is not None:
            self._connect_worker.cancel()
            self._connect_worker.requestInterruption()
        for worker in list(self._workers) + (
            [self._connect_worker] if self._connect_worker else []
        ):
            try:
                worker.requestInterruption()
                if not worker.wait(2000):
                    worker.terminate()
                    worker.wait(500)
            except Exception:  # noqa: BLE001
                pass
        self._workers.clear()
