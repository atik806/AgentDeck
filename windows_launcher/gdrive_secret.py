"""The Google Drive OAuth **client secret**, encrypted at rest on this machine.

Google's auth servers have no Dynamic Client Registration, so -- alone among
AgentDeck's plugins -- the Drive plugin needs a client *secret*. The user brings
a Desktop-app OAuth client from their own Google Cloud project; this module is
where its secret lives between "Connect" and the moment it is handed to Claude
Code.

Same shape and the same rules as ``github_auth.GitHubTokenStore``: a DPAPI blob
on Windows / the OS keyring on Linux (see ``secret_store``), at
``%APPDATA%\multi-terminal\gdrive.bin``, and **never** mirrored to the account.
``gdrive_controller._mirror_up`` sends a bare presence row to Supabase -- no
client id, no secret.

A caveat worth knowing and worth telling the user (the Drive detail page does):
once seeded, *Claude Code* keeps its own copy of the secret in
``~/.claude/.credentials.json`` as plaintext JSON, under
``mcpOAuthClientConfig``. That is Claude Code's storage, not ours, and it is why
the plugin asks for a **Desktop-app** client -- RFC 8252 treats a native app's
client secret as non-confidential, so this is the intended trust level rather
than a leak. It is still the user's secret, so we do not add a second plaintext
copy of our own.

Qt-free.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from secret_store import EncryptedJsonStore

__all__ = ["GDriveSecretStore"]


def _default_store_path() -> Path:
    try:
        from config import config_dir

        return config_dir() / "gdrive.bin"
    except Exception:  # noqa: BLE001 - config import must never block a connect
        base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") \
            or os.path.join(os.path.expanduser("~"), ".config")
        return Path(base) / "multi-terminal" / "gdrive.bin"


class GDriveSecretStore:
    """The connected Drive client secret, at ``%APPDATA%\multi-terminal\gdrive.bin``."""

    def __init__(self, path=None):
        self._store = EncryptedJsonStore(path or _default_store_path())

    def load(self) -> Optional[str]:
        """The stored client secret, or None when nothing is stored."""
        data = self._store.load()
        if not data:
            return None
        secret = data.get("client_secret")
        return secret if isinstance(secret, str) and secret else None

    def save(self, client_secret: str) -> bool:
        """Persist ``client_secret``. False when the OS secret store refused it
        (see ``secret_store.EncryptedJsonStore.save`` -- no plaintext fallback)."""
        secret = (client_secret or "").strip()
        if not secret:
            return False
        return self._store.save({"client_secret": secret})

    def clear(self) -> None:
        self._store.clear()
