"""GitHub sign-in for the AgentDeck **plugins** system -- OAuth device flow.

Qt-free (same rule as ``supabase_auth`` / ``agents`` / ``entitlements``): plain
``requests`` + stdlib, unit-testable headless, driven from a worker thread by
``github_controller.py``.

Why the **device flow** rather than the loopback-PKCE dance ``supabase_auth``
does for Google:

* A GitHub *App* (not a classic OAuth App) gives per-repository permissions the
  user chooses at install time, fine-grained scopes and a 15k/h rate limit.
* Device flow needs **no client secret** in the shipped binary and **no local
  callback server** -- GitHub shows the user a code, they paste it at
  ``github.com/login/device``, and we poll until it's authorised.
* GitHub App user-to-server tokens expire (~8 h) and carry a refresh token;
  :func:`refresh` trades it for a fresh one exactly like ``supabase_auth.refresh``.

The GitHub App must exist and have *device flow* enabled -- see
``docs/PLUGINS.md`` §"Dashboard prerequisites". Its client id is public; set
``AGENTDECK_GITHUB_CLIENT_ID`` to override the baked-in default.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

from secret_store import EncryptedJsonStore

__all__ = [
    "GITHUB_CLIENT_ID",
    "DEFAULT_SCOPE",
    "GitHubAuthError",
    "GitHubToken",
    "DeviceCode",
    "DeviceFlow",
    "refresh",
    "revoke",
    "GitHubTokenStore",
]

#: Public client id of the "AgentDeck" GitHub App. Safe to ship (device flow has
#: no secret -- same class of value as the publishable Supabase key). Override
#: with ``AGENTDECK_GITHUB_CLIENT_ID`` for a test App.
_DEFAULT_CLIENT_ID = "Iv23liY7p5rRtAOm6mtc"
GITHUB_CLIENT_ID = (os.environ.get("AGENTDECK_GITHUB_CLIENT_ID") or _DEFAULT_CLIENT_ID).strip()

#: Classic-OAuth scope string. A GitHub *App* ignores this (its permissions are
#: fixed at registration) but GitHub still wants the parameter present; keep it
#: aligned with docs/PLUGINS.md §5 for the OAuth-App fallback.
DEFAULT_SCOPE = "repo read:org workflow"

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"

_HTTP_TIMEOUT = 30
_JSON = {"Accept": "application/json"}


class GitHubAuthError(Exception):
    """Anything that stops a connect / refresh completing. Message is phrased
    for a status line."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass
