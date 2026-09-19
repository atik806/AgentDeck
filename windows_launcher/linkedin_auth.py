"""The tier-1 LinkedIn sign-in: OAuth 2.0 authorization code over a loopback.

Shaped like ``supabase_auth.GoogleSignIn`` -- bind a loopback port, open the
browser, wait for the redirect, exchange the code -- with three differences that
come from LinkedIn rather than from taste:

* **No PKCE.** LinkedIn's authorization server does not support it for public
  clients; the token exchange takes ``client_secret`` instead. So ``state`` is
  not belt-and-braces here, it is the *only* CSRF defence on the callback, and
  a mismatched or missing state is rejected outright (Supabase's flow can be
  laxer because its PKCE verifier never leaves the process).
* **A fixed port.** Redirect URLs must be pre-registered on the LinkedIn app,
  so :data:`CALLBACK_PORT` cannot be ephemeral and there is no fallback to
  port 0 -- a busy port is an error the user can act on, not something to
  silently paper over with a URL LinkedIn will reject.
* **No refresh token.** Refresh tokens are a partner-program feature. A
  self-serve app gets a ~60-day access token and nothing to renew it with, so
  :func:`sign_in` returns an expiry and the card asks for a reconnect when it
  lapses.

Blocking -- call :func:`sign_in` off the GUI thread. Qt-free.

See docs/PLUGINS.md 19.
"""

from __future__ import annotations

import secrets
import socket
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, quote, urlsplit

import requests

__all__ = [
    "AuthError",
    "CALLBACK_PORT",
    "REDIRECT_URI",
    "AUTHORIZE_URL",
    "TOKEN_URL",
    "DEFAULT_SCOPES",
    "sign_in",
]

#: LinkedIn pre-registers every redirect URL, so this is fixed. The user adds
#: ``http://localhost:8977/callback`` on their app's Auth tab.
CALLBACK_PORT = 8977
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

#: The self-serve set: OIDC identity (Sign In with LinkedIn) plus posting as the
#: member (Share on LinkedIn). Space-separated per RFC 6749 3.3.
DEFAULT_SCOPES = "openid profile email w_member_social"

_TIMEOUT = 20


class AuthError(RuntimeError):
    """A sign-in that didn't finish, phrased for a person."""


