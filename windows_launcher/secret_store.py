"""A tiny on-disk secret store: encrypted-at-rest JSON, per-OS backend.

Factored out of ``supabase_auth.SessionStore`` so the GitHub plugin's token
vault (``github_auth.GitHubTokenStore``) gets the exact same guarantees without
copy-pasting the platform-specific bits:

* on Windows the blob is encrypted with DPAPI, tied to the OS user account;
* on Linux the blob is handed to the OS keyring (GNOME Keyring / KWallet /
  whatever implements the freedesktop Secret Service, via the ``keyring``
  package) rather than written to disk at all -- the on-disk file becomes a
  small marker recording *that* something is stored and which keyring entry
  to look it up under;
* it **refuses to write plaintext anywhere** -- a failed DPAPI call, a Linux
  machine with no keyring daemon running, or any platform that is neither of
  the two above, all drop the write rather than leave a long-lived token on
  disk in the clear (this used to fall back to plain JSON off Windows; that
  was a real security regression on the one non-Windows environment this
  matters for -- see linux-v4/context.md);
* :meth:`load` never raises -- a missing / corrupt / foreign file reads as
  ``None`` ("nothing stored").

Qt-free on purpose (same rule as ``supabase_auth`` / ``agents`` / ``entitlements``).
``supabase_auth`` keeps its own inline copy for now; new code should use this.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

__all__ = ["EncryptedJsonStore"]

_IS_WINDOWS = os.name == "nt"
_IS_LINUX = sys.platform.startswith("linux")
_MAGIC_DPAPI = b"ADKS1D"
_MAGIC_PLAIN = b"ADKS1P"  # legacy: still *read* for back-compat, never written
_MAGIC_LINUX = b"ADKS1L"

#: Shared keyring "service" name; each store gets its own "username" within it
#: (see EncryptedJsonStore._keyring_username) so github.bin and session.bin
#: don't collide in one keyring collection.
_KEYRING_SERVICE = "AgentDeck"


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


if _IS_LINUX:  # pragma: no cover - platform specific

    def _keyring_save(username: str, blob: bytes) -> bool:
        """True if the OS keyring actually took the secret.

        False (never an exception) covers every way a Linux desktop can lack a
        working Secret Service: no daemon running at all (``NoKeyringError``),
        a locked/unavailable collection, or any other backend error -- the
        caller's job is only to decide "encrypted store worked" vs. "it
        didn't", never to crash the caller over a keyring hiccup.
        """
        try:
            import keyring

            keyring.set_password(_KEYRING_SERVICE, username, blob.decode("utf-8"))
            return True
        except Exception:  # noqa: BLE001 - see docstring
            return False

    def _keyring_load(username: str) -> Optional[bytes]:
        try:
            import keyring

            value = keyring.get_password(_KEYRING_SERVICE, username)
        except Exception:  # noqa: BLE001
            return None
        return value.encode("utf-8") if value is not None else None

    def _keyring_delete(username: str) -> None:
        try:
            import keyring

            keyring.delete_password(_KEYRING_SERVICE, username)
        except Exception:  # noqa: BLE001 - already gone, or no keyring -- fine
            pass

else:  # pragma: no cover - non-Linux

    def _keyring_save(username: str, blob: bytes) -> bool:
        return False

    def _keyring_load(username: str) -> Optional[bytes]:
        return None

    def _keyring_delete(username: str) -> None:
        pass


class EncryptedJsonStore:
    """Reads / writes one JSON object to ``path``, encrypted at rest.

    Windows: DPAPI, blob written straight into ``path``. Linux: the OS keyring
    (see the module docstring) holds the actual secret; ``path`` holds only a
    small marker (magic + the keyring lookup key) so :meth:`load`/:meth:`clear`
    know where to look without hitting the keyring for a store that was never
    written. Anywhere else: :meth:`save` always returns ``False`` -- there is
    no plaintext fallback.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _keyring_username(self) -> str:
        """The keyring "username" for this store -- distinguishes e.g.
        github.bin from session.bin within the one shared _KEYRING_SERVICE."""
        return f"agentdeck:{self.path.name}"

    def load(self) -> Optional[dict]:
        try:
            raw = self.path.read_bytes()
        except OSError:
            return None
        try:
            if raw.startswith(_MAGIC_DPAPI):
                blob = _unprotect(raw[len(_MAGIC_DPAPI):])
            elif raw.startswith(_MAGIC_LINUX):
                blob = _keyring_load(self._keyring_username())
                if blob is None:
                    return None
            elif raw.startswith(_MAGIC_PLAIN):
                # Legacy: a file written before this store refused to write
                # plaintext. Still readable so an existing install doesn't
                # lose a stored session/token on upgrade; never written again.
                blob = raw[len(_MAGIC_PLAIN):]
            else:
                blob = raw  # tolerate a legacy / hand-written plain JSON file
            data = json.loads(blob.decode("utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt store is just "nothing stored"
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict) -> bool:
        """Persist ``data``. Returns True on a successful write.

        The blob **must** land in a real OS-backed secret store -- DPAPI on
        Windows, the keyring on Linux. If that fails (DPAPI error, no keyring
        daemon running, or any other platform), the write is dropped entirely
        rather than falling back to plaintext; callers treat a missing store
        the same as "not connected yet" (reconnect / sign in again).
        """
        blob = json.dumps(data).encode("utf-8")

        if _IS_WINDOWS:
            try:
                payload = _MAGIC_DPAPI + _protect(blob)
            except Exception:  # noqa: BLE001
                print(
                    "[AgentDeck] WARNING: DPAPI encryption failed; not saving "
                    f"{self.path.name} (you'll need to reconnect).",
                    file=sys.stderr,
                )
                return False
        elif _IS_LINUX:
            username = self._keyring_username()
            if not _keyring_save(username, blob):
                print(
                    "[AgentDeck] WARNING: no OS keyring available; not saving "
                    f"{self.path.name} (you'll need to reconnect). Install/start "
                    "a Secret Service provider (GNOME Keyring, KWallet, ...).",
                    file=sys.stderr,
                )
                return False
            payload = _MAGIC_LINUX + username.encode("utf-8")
        else:
            print(
                "[AgentDeck] WARNING: no supported secret store on this platform; "
                f"not saving {self.path.name} (you'll need to reconnect).",
                file=sys.stderr,
            )
            return False

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_bytes(payload)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def clear(self) -> None:
        if _IS_LINUX:
            try:
                raw = self.path.read_bytes()
            except OSError:
                raw = b""
            if raw.startswith(_MAGIC_LINUX):
                _keyring_delete(self._keyring_username())
        try:
            self.path.unlink()
        except OSError:
            pass
