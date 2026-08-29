"""Supabase auth for AgentDeck -- Google sign-in over a loopback PKCE flow.

Qt-free on purpose (same rule as ``agents.py``): plain ``requests`` + stdlib, so
it can be unit-tested headless and driven from a worker thread without pulling Qt
in. The Qt bridge lives in ``account.py``.

Why a loopback flow instead of the ``supabase`` SDK:

* A desktop app has no server to catch an OAuth redirect, so we stand up a
  throwaway HTTP server on ``127.0.0.1`` for exactly one request, hand Supabase
  that address as ``redirect_to``, and read the ``?code=`` it bounces back.
* PKCE (RFC 7636) keeps a client secret out of the binary -- the one-time
  ``code_verifier`` never leaves this process.
* The full SDK drags in httpx/gotrue/postgrest/realtime/websockets/pydantic,
  which is a lot of PyInstaller hidden-import grief for four REST calls.

The Supabase project must have the Google provider enabled and
``http://127.0.0.1:*`` in its redirect allowlist -- see ``docs/ACCOUNTS.md``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, quote, urlsplit

import requests

__all__ = [
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "AuthError",
    "Session",
    "build_pkce",
    "GoogleSignIn",
    "refresh",
    "sign_out",
    "fetch_user",
    "rest_select",
    "rest_upsert",
    "SessionStore",
]

_DEFAULT_URL = "https://pxlrabmoohrfaptsotzx.supabase.co"
#: Publishable ("anon"-class) key -- safe to ship in a client. Never put the
#: service-role key or the database password anywhere near this file.
_DEFAULT_KEY = "sb_publishable_tduSnEfpGLNFVWMzqx49LA_Ip-JOSj-"

SUPABASE_URL = (os.environ.get("AGENTDECK_SUPABASE_URL") or _DEFAULT_URL).rstrip("/")
SUPABASE_KEY = os.environ.get("AGENTDECK_SUPABASE_KEY") or _DEFAULT_KEY

_HTTP_TIMEOUT = 30


class AuthError(Exception):
    """Anything that stops a sign-in / refresh from completing. Message is
    already phrased for a status line."""


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass
class Session:
    """One signed-in session: the tokens plus the GoTrue user object."""

    access_token: str
    refresh_token: str
    expires_at: int  # absolute epoch seconds
    user: dict = field(default_factory=dict)

    # -- derived views ---------------------------------------------------------

    @property
    def _meta(self) -> dict:
        meta = self.user.get("user_metadata") if isinstance(self.user, dict) else None
        return meta if isinstance(meta, dict) else {}

    @property
    def email(self) -> str:
        return str((self.user or {}).get("email") or "").strip()

    @property
    def user_id(self) -> str:
        return str((self.user or {}).get("id") or "")

    @property
    def display_name(self) -> str:
        for key in ("full_name", "name", "display_name"):
            value = self._meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        email = self.email
        if "@" in email:
            return email.split("@", 1)[0]
        return email or "there"

    @property
    def avatar_url(self) -> str:
        for key in ("avatar_url", "picture"):
            value = self._meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    # -- lifetime ------------------------------------------------------------

    def is_expired(self, skew_seconds: int = 60) -> bool:
        """True once we are within ``skew_seconds`` of expiry (so callers refresh
        early rather than on a 401)."""
        return time.time() >= (self.expires_at - max(0, skew_seconds))

    # -- (de)serialisation --------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": int(self.expires_at),
            "user": self.user or {},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        if not isinstance(data, dict):
            raise ValueError("session payload is not an object")
        user = data.get("user")
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=int(data.get("expires_at") or 0),
            user=user if isinstance(user, dict) else {},
        )

    @classmethod
    def from_token_response(cls, data: dict) -> "Session":
        """Build from a GoTrue ``/token`` response (``expires_at`` absolute, or
        ``expires_in`` relative)."""
        expires_at = data.get("expires_at")
        if not expires_at:
            try:
                expires_at = time.time() + float(data.get("expires_in") or 3600)
            except (TypeError, ValueError):
                expires_at = time.time() + 3600
        user = data.get("user")
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=int(expires_at),
            user=user if isinstance(user, dict) else {},
        )


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def build_pkce() -> tuple[str, str]:
    """A fresh ``(code_verifier, s256_challenge)`` pair, both base64url, no pad."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers(key: str, token: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    out = {"apikey": key}
    if token:
        out["Authorization"] = f"Bearer {token}"
    if extra:
        out.update(extra)
    return out