class DeviceCode:
    """The ``/login/device/code`` response -- what to show the user."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int = 900
    interval: int = 5

    @classmethod
    def from_response(cls, data: dict) -> "DeviceCode":
        return cls(
            device_code=str(data.get("device_code") or ""),
            user_code=str(data.get("user_code") or ""),
            verification_uri=str(
                data.get("verification_uri") or "https://github.com/login/device"
            ),
            expires_in=int(data.get("expires_in") or 900),
            interval=int(data.get("interval") or 5),
        )


@dataclass
class GitHubToken:
    """One authorised token set. ``expires_at`` is absolute epoch seconds; a
    classic OAuth App returns non-expiring tokens, in which case it is ``0``."""

    access_token: str
    refresh_token: str = ""
    expires_at: int = 0
    scope: str = ""
    token_type: str = "bearer"

    def is_expired(self, skew_seconds: int = 120) -> bool:
        if not self.expires_at:
            return False
        return time.time() >= (self.expires_at - max(0, skew_seconds))

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": int(self.expires_at),
            "scope": self.scope,
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GitHubToken":
        if not isinstance(data, dict):
            raise ValueError("token payload is not an object")
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=int(data.get("expires_at") or 0),
            scope=str(data.get("scope") or ""),
            token_type=str(data.get("token_type") or "bearer"),
        )

    @classmethod
    def from_token_response(cls, data: dict) -> "GitHubToken":
        expires_in = data.get("expires_in")
        try:
            expires_at = int(time.time() + float(expires_in)) if expires_in else 0
        except (TypeError, ValueError):
            expires_at = 0
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=expires_at,
            scope=str(data.get("scope") or ""),
            token_type=str(data.get("token_type") or "bearer"),
        )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _post(url: str, data: dict) -> dict:
    try:
        resp = requests.post(url, data=data, headers=_JSON, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise GitHubAuthError(f"Couldn't reach GitHub: {exc}") from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if not resp.ok and "error" not in body:
        raise GitHubAuthError(f"GitHub returned HTTP {resp.status_code}")
    return body


# GitHub device-flow polling errors that are not fatal -- keep polling.
_PENDING = "authorization_pending"
_SLOW_DOWN = "slow_down"

_ERROR_TEXT = {
    "expired_token": "The sign-in code expired before you authorised it. Try again.",
    "access_denied": "Sign-in was cancelled on GitHub.",
    "incorrect_client_credentials": "This AgentDeck build has an invalid GitHub client id.",
    "device_flow_disabled": "Device flow isn't enabled on the AgentDeck GitHub App yet.",
    "unsupported_grant_type": "GitHub rejected the sign-in grant type.",
}


# ---------------------------------------------------------------------------
# Device flow
# ---------------------------------------------------------------------------

class DeviceFlow:
    """Runs one device-flow connect. Blocking -- call :meth:`run` off the GUI
    thread (or drive :meth:`start` / :meth:`poll_once` yourself)."""

    def __init__(
        self,
        *,
        client_id: str = "",
        scope: str = DEFAULT_SCOPE,
        timeout: float = 900.0,
    ):
        self.client_id = (client_id or GITHUB_CLIENT_ID).strip()
        self.scope = scope
        self.timeout = float(timeout)
        self._device: Optional[DeviceCode] = None

    def start(self) -> DeviceCode:
        if not self.client_id:
            raise GitHubAuthError(
                "GitHub isn't configured for this AgentDeck build yet "
                "(no GitHub App client id -- see docs/PLUGINS.md)."
            )
        body = _post(
            _DEVICE_CODE_URL,
            {"client_id": self.client_id, "scope": self.scope},
        )
        if body.get("error"):
            raise GitHubAuthError(
                _ERROR_TEXT.get(body["error"], body.get("error_description") or body["error"])
            )
        device = DeviceCode.from_response(body)
        if not device.device_code or not device.user_code:
            raise GitHubAuthError("GitHub returned an unreadable device-code response.")
        self._device = device
        return device

    def poll_once(self) -> Optional[GitHubToken]:
        """One token poll. Returns a :class:`GitHubToken` when authorised,
        ``None`` while still pending. Raises on a fatal error."""
        if self._device is None:
            raise GitHubAuthError("poll_once() called before start()")
        body = _post(
            _TOKEN_URL,
            {
                "client_id": self.client_id,
                "device_code": self._device.device_code,
                "grant_type": _GRANT_DEVICE,
            },
        )
        error = body.get("error")
        if error in (_PENDING, _SLOW_DOWN):
            if error == _SLOW_DOWN:
                self._device.interval = int(body.get("interval") or self._device.interval + 5)
            return None
        if error:
            raise GitHubAuthError(
                _ERROR_TEXT.get(error, body.get("error_description") or error)
            )
        token = GitHubToken.from_token_response(body)
        if not token.access_token:
            raise GitHubAuthError("GitHub authorised the sign-in but returned no token.")
        return token

    def run(self, should_cancel: Callable[[], bool] = lambda: False) -> GitHubToken:
        device = self.start()
        deadline = time.monotonic() + min(self.timeout, device.expires_in)
        while time.monotonic() < deadline:
            if should_cancel():
                raise GitHubAuthError("Sign-in was cancelled.")
            time.sleep(max(1, self._device.interval if self._device else device.interval))
            if should_cancel():
                raise GitHubAuthError("Sign-in was cancelled.")
            token = self.poll_once()
            if token is not None:
                return token
        raise GitHubAuthError("Timed out waiting for the GitHub authorisation.")


# ---------------------------------------------------------------------------
# Module-level token calls
# ---------------------------------------------------------------------------

def refresh(token: GitHubToken, *, client_id: str = "") -> GitHubToken:
    """Trade a refresh token for a fresh one. Raises :class:`GitHubAuthError`."""
    client_id = (client_id or GITHUB_CLIENT_ID).strip()
    if not token.refresh_token:
        raise GitHubAuthError("This GitHub token can't be refreshed -- reconnect.")
    body = _post(
        _TOKEN_URL,
        {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
        },
    )
    if body.get("error"):
        raise GitHubAuthError(
            _ERROR_TEXT.get(body["error"], body.get("error_description") or body["error"])
        )
    new = GitHubToken.from_token_response(body)
    if not new.access_token:
        raise GitHubAuthError("GitHub's refresh response was missing a token.")
    if not new.refresh_token:
        new.refresh_token = token.refresh_token
    return new


def revoke(token: GitHubToken, *, client_id: str = "") -> None:
    """Best-effort: tell GitHub to drop this authorisation. Never raises."""
    client_id = (client_id or GITHUB_CLIENT_ID).strip()
    if not client_id or not token.access_token:
        return
    try:
        requests.delete(
            f"https://api.github.com/applications/{client_id}/token",
            json={"access_token": token.access_token},
            timeout=15,
        )
    except requests.RequestException:
        pass


# ---------------------------------------------------------------------------
# Token vault -- local, DPAPI-encrypted, never cloud-synced
# ---------------------------------------------------------------------------

def _default_store_path():
    try:
        from config import _get_config_dir

        return _get_config_dir() / "github.bin"
    except Exception:  # noqa: BLE001 - config import must never block auth
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        from pathlib import Path

        return Path(base) / "multi-terminal" / "github.bin"


class GitHubTokenStore:
    """The connected GitHub token, at ``%APPDATA%\\multi-terminal\\github.bin``.

    DPAPI-encrypted on Windows (bound to the OS user). Deliberately a separate
    file from ``session.bin`` and **never** mirrored to the account -- a GitHub
    token grants repo write / Actions access, so it stays on the one machine.
    """

    def __init__(self, path=None):
        self._store = EncryptedJsonStore(path or _default_store_path())

    def load(self) -> Optional[GitHubToken]:
        data = self._store.load()
        if not data:
            return None
        try:
            token = GitHubToken.from_dict(data)
        except ValueError:
            return None
        return token if token.access_token else None

    def save(self, token: GitHubToken) -> bool:
        return self._store.save(token.to_dict())

    def clear(self) -> None:
        self._store.clear()