_SUCCESS_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>AgentDeck</title><style>
 html,body{height:100%;margin:0}
 body{display:flex;align-items:center;justify-content:center;
   background:#1e1e2e;color:#cdd6f4;font:15px/1.5 "Segoe UI",system-ui,sans-serif}
 .card{text-align:center;padding:40px 48px;border:1px solid #313244;border-radius:16px;
   background:#181825;max-width:360px}
 h1{font-size:19px;margin:0 0 8px}p{margin:0;color:#9399b2;font-size:13px}
</style></head><body><div class="card">
<h1>LinkedIn connected</h1><p>Head back to AgentDeck &mdash; you can close this tab.</p>
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
<h1>Connecting didn't finish</h1><p>You can close this tab and try again in AgentDeck.</p>
</div></body></html>"""


class _CallbackServer(HTTPServer):
    # False on purpose: SO_REUSEADDR would let another local process re-bind
    # this exact port and race us for the authorization code.
    allow_reuse_address = False
    timeout = 0.4  # so handle_request() returns and we can poll for cancel

    def __init__(self, address, expected_state: str):
        self.expected_state = expected_state
        super().__init__(address, _CallbackHandler)
        #: ``("code", value)`` or ``("error", description)``.
        self.result: Optional[tuple] = None

    def server_bind(self) -> None:
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
        # LinkedIn redirects the browser here as a top-level navigation: no
        # Origin header, and always to the registered path. Anything else on
        # this machine poking at the port doesn't get to hand us a code.
        if self.headers.get("Origin") or split.path not in ("/callback", "/callback/"):
            self._respond(204, "")
            return
        try:
            query = parse_qs(split.query)
        except ValueError:
            query = {}
        code = (query.get("code") or [""])[0]
        error = (query.get("error") or [""])[0]
        state = (query.get("state") or [""])[0]
        expected = getattr(self.server, "expected_state", "")

        if (code or error) and not (state and secrets.compare_digest(state, expected)):
            # Without PKCE this check is the whole CSRF defence -- so unlike the
            # Supabase flow, a *missing* state is a failure too.
            self.server.result = ("error", "the sign-in callback failed a security check")
            self._respond(400, _ERROR_HTML)
            return

        if code:
            self.server.result = ("code", code)
            self._respond(200, _SUCCESS_HTML)
        elif error:
            description = (query.get("error_description") or [error])[0]
            self.server.result = ("error", description or error)
            self._respond(200, _ERROR_HTML)
        else:
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


def _authorize_url(client_id: str, state: str, scopes: str) -> str:
    return (
        f"{AUTHORIZE_URL}?response_type=code"
        f"&client_id={quote(client_id, safe='')}"
        f"&redirect_uri={quote(REDIRECT_URI, safe='')}"
        f"&state={quote(state, safe='')}"
        f"&scope={quote(scopes, safe='')}"
    )


def sign_in(
    client_id: str,
    client_secret: str,
    *,
    scopes: str = DEFAULT_SCOPES,
    timeout: float = 180.0,
    open_browser: Callable[[str], object] = webbrowser.open,
    exchange: Optional[Callable[..., dict]] = None,
) -> dict:
    """Run one loopback sign-in. Returns ``{"access_token", "expires_at", "scope"}``.

    ``open_browser`` / ``exchange`` are injection points for the offline test
    suite -- nothing in this module talks to the network when both are supplied.
    """
    cid = (client_id or "").strip()
    secret = (client_secret or "").strip()
    if not cid or not secret:
        raise AuthError("LinkedIn needs both the Client ID and the Client Secret.")

    state = secrets.token_urlsafe(32)
    try:
        server = _CallbackServer(("127.0.0.1", CALLBACK_PORT), state)
    except OSError as exc:
        raise AuthError(
            f"Port {CALLBACK_PORT} is already in use, and LinkedIn only accepts "
            f"the redirect URL registered on your app ({REDIRECT_URI}). Close "
            "whatever is holding the port and try again."
        ) from exc

    try:
        try:
            open_browser(_authorize_url(cid, state, scopes))
        except Exception as exc:  # noqa: BLE001 - surfaced as an AuthError
            raise AuthError(f"Couldn't open your browser: {exc}") from exc

        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            server.handle_request()
            if server.result is not None:
                break
        else:
            raise AuthError("Timed out waiting for the browser sign-in.")

        kind, value = server.result
        if kind == "error":
            raise AuthError(f"LinkedIn declined the sign-in: {value}")
    finally:
        try:
            server.server_close()
        except Exception:  # noqa: BLE001
            pass

    return (exchange or _exchange)(value, cid, secret)


def _exchange(code: str, client_id: str, client_secret: str) -> dict:
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AuthError(f"Couldn't reach LinkedIn to finish signing in: {exc}") from exc

    if not resp.ok:
        detail = ""
        try:
            detail = str(resp.json().get("error_description") or "").strip()
        except ValueError:
            detail = (resp.text or "").strip()[:200]
        raise AuthError(
            f"LinkedIn rejected the sign-in ({resp.status_code})"
            f"{': ' + detail if detail else ''}."
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise AuthError("LinkedIn returned an unreadable token response.") from exc

    token = str(data.get("access_token") or "")
    if not token:
        raise AuthError("LinkedIn didn't return an access token.")
    try:
        expires_in = int(data.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    return {
        "access_token": token,
        # LinkedIn's self-serve apps get no refresh token, so this expiry is
        # what the card watches to say "reconnect".
        "expires_at": time.time() + expires_in if expires_in else 0.0,
        "scope": str(data.get("scope") or ""),
    }