def _error_message(resp) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("msg", "error_description", "message", "error", "hint"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return (getattr(resp, "text", "") or "").strip()[:200]


def _check(resp, what: str) -> None:
    if getattr(resp, "ok", False):
        return
    message = _error_message(resp)
    raise AuthError(f"{what} failed ({resp.status_code})" + (f": {message}" if message else ""))


def _google_enabled(url: str, key: str) -> bool:
    """Best-effort provider probe. Returns False only when we can *positively*
    confirm Google is off, so a transient network blip never blocks a retry."""
    try:
        resp = requests.get(f"{url}/auth/v1/settings", headers=_headers(key), timeout=15)
        if resp.ok:
            body = resp.json()
            external = body.get("external") if isinstance(body, dict) else None
            if isinstance(external, dict) and "google" in external:
                return bool(external["google"])
    except (requests.RequestException, ValueError):
        pass
    return True


# ---------------------------------------------------------------------------
# Loopback OAuth callback server
# ---------------------------------------------------------------------------

_SUCCESS_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>AgentDeck</title><style>
 html,body{height:100%;margin:0}
 body{display:flex;align-items:center;justify-content:center;
   background:#1e1e2e;color:#cdd6f4;font:15px/1.5 "Segoe UI",system-ui,sans-serif}
 .card{text-align:center;padding:40px 48px;border:1px solid #313244;border-radius:16px;
   background:#181825;max-width:360px}
 .tick{width:52px;height:52px;border-radius:50%;margin:0 auto 18px;display:flex;
   align-items:center;justify-content:center;font-size:26px;color:#1e1e2e;
   background:linear-gradient(135deg,#89b4fa,#a6e3a1)}
 h1{font-size:19px;margin:0 0 8px}p{margin:0;color:#9399b2;font-size:13px}
</style></head><body><div class="card"><div class="tick">&#10003;</div>
<h1>You're signed in</h1><p>Head back to AgentDeck &mdash; you can close this tab.</p>
</div></body></html>"""

_ERROR_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>AgentDeck</title><style>
 html,body{height:100%;margin:0}
 body{display:flex;align-items:center;justify-content:center;
   background:#1e1e2e;color:#cdd6f4;font:15px/1.5 "Segoe UI",system-ui,sans-serif}
 .card{text-align:center;padding:40px 48px;border:1px solid #45373d;border-radius:16px;
   background:#181825;max-width:360px}
 h1{font-size:19px;margin:0 0 8px;color:#f38ba8}p{margin:0;color:#9399b2;font-size:13px}
</style></head><body><div class="card">
<h1>Sign-in didn't finish</h1><p>You can close this tab and try again in AgentDeck.</p>
</div></body></html>"""


class _CallbackServer(HTTPServer):
    # Stays False on purpose: on Windows SO_REUSEADDR would let another local
    # process re-bind this exact port and race us for the OAuth code.
    allow_reuse_address = False
    timeout = 0.4  # so handle_request() returns and we can poll for cancel

    def __init__(self, address: tuple[str, int], expected_state: str = ""):
        #: CSRF nonce placed in the authorize URL; a callback that echoes a
        #: ``state`` at all must echo this one.
        self.expected_state = expected_state
        super().__init__(address, _CallbackHandler)
        #: ``("code", value)`` or ``("error", description)`` once the browser hits us.
        self.result: Optional[tuple[str, str]] = None

    def server_bind(self) -> None:
        # Ask Windows for exclusive ownership of the port for the sign-in's
        # lifetime (POSIX is already exclusive without SO_REUSEADDR).
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            except OSError:
                pass
        super().server_bind()


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "AgentDeck/1"

    def log_message(self, *_args) -> None:  # noqa: N802 - silence stderr spam
        pass

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        split = urlsplit(self.path)

        # The provider redirects the browser here as a top-level navigation:
        # no Origin header, and always to "/". A request carrying an Origin, or
        # aimed at another path, is something else on the machine poking at the
        # port -- don't let it hand us a code.
        if self.headers.get("Origin") or split.path not in ("", "/"):
            self._respond(204, "")
            return

        try:
            query = parse_qs(split.query)
        except ValueError:
            query = {}
        code = (query.get("code") or [""])[0]
        error = (query.get("error") or [""])[0]
        state = (query.get("state") or [""])[0]

        expected = getattr(self.server, "expected_state", "")  # type: ignore[attr-defined]
        if (code or error) and expected and state and not secrets.compare_digest(state, expected):
            # A callback that echoes a state must echo ours -- this one didn't,
            # so it's forged. (PKCE still guards the exchange when the provider
            # echoes no state at all, which is why absence isn't rejected here.)
            self.server.result = (  # type: ignore[attr-defined]
                "error", "the sign-in callback failed a security check"
            )
            self._respond(400, _ERROR_HTML)
            return

        if code:
            self.server.result = ("code", code)  # type: ignore[attr-defined]
            self._respond(200, _SUCCESS_HTML)
        elif error:
            description = (query.get("error_description") or [error])[0]
            self.server.result = ("error", description or error)  # type: ignore[attr-defined]
            self._respond(200, _ERROR_HTML)
        else:
            # A stray hit (favicon, a bare "/"): ignore, keep serving.
            self._respond(204, "")

    def _respond(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# The sign-in flow
# ---------------------------------------------------------------------------

class GoogleSignIn:
    """Runs one loopback PKCE sign-in. Blocking -- call :meth:`run` off the GUI
    thread."""

    PREFERRED_PORT = 51737

    def __init__(
        self,
        *,
        url: str = SUPABASE_URL,
        key: str = SUPABASE_KEY,
        open_browser: Callable[[str], object] = webbrowser.open,
        timeout: float = 180.0,
    ):
        self._url = url.rstrip("/")
        self._key = key
        self._open_browser = open_browser
        self._timeout = float(timeout)
        self._redirect_uri = ""

    @property
    def redirect_uri(self) -> str:
        """The ``http://127.0.0.1:<port>`` handed to Supabase. Empty until
        :meth:`run` has bound the port."""
        return self._redirect_uri

    def _bind(self, expected_state: str) -> _CallbackServer:
        for address in (("127.0.0.1", self.PREFERRED_PORT), ("127.0.0.1", 0)):
            try:
                return _CallbackServer(address, expected_state=expected_state)
            except OSError:
                continue
        raise AuthError("Couldn't open a local port for the sign-in callback.")

    def run(self, should_cancel: Callable[[], bool] = lambda: False) -> Session:
        if not _google_enabled(self._url, self._key):
            raise AuthError(
                "Google sign-in isn't enabled for this AgentDeck project yet "
                "(see docs/ACCOUNTS.md)."
            )

        verifier, challenge = build_pkce()
        state = secrets.token_urlsafe(32)
        server = self._bind(state)
        port = server.server_address[1]
        self._redirect_uri = f"http://127.0.0.1:{port}"

        authorize_url = (
            f"{self._url}/auth/v1/authorize?provider=google"
            f"&redirect_to={quote(self._redirect_uri, safe='')}"
            f"&code_challenge={challenge}"
            f"&code_challenge_method=s256"
            f"&flow_type=pkce"
            f"&state={state}"
        )

        try:
            try:
                self._open_browser(authorize_url)
            except Exception as exc:  # noqa: BLE001 - surfaced as an AuthError
                raise AuthError(f"Couldn't open your browser: {exc}") from exc

            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                if should_cancel():
                    raise AuthError("Sign-in was cancelled.")
                server.handle_request()
                if server.result is not None:
                    break
            else:
                raise AuthError("Timed out waiting for the browser sign-in.")

            kind, value = server.result
            if kind == "error":
                raise AuthError(f"Google declined the sign-in: {value}")
        finally:
            try:
                server.server_close()
            except Exception:  # noqa: BLE001
                pass

        return self._exchange(value, verifier)

    def _exchange(self, code: str, verifier: str) -> Session:
        try:
            resp = requests.post(
                f"{self._url}/auth/v1/token?grant_type=pkce",
                headers=_headers(self._key, extra={"Content-Type": "application/json"}),
                json={"auth_code": code, "code_verifier": verifier},
                timeout=_HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise AuthError(f"Couldn't reach Supabase to finish sign-in: {exc}") from exc

        _check(resp, "Sign-in")
        try:
            data = resp.json()
        except ValueError as exc:
            raise AuthError("Supabase returned an unreadable sign-in response.") from exc

        session = Session.from_token_response(data)
        if not session.access_token or not session.refresh_token:
            raise AuthError("Supabase sign-in response was missing tokens.")
        return session


# ---------------------------------------------------------------------------
# Module-level REST calls
# ---------------------------------------------------------------------------

def refresh(session: Session, *, url: str = SUPABASE_URL, key: str = SUPABASE_KEY) -> Session:
    """Trade the refresh token for a fresh session. Raises :class:`AuthError`."""
    try:
        resp = requests.post(
            f"{url.rstrip('/')}/auth/v1/token?grant_type=refresh_token",
            headers=_headers(key, extra={"Content-Type": "application/json"}),
            json={"refresh_token": session.refresh_token},
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AuthError(f"Couldn't reach Supabase to refresh the session: {exc}") from exc

    _check(resp, "Session refresh")
    try:
        data = resp.json()
    except ValueError as exc:
        raise AuthError("Supabase returned an unreadable refresh response.") from exc

    new = Session.from_token_response(data)
    if not new.user and session.user:
        new.user = session.user
    if not new.access_token or not new.refresh_token:
        raise AuthError("Refresh response was missing tokens.")
    return new


def sign_out(session: Session, *, url: str = SUPABASE_URL, key: str = SUPABASE_KEY) -> None:
    """Best-effort server-side logout. Never raises -- a dead network shouldn't
    stop the client from forgetting the session."""
    try:
        requests.post(
            f"{url.rstrip('/')}/auth/v1/logout",
            headers=_headers(key, session.access_token),
            timeout=15,
        )
    except requests.RequestException:
        pass


def fetch_user(access_token: str, *, url: str = SUPABASE_URL, key: str = SUPABASE_KEY) -> dict:
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/auth/v1/user",
            headers=_headers(key, access_token),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise AuthError(f"Couldn't reach Supabase: {exc}") from exc
    _check(resp, "Fetching your profile")
    try:
        return resp.json()
    except ValueError as exc:
        raise AuthError("Supabase returned an unreadable user response.") from exc


def rest_select(
    table: str,
    access_token: str,
    *,
    params: Optional[dict] = None,
    url: str = SUPABASE_URL,
    key: str = SUPABASE_KEY,
) -> list:
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/v1/{table}",
            headers=_headers(key, access_token, {"Accept": "application/json"}),
            params=params or {},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise AuthError(f"Couldn't reach Supabase: {exc}") from exc
    _check(resp, "Reading your account data")
    try:
        out = resp.json()
    except ValueError:
        return []
    return out if isinstance(out, list) else [out]


def rest_upsert(
    table: str,
    row: dict,
    access_token: str,
    *,
    url: str = SUPABASE_URL,
    key: str = SUPABASE_KEY,
) -> list:
    try:
        resp = requests.post(
            f"{url.rstrip('/')}/rest/v1/{table}",
            headers=_headers(
                key,
                access_token,
                {
                    "Content-Type": "application/json",
                    "Prefer": "return=representation,resolution=merge-duplicates",
                },
            ),
            json=row,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise AuthError(f"Couldn't reach Supabase: {exc}") from exc
    _check(resp, "Saving your account data")
    try:
        out = resp.json()
    except ValueError:
        return []
    return out if isinstance(out, list) else [out]


# ---------------------------------------------------------------------------
# Session persistence (DPAPI on Windows, plaintext fallback elsewhere)
# ---------------------------------------------------------------------------

_IS_WINDOWS = os.name == "nt"
_MAGIC_DPAPI = b"ADK1D"
_MAGIC_PLAIN = b"ADK1P"

if _IS_WINDOWS:  # pragma: no cover - platform specific
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def _dpapi(fn, data: bytes) -> bytes:
        src = ctypes.create_string_buffer(data, len(data))
        blob_in = _DATA_BLOB(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()
        ok = fn(
            ctypes.byref(blob_in), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
        )
        if not ok:
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    def _protect(data: bytes) -> bytes:
        return _dpapi(ctypes.windll.crypt32.CryptProtectData, data)

    def _unprotect(data: bytes) -> bytes:
        return _dpapi(ctypes.windll.crypt32.CryptUnprotectData, data)

else:  # pragma: no cover - non-Windows fallback

    def _protect(data: bytes) -> bytes:
        raise OSError("DPAPI is Windows-only")

    def _unprotect(data: bytes) -> bytes:
        raise OSError("DPAPI is Windows-only")


def _default_store_path() -> Path:
    try:
        from config import _get_config_dir

        return _get_config_dir() / "session.bin"
    except Exception:  # noqa: BLE001 - config import must never block auth
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "multi-terminal" / "session.bin"


class SessionStore:
    """Reads / writes the stored session. On Windows the blob is DPAPI-encrypted
    (bound to the OS user); elsewhere, or if DPAPI fails, it falls back to plain
    JSON. :meth:`load` never raises."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else _default_store_path()

    def load(self) -> Optional[Session]:
        try:
            raw = self.path.read_bytes()
        except OSError:
            return None
        try:
            if raw.startswith(_MAGIC_DPAPI):
                blob = _unprotect(raw[len(_MAGIC_DPAPI):])
            elif raw.startswith(_MAGIC_PLAIN):
                blob = raw[len(_MAGIC_PLAIN):]
            else:
                blob = raw  # tolerate a legacy / hand-written plain JSON file
            session = Session.from_dict(json.loads(blob.decode("utf-8")))
        except Exception:  # noqa: BLE001 - a corrupt store is just "not signed in"
            return None
        if not session.access_token or not session.refresh_token:
            return None
        return session

    def save(self, session: Session) -> None:
        """Persist the session.

        On Windows the blob **must** encrypt with DPAPI; if that fails we refuse
        to write rather than drop long-lived refresh tokens onto disk in the
        clear (callers treat a missing store as "sign in again"). The plaintext
        form is only ever used off Windows, where DPAPI does not exist.
        """
        blob = json.dumps(session.to_dict()).encode("utf-8")
        try:
            payload = _MAGIC_DPAPI + _protect(blob)
        except Exception:  # noqa: BLE001
            if _IS_WINDOWS:
                print(
                    "[AgentDeck] WARNING: DPAPI encryption of the session failed; "
                    "not saving it (you'll be asked to sign in again next launch).",
                    file=sys.stderr,
                )
                return
            payload = _MAGIC_PLAIN + blob
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_bytes(payload)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
