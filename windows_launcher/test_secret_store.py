"""Tests for secret_store.EncryptedJsonStore.

Runs on any platform: the real DPAPI/keyring calls are exercised on their
native OS elsewhere (Windows CI/dev for DPAPI; a dedicated ubuntu-latest CI
job with a real gnome-keyring for the Linux keyring round trip -- see
linux-v4/context.md). Here, the Linux *dispatch* logic (EncryptedJsonStore's
branching between DPAPI / keyring / refuse-to-write-plaintext) is verified by
monkeypatching secret_store._IS_WINDOWS / _IS_LINUX / _keyring_* directly, so
it's checked regardless of which OS runs this file.

    python test_secret_store.py
"""

import json
import sys
import tempfile
from pathlib import Path

import secret_store
from secret_store import EncryptedJsonStore

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


def _tmp_path(name="store.bin"):
    d = Path(tempfile.mkdtemp(prefix="agentdeck-secret-store-"))
    return d / name


class _FakeKeyring:
    """In-memory stand-in for the module-level _keyring_save/_load/_delete
    trio -- avoids depending on a real Secret Service daemon to test
    EncryptedJsonStore's own branching."""

    def __init__(self, available=True):
        self.available = available
        self.entries = {}

    def save(self, username, blob):
        if not self.available:
            return False
        self.entries[username] = blob
        return True

    def load(self, username):
        return self.entries.get(username)

    def delete(self, username):
        self.entries.pop(username, None)


def _patch_linux(fake):
    secret_store._IS_WINDOWS = False
    secret_store._IS_LINUX = True
    secret_store._keyring_save = fake.save
    secret_store._keyring_load = fake.load
    secret_store._keyring_delete = fake.delete


_orig_is_windows = secret_store._IS_WINDOWS
_orig_is_linux = secret_store._IS_LINUX
_orig_keyring_save = secret_store._keyring_save
_orig_keyring_load = secret_store._keyring_load
_orig_keyring_delete = secret_store._keyring_delete


def _restore():
    secret_store._IS_WINDOWS = _orig_is_windows
    secret_store._IS_LINUX = _orig_is_linux
    secret_store._keyring_save = _orig_keyring_save
    secret_store._keyring_load = _orig_keyring_load
    secret_store._keyring_delete = _orig_keyring_delete


# ---------------------------------------------------------------------------
print("[1] Linux dispatch: a working keyring round-trips through save/load/clear")

fake = _FakeKeyring(available=True)
_patch_linux(fake)
try:
    path = _tmp_path("session.bin")
    store = EncryptedJsonStore(path)

    ok = store.save({"access_token": "secret-abc"})
    check("save reports success", ok is True)
    check("marker file exists", path.is_file())

    raw = path.read_bytes()
    check("on-disk file is the ADKS1L marker, not plaintext json",
          raw.startswith(secret_store._MAGIC_LINUX)
          and b"secret-abc" not in raw)
    check("the secret actually landed in the (fake) keyring",
          any(b"secret-abc" in v for v in fake.entries.values()))

    loaded = store.load()
    check("load returns the saved data", loaded == {"access_token": "secret-abc"})

    store.clear()
    check("clear() removes the marker file", not path.exists())
    check("clear() also removed the keyring entry", not fake.entries)
finally:
    _restore()


# ---------------------------------------------------------------------------
print("[2] Linux dispatch: no keyring daemon -> refuse, never write plaintext")

fake = _FakeKeyring(available=False)
_patch_linux(fake)
try:
    path = _tmp_path("session.bin")
    store = EncryptedJsonStore(path)

    ok = store.save({"access_token": "should-not-be-written"})
    check("save reports failure", ok is False)
    check("nothing was written to disk at all", not path.exists())
finally:
    _restore()


# ---------------------------------------------------------------------------
print("[3] Linux dispatch: load() survives the keyring entry having vanished")

fake = _FakeKeyring(available=True)
_patch_linux(fake)
try:
    path = _tmp_path("session.bin")
    store = EncryptedJsonStore(path)
    store.save({"access_token": "x"})
    fake.entries.clear()  # simulate the user clearing it via their OS's keyring UI

    check("load() returns None, doesn't raise", store.load() is None)
finally:
    _restore()


# ---------------------------------------------------------------------------
print("[4] an unsupported platform (neither Windows nor Linux) never writes plaintext")

secret_store._IS_WINDOWS = False
secret_store._IS_LINUX = False
try:
    path = _tmp_path("session.bin")
    store = EncryptedJsonStore(path)
    ok = store.save({"access_token": "nope"})
    check("save reports failure", ok is False)
    check("nothing written", not path.exists())
finally:
    _restore()


# ---------------------------------------------------------------------------
print("[5] a legacy plaintext file (written before this store existed) still loads")

path = _tmp_path("legacy.bin")
path.write_bytes(secret_store._MAGIC_PLAIN + json.dumps({"a": 1}).encode("utf-8"))
store = EncryptedJsonStore(path)
check("legacy ADKS1P-marked plaintext still reads back",
      store.load() == {"a": 1})


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
