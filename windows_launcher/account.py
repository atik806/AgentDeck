"""Qt bridge over Supabase auth -- the "account" surface the panel talks to.

``supabase_auth`` does the actual work (PKCE loopback sign-in, token refresh,
REST calls, on-disk session) and is deliberately Qt-free. This module wraps it
in a :class:`QObject` so the UI can drive it with signals, exactly the way
``updater.py`` wraps Velopack:

* every blocking call runs on a short-lived :class:`_Worker` (``QThread``);
  results come back as queued signals on the GUI thread,
* the whole thing degrades to a no-op when there is no session and the user
  never signs in -- AgentDeck stays 100% usable signed-out,
* a network failure is surfaced as :attr:`error` and swallowed, never raised.

Design mirrors ``updater.UpdateController``: signals up top, ``_set_busy`` for
the one-op-at-a-time flows (sign in / sign out), ``finished -> deleteLater`` so
worker threads don't pile up.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import supabase_auth
from supabase_auth import AuthError
from config import _get_config_dir, save_config

__all__ = ["AccountController", "CLOUD_KEYS"]

#: The only config keys cloud sync ever reads or writes. Everything else
#: (window geometry, voice overlay position, update bookkeeping) is
#: machine-local and stays put.
CLOUD_KEYS = [
    "working_folder",
    "recent_folders",
    "agent",
    "agent_command",
    "default_count",
    "layout",
    "font_size",
    "default_shell",
    "theme",
]

#: Downloaded Google avatar, cached beside config.json so the chip has a picture
#: the instant the window opens instead of after a round-trip.
_AVATAR_CACHE = "avatar.img"


def _filter_cloud(data: dict) -> dict:
    """Keep only the keys cloud sync is allowed to touch."""
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in CLOUD_KEYS if k in data}


def _looks_like_auth_error(exc: BaseException) -> bool:
    if isinstance(exc, AuthError):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in ("401", "unauthorized", "jwt", "expired", "invalid token"))


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _Worker(QThread):
    """Runs one callable off the GUI thread; emits its result or the error."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - reported, never propagated
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        self.done.emit(result)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class AccountController(QObject):
    """The account surface. Safe to construct unconditionally.

    Signals (GUI thread):
        signed_in(user)      a session was established (fresh or restored)
        signed_out()         the session ended (sign out, or a dead refresh)
        error(message)       a non-fatal problem, already handled
        busy_changed(bool)   a sign in / sign out started or finished
        avatar_ready(bytes)  raw avatar image bytes (cached or freshly fetched)
        profile_ready(dict)  the row from public.profiles (carries ``plan``)
    """

    signed_in = Signal(dict)
    signed_out = Signal()
    error = Signal(str)
    busy_changed = Signal(bool)
    avatar_ready = Signal(bytes)
    profile_ready = Signal(dict)

    def __init__(self, config: Optional[dict] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._config = config if config is not None else {}
        self._store = supabase_auth.SessionStore()
        self._session = None
        self._plan = "free"
        self._busy = False
        self._cancel_signin = False
        self._workers: set[_Worker] = set()

        try:
            self._session = self._store.load()
        except Exception:  # noqa: BLE001 - a corrupt store is just "not signed in"
            self._session = None

        self._cached_avatar = self._read_cached_avatar()

        if self._session is not None:
            self._sync_email(self._session.email)
            if self._session.is_expired():
                # Don't block startup on the network: refresh in the background,
                # and if it fails the user simply lands signed-out.
                self._refresh(reason="startup")

    # -- state ---------------------------------------------------------------

    @property
    def is_signed_in(self) -> bool:
        return self._session is not None

    @property
    def user(self) -> Optional[dict]:
        return self._session.user if self._session is not None else None

    @property
    def session(self):
        return self._session

    @property
    def email(self) -> str:
        if self._session is not None:
            return self._session.email
        return self._config.get("account_email", "") or ""

    @property
    def display_name(self) -> str:
        if self._session is not None:
            return self._session.display_name
        email = self.email
        return email.split("@")[0] if email else ""

    @property
    def avatar_url(self) -> str:
        return self._session.avatar_url if self._session is not None else ""

    @property
    def plan(self) -> str:
        return self._plan or "free"

    def needs_login(self) -> bool:
        """True when the login window must be shown before the panel opens.

        A signed-in account is mandatory, so this is simply "no session yet".
        """
        return not self.is_signed_in

    # -- actions -----------------------------------------------------------------

    def sign_in_with_google(self) -> None:
        if self._busy:
            return
        self._cancel_signin = False
        flow = supabase_auth.GoogleSignIn()

        def _do():
            return flow.run(should_cancel=lambda: self._cancel_signin)

        self._run(_do, self._on_signed_in, on_fail=self._on_signin_failed, busy=True)

    def cancel_sign_in(self) -> None:
        """Ask an in-flight sign-in to give up. The worker checks this ~4x/s."""
        self._cancel_signin = True

    def sign_out(self) -> None:
        if self._busy:
            return
        session = self._session

        def _do():
            if session is not None:
                supabase_auth.sign_out(session)  # best-effort, never raises
            return None

        # Local sign-out happens whether or not the server call worked.
        self._run(_do, lambda _r: self._finish_sign_out(),
                  on_fail=lambda _m: self._finish_sign_out(), busy=True)

    def refresh_now(self) -> None:
        self._refresh(reason="manual")

    def fetch_avatar(self) -> None:
        if self._cached_avatar:
            data = self._cached_avatar
            QTimer.singleShot(0, lambda: self.avatar_ready.emit(data))

        url = self.avatar_url
        if not url:
            return

        def _do():
            import requests

            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            return resp.content

        def _done(content):
            if content and content != self._cached_avatar:
                self._cached_avatar = content
                self._write_cached_avatar(content)
                self.avatar_ready.emit(content)

        self._run(_do, _done, on_fail=lambda _m: None)  # a missing avatar is silent

    def fetch_profile(self) -> None:
        if self._session is None:
            return
        uid = self._session.user_id

        def _do():
            rows = self._rest_with_reauth(
                lambda tok: supabase_auth.rest_select(
                    "profiles", tok,
                    params={"id": f"eq.{uid}", "select": "*"},
                )
            )
            return rows[0] if rows else {}

        def _done(profile):
            if isinstance(profile, dict) and profile:
                plan = profile.get("plan")
                if isinstance(plan, str) and plan:
                    self._plan = plan
                self.profile_ready.emit(profile)

        self._run(_do, _done, on_fail=self._on_rest_failed)

    def pull_cloud_settings(self) -> Optional[dict]:
        """Read this account's saved settings. Blocks briefly (<=8s), then gives up.

        Called once, right after a sign-in, by the integration code. Returns the
        cloud ``data`` filtered to :data:`CLOUD_KEYS`, or ``None`` when sync is
        off / not signed in / the read failed.
        """
        if not self._config.get("account_cloud_sync", True) or self._session is None:
            return None

        uid = self._session.user_id
        box: dict = {}

        def _work():
            try:
                rows = self._rest_with_reauth(
                    lambda tok: supabase_auth.rest_select(
                        "user_settings", tok,
                        params={"user_id": f"eq.{uid}", "select": "data"},
                    )
                )
                if rows and isinstance(rows[0], dict):
                    data = rows[0].get("data")
                    if isinstance(data, dict):
                        box["data"] = _filter_cloud(data)
            except Exception:  # noqa: BLE001 - a failed pull just means "no cloud data"
                pass

        thread = threading.Thread(target=_work, name="account-pull", daemon=True)
        thread.start()
        thread.join(8.0)
        return box.get("data")

    def push_cloud_settings(self, data: dict) -> None:
        if not self._config.get("account_cloud_sync", True) or self._session is None:
            return
        payload = _filter_cloud(data)
        if not payload:
            return
        uid = self._session.user_id

        def _do():
            return self._rest_with_reauth(
                lambda tok: supabase_auth.rest_upsert(
                    "user_settings", {"user_id": uid, "data": payload}, tok,
                )
            )

        self._run(_do, lambda _r: None, on_fail=self._on_rest_failed)

    def shutdown(self) -> None:
        """Stop background work for the window's closeEvent."""
        self._cancel_signin = True
        for worker in list(self._workers):
            try:
                worker.requestInterruption()
                if not worker.wait(2500):
                    worker.terminate()
                    worker.wait(500)
            except Exception:  # noqa: BLE001
                pass
        self._workers.clear()

    # -- internals -------------------------------------------------------------

    def _refresh(self, *, reason: str) -> None:
        session = self._session
        if session is None:
            return

        def _do():
            return supabase_auth.refresh(session)

        def _done(new_session):
            self._session = new_session
            self._save_session(new_session)
            self._sync_email(new_session.email)

        def _fail(_msg):
            # The refresh token is dead -- there is no session any more.
            self._finish_sign_out()
            self.error.emit("Your session expired -- please sign in again.")

        self._run(_do, _done, on_fail=_fail)

    def _on_signed_in(self, session) -> None:
        self._session = session
        self._plan = "free"
        self._save_session(session)
        self._sync_email(session.email)
        self.signed_in.emit(session.user or {})
        self.fetch_avatar()
        self.fetch_profile()

    def _on_signin_failed(self, message: str) -> None:
        low = message.lower()
        if "cancel" in low:
            return  # the user backed out; not an error to report
        if "provider is not enabled" in low:
            self.error.emit(
                "Google sign-in isn't enabled on the server yet. "
                "Please try again later."
            )
            return
        self.error.emit(message)

    def _finish_sign_out(self) -> None:
        self._session = None
        self._plan = "free"
        try:
            self._store.clear()
        except Exception:  # noqa: BLE001
            pass
        self._clear_cached_avatar()
        if self._config.get("account_email"):
            self._config["account_email"] = ""
            self._save_config_quietly()
        self.signed_out.emit()

    def _rest_with_reauth(self, call: Callable[[str], object]) -> object:
        """Run ``call(access_token)``, refreshing the token once on a 401.

        Runs on whatever thread called it (a worker for push/profile, the GUI
        thread for the bounded ``pull_cloud_settings``). ``self._session`` is a
        plain reference swap, safe enough under the GIL for this.
        """
        session = self._session
        if session is None:
            raise AuthError("not signed in")
        try:
            return call(session.access_token)
        except Exception as exc:  # noqa: BLE001
            if not _looks_like_auth_error(exc):
                raise

        try:
            new_session = supabase_auth.refresh(session)
        except Exception:  # noqa: BLE001
            raise AuthError("session expired")

        self._session = new_session
        self._save_session(new_session)
        try:
            return call(new_session.access_token)
        except Exception as exc:  # noqa: BLE001
            if _looks_like_auth_error(exc):
                raise AuthError("session expired")
            raise

    def _on_rest_failed(self, message: str) -> None:
        low = message.lower()
        if "session expired" in low or "not signed in" in low:
            self._finish_sign_out()
            self.error.emit("Your session expired -- please sign in again.")
        else:
            self.error.emit(message)

    # -- worker plumbing ------------------------------------------------------

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busy_changed.emit(value)

    def _run(
        self,
        fn: Callable[[], object],
        on_done: Callable[[object], None],
        *,
        on_fail: Optional[Callable[[str], None]] = None,
        busy: bool = False,
    ) -> _Worker:
        worker = _Worker(fn, parent=self)

        def _cleanup() -> None:
            worker.deleteLater()
            self._workers.discard(worker)
            if busy:
                self._set_busy(False)

        def _handle_done(result) -> None:
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001 - a slot must not kill the loop
                self.error.emit(str(exc))

        def _handle_fail(message: str) -> None:
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

    # -- session / email persistence ----------------------------------------

    def _save_session(self, session) -> None:
        try:
            self._store.save(session)
        except Exception:  # noqa: BLE001 - unable to persist just means re-login later
            pass

    def _sync_email(self, email: str) -> None:
        if email and self._config.get("account_email") != email:
            self._config["account_email"] = email
            self._save_config_quietly()

    def _save_config_quietly(self) -> None:
        try:
            save_config(self._config)
        except Exception:  # noqa: BLE001
            pass

    # -- avatar cache ------------------------------------------------------------

    def _avatar_path(self):
        return _get_config_dir() / _AVATAR_CACHE

    def _read_cached_avatar(self) -> bytes:
        try:
            path = self._avatar_path()
            if path.is_file():
                return path.read_bytes()
        except Exception:  # noqa: BLE001
            pass
        return b""

    def _write_cached_avatar(self, data: bytes) -> None:
        try:
            path = self._avatar_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except Exception:  # noqa: BLE001
            pass

    def _clear_cached_avatar(self) -> None:
        self._cached_avatar = b""
        try:
            self._avatar_path().unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
