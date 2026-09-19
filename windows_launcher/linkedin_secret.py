r"""The LinkedIn plugin's credentials, encrypted at rest on this machine.

Three things live here, none of which may ever reach ``plugins.json`` or the
account mirror:

* ``client_secret`` -- LinkedIn has no Dynamic Client Registration and no PKCE
  for public clients, so the user's own app's secret is unavoidable. Same
  situation as Google Drive (``gdrive_secret``), same trust level: the app is
  the user's, and the secret only ever leaves this process to hit LinkedIn's
  own token endpoint.
* ``provider_key`` -- the tier-2 job-data provider's API key.
* ``li_at`` / ``li_jsession`` -- the member's own LinkedIn session cookies,
  used only when the user explicitly switches tier 3 on. The most sensitive
  values AgentDeck ever stores: together they *are* the session. LinkedIn's
  internal API validates ``Csrf-Token`` against the real ``JSESSIONID`` cookie,
  so both have to be pasted -- a synthetic pair is rejected. Neither leaves the
  machine except in a request to linkedin.com, and ``disconnect`` clears them.
* ``access_token`` / ``token_expires`` -- what the tier-1 OAuth flow came back
  with. LinkedIn issues 60-day access tokens and hands out refresh tokens only
  to approved partners, so there is nothing to refresh: when the token expires
  the card asks the user to reconnect. ``token_expires`` is an epoch **string**
  because every field in this store is a string (see :data:`FIELDS`).

Unlike ``gdrive_secret``, nothing is ever handed to an agent out-of-band: the
MCP server is our own process and reads this store directly, so no credential is
ever written into an agent's config file, passed on a command line, or copied
into a second plaintext store.

Same backend and rules as ``github_auth.GitHubTokenStore`` -- a DPAPI blob on
Windows / the OS keyring on Linux (see ``secret_store``), at
``%APPDATA%\multi-terminal\linkedin.bin``.

Qt-free. See docs/PLUGINS.md 19.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from secret_store import EncryptedJsonStore

__all__ = ["LinkedInSecretStore", "FIELDS"]

#: Every key this store understands. Anything else handed to :meth:`save` is
#: dropped rather than persisted -- a typo must not quietly create a second,
#: unreadable credential.
FIELDS = ("client_secret", "provider_key", "li_at", "li_jsession",
          "access_token", "token_expires")


def _default_store_path() -> Path:
    # Tests redirect the vault here (see plugin_store's ADK_PLUGIN_STORE note).
    override = os.environ.get("ADK_LINKEDIN_VAULT")
    if override:
        return Path(override)
    try:
        from config import config_dir

        return config_dir() / "linkedin.bin"
    except Exception:  # noqa: BLE001 - config import must never block a connect
        base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") \
            or os.path.join(os.path.expanduser("~"), ".config")
        return Path(base) / "multi-terminal" / "linkedin.bin"


class LinkedInSecretStore:
    r"""The connected LinkedIn credentials, at ``%APPDATA%\multi-terminal\linkedin.bin``."""

    def __init__(self, path=None):
        self._store = EncryptedJsonStore(path or _default_store_path())

    # -- read ------------------------------------------------------------

    def load(self) -> dict:
        """Every stored field. ``{}`` when nothing is stored or the blob is
        unreadable (a foreign machine, a cleared keyring)."""
        data = self._store.load()
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if k in FIELDS and isinstance(v, str) and v}

    def get(self, field: str) -> Optional[str]:
        return self.load().get(field) or None

    def has(self, field: str) -> bool:
        return bool(self.get(field))

    # -- write -----------------------------------------------------------

    def save(self, **fields: Optional[str]) -> bool:
        """Merge ``fields`` into the stored blob.

        ``None`` means "leave whatever is stored alone" -- so saving a new
        provider key cannot silently wipe the client secret. An **empty string**
        means "forget this one", which is how a tier is switched off without
        disconnecting the whole plugin.

        False when the OS secret store refused the write (see
        ``secret_store.EncryptedJsonStore.save`` -- there is no plaintext
        fallback), in which case nothing was persisted.
        """
        data = self.load()
        touched = False
        for key, value in fields.items():
            if key not in FIELDS or value is None:
                continue
            touched = True
            text = str(value).strip()
            if text:
                data[key] = text
            else:
                data.pop(key, None)
        if not touched:
            return True
        if not data:
            self._store.clear()
            return True
        return self._store.save(data)

    def clear(self) -> None:
        self._store.clear()
