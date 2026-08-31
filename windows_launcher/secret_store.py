"""A tiny on-disk secret store: DPAPI-encrypted JSON, Windows-bound.

Factored out of ``supabase_auth.SessionStore`` so the GitHub plugin's token
vault (``github_auth.GitHubTokenStore``) gets the exact same guarantees without
copy-pasting the ctypes:

* on Windows the blob is encrypted with DPAPI, tied to the OS user account;
* it **refuses to write plaintext on Windows** -- a failed encryption drops the
  write rather than leaving a long-lived token on disk in the clear;
* off Windows (tests, CI) it falls back to plain JSON with a distinct magic;
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
_MAGIC_DPAPI = b"ADKS1D"
_MAGIC_PLAIN = b"ADKS1P"


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


class EncryptedJsonStore:
    """Reads / writes one JSON object to ``path``, encrypted at rest on Windows."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> Optional[dict]:
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
            data = json.loads(blob.decode("utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt store is just "nothing stored"
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict) -> bool:
        """Persist ``data``. Returns True on a successful write.

        On Windows the blob **must** encrypt with DPAPI; if that fails the write
        is dropped (callers treat a missing store as "connect again") rather than
        writing a token in the clear.
        """
        blob = json.dumps(data).encode("utf-8")
        try:
            payload = _MAGIC_DPAPI + _protect(blob)
        except Exception:  # noqa: BLE001
            if _IS_WINDOWS:
                print(
                    "[AgentDeck] WARNING: DPAPI encryption failed; not saving "
                    f"{self.path.name} (you'll need to reconnect).",
                    file=sys.stderr,
                )
                return False
            payload = _MAGIC_PLAIN + blob
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_bytes(payload)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
