"""Qt bridge for the Google Drive plugin -- the surface ``plugins_panel`` talks to.

Closest to ``supabase_controller``: the connection carries a small ``settings``
dict that shapes the injected server, and connecting validates before it writes.
Two things make this one different from every other plugin, both flowing from
Google having no Dynamic Client Registration (see ``gdrive_mcp``):

* **There is a credential.** ``start_connect`` takes the user's Desktop-app
  client id *and secret*. The id goes in ``plugins.json``; the secret goes to
  ``gdrive_secret.GDriveSecretStore`` (DPAPI / keyring) and is handed to Claude
  Code by ``gdrive_mcp.seed_secret``. Neither is ever mirrored to the account --
  ``_mirror_up`` sends a bare presence row, exactly as the thin plugins do.
* **Connecting runs a subprocess**, so it happens on a worker thread and the
  card shows a busy state. A failure is not silent: ``error`` carries the
  ``claude mcp add`` command so the user can finish by hand and hit *Re-sync to
  agents*.

``gdrive_mcp`` / ``gdrive_secret`` / ``plugin_store`` do the real work and stay
Qt-free. See docs/PLUGINS.md 18.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import gdrive_mcp
from gdrive_secret import GDriveSecretStore
from plugin_store import GDRIVE, PluginConnection, PluginStore

__all__ = ["GDriveController", "valid_client_id"]

#: Google OAuth client ids look like ``123456789-abc123.apps.googleusercontent.com``.
#: Kept deliberately loose -- the point is to catch "empty" and "pasted the
#: secret into the id box", not to out-guess Google on the exact id format.
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._\-]+\.apps\.googleusercontent\.com$")


def valid_client_id(value: str) -> bool:
    return bool(_CLIENT_ID_RE.match((value or "").strip()))


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


class GDriveController(QObject):
    """The Google Drive-plugin surface. Safe to construct unconditionally."""

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
        self._vault = GDriveSecretStore()
        self._busy = False
        self._workers: set = set()

        # Already connected from a previous run? Make sure the MCP server is in
        # the target agent's config now. Same-file writers are serialised by
        # mcp_io.locked(), so no stagger is needed.
        if self.is_connected:
            QTimer.singleShot(0, self.ensure_wired)

    # -- state -----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._store.is_connected(GDRIVE)

    @property
    def connection(self) -> Optional[PluginConnection]:
        return self._store.get(GDRIVE)

    @property
    def login(self) -> str:
        return ""

    @property
    def client_id(self) -> str:
        conn = self.connection
        return conn.settings.get("client_id", "") if conn else ""

    @property
    def has_secret(self) -> bool:
        """Whether a client secret is in the vault. The detail page uses this to
        show "stored" rather than an empty password box."""
        return bool(self._vault.load())

    @property
    def is_busy(self) -> bool:
        return self._busy

    # -- connect / disconnect --------------------------------------------

    def start_connect(self, client_id: str, client_secret: str) -> bool:
        """Connect with a Desktop-app OAuth client from the user's own Google
        Cloud project. Returns False (and emits ``error``) without connecting if
        either field is missing or the id is malformed.

        The write order matters. ``gdrive_mcp.seed_secret`` shells out to
        ``claude mcp add``, which creates the server entry itself -- so we check
        for a *foreign* ``gdrive`` server first (the guarantee every other plugin
        gets free from ``write_server``), then seed, then re-write the entry with
        our marker and scopes via ``inject(seeded=True)``.
        """
        if self._busy:
            return False
        cid = (client_id or "").strip()
        secret = (client_secret or "").strip()
        if not cid or not secret:
            self.error.emit(
                "Paste both the Client ID and the Client secret from your Google "
                "Cloud OAuth client before connecting."
            )
            return False
        if not valid_client_id(cid):
            self.error.emit(
                "That doesn't look like a Google OAuth client ID -- it should end "
                "in .apps.googleusercontent.com."
            )
            return False

        try:
            foreign = gdrive_mcp.foreign_server_agents()
        except Exception:  # noqa: BLE001
            foreign = []
        if foreign:
            self.error.emit(
                "You already have your own \"gdrive\" MCP server configured "
                f"({', '.join(foreign)}). AgentDeck won't overwrite it -- rename "
                "or remove it first."
            )
            return False

        if not self._vault.save(secret):
            self.error.emit(
                "Couldn't store the client secret securely on this machine, so "
                "nothing was connected."
            )
            return False

        def _do():
            return gdrive_mcp.seed_secret(cid, secret)

        def _done(result):
            ok, message = result
            if not ok:
                self._vault.clear()
                self.error.emit(message)
                return
            conn = PluginConnection(
                GDRIVE,
                settings={"client_id": cid, "scopes": gdrive_mcp.DEFAULT_SCOPES},
            )
            self._store.put(conn)
            self.ensure_wired(seeded=True)
            self._mirror_up(conn)
            self.connected.emit({})

        def _fail(message: str):
            self._vault.clear()
            self.error.emit(message)

        self._run(_do, _done, on_fail=_fail, busy=True)
        return True

    def update_settings(self, *, client_id: Optional[str] = None,
                        client_secret: Optional[str] = None) -> bool:
        """Change the connected client id and/or secret and re-inject in place --
        no disconnect/reconnect needed. An empty ``client_secret`` means "leave
        the stored one alone", so editing only the id doesn't wipe the secret.
        """
        conn = self.connection
        if conn is None or self._busy:
            return False

        cid = (client_id if client_id is not None else conn.settings.get("client_id", "")).strip()
        if not valid_client_id(cid):
            self.error.emit(
                "That doesn't look like a Google OAuth client ID -- it should end "
                "in .apps.googleusercontent.com."
            )
            return False

        secret = (client_secret or "").strip() or self._vault.load()
        if not secret:
            self.error.emit("No client secret stored -- paste it again to reconnect.")
            return False
        if client_secret and not self._vault.save(secret):
            self.error.emit("Couldn't store the client secret securely on this machine.")
            return False

        def _do():
            return gdrive_mcp.seed_secret(cid, secret)

        def _done(result):
            ok, message = result
            if not ok:
                self.error.emit(message)
                return
            conn.settings["client_id"] = cid
            conn.settings.setdefault("scopes", gdrive_mcp.DEFAULT_SCOPES)
            self._store.put(conn)
            self.ensure_wired(seeded=True)
            self._mirror_up(conn)
            self.connected.emit({})

        self._run(_do, _done, busy=True)
        return True

    def disconnect(self) -> None:
        if self._busy:
            return
        self.unwire_all()
        # Only an explicit disconnect drops Claude Code's own copy of the
        # secret -- NOT app shutdown, which also calls unwire_all().
        try:
            gdrive_mcp.forget_secret()
        except Exception:  # noqa: BLE001
            pass
        self._vault.clear()
        self._store.remove(GDRIVE)
        self._mirror_delete()
        self.disconnected.emit()

    # -- MCP wiring ------------------------------------------------------

    def _target_agent_keys(self, agent_command: Optional[str] = None) -> list[str]:
        """Which agents to write the Drive MCP server into -- the workspace's
        agent plus, unless ``plugins_wire_all_agents`` is off, every installed
        agent. ``gdrive_mcp.inject`` further filters to the static-OAuth set,
        which today is Claude Code alone."""
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
                     agent_command: Optional[str] = None,
                     *, seeded: bool = False) -> bool:
        """Add the Drive MCP server to every static-OAuth-capable target agent's
        user-scope config. Best-effort; returns True if any config changed."""
        conn = self.connection
        if conn is None:
            return False
        keys = self._target_agent_keys(agent_command)
        if not keys:
            return False
        try:
            return gdrive_mcp.inject(settings=conn.settings, agent_keys=keys, seeded=seeded)
        except Exception:  # noqa: BLE001
            return False

    #: Back-compat alias -- older name for the same thing.
    def wire_if_connected(self) -> bool:
        return self.ensure_wired()

    def unwire_all(self) -> None:
        """Drop our MCP entry from every agent config.

        Called on **app shutdown** as well as disconnect (see
        ``terminal_panel._shutdown_all``), so it must stay cheap and must not
        touch the credential: Claude Code keys its stored secret by server name
        and URL, both of which we re-write unchanged on the next launch, so the
        secret survives a restart and the plugin keeps working. Dropping it
        belongs to :meth:`disconnect` alone.
        """
        try:
            gdrive_mcp.remove()
        except Exception:  # noqa: BLE001
            pass

    # -- Supabase mirror (metadata only, best-effort) --------------------
    #
    # Presence only. The client id stays in plugins.json and the secret in the
    # DPAPI vault -- neither leaves the machine, same trust boundary as a GitHub
    # token (see docs/PLUGINS.md 18).

    def _mirror_up(self, conn: PluginConnection) -> None:
        acc = self._account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return
        session = getattr(acc, "session", None)
        if session is None:
            return
        row = {
            "provider": GDRIVE,
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
                params={"provider": f"eq.{GDRIVE}"},
                timeout=15,
            )
            return None

        self._run(_do, lambda _r: None, on_fail=lambda _m: None)

    # -- worker plumbing -------------------------------------------------

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
