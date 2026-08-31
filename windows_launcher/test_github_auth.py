"""Offline tests for github_auth.py + secret_store.py -- no network.

``github_auth.requests`` is swapped for a scripted fake. Run:

    .venv\\Scripts\\python.exe test_github_auth.py
"""

import sys
import tempfile
import time
from pathlib import Path

import github_auth
import secret_store
from github_auth import DeviceFlow, GitHubAuthError, GitHubToken, GitHubTokenStore, refresh

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


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeRequests:
    """Scripted: each POST pops the next queued response for its URL."""

    def __init__(self):
        self.script = {}   # url-substring -> list[_Resp]
        self.calls = []

    def queue(self, url_sub, *responses):
        self.script.setdefault(url_sub, []).extend(responses)

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data))
        for sub, responses in self.script.items():
            if sub in url and responses:
                return responses.pop(0)
        return _Resp({"error": "unexpected_call"}, 400)

    def delete(self, *a, **k):
        self.calls.append(("DELETE", a, k))
        return _Resp({}, 204)

    class RequestException(Exception):
        pass


# ---------------------------------------------------------------------------
print("[1] GitHubToken value object")
t = GitHubToken.from_token_response(
    {"access_token": "gho_x", "refresh_token": "ghr_y", "expires_in": 3600, "scope": "repo"}
)
check("access token parsed", t.access_token == "gho_x")
check("expires_at is absolute + future", t.expires_at > time.time() + 3000)
check("not expired now", not t.is_expired())
check("round-trips through dict", GitHubToken.from_dict(t.to_dict()).refresh_token == "ghr_y")
never = GitHubToken(access_token="a")
check("no-expiry token never expires", not never.is_expired())


# ---------------------------------------------------------------------------
print("[2] device flow -- happy path")
fake = FakeRequests()
github_auth.requests = fake
fake.queue(
    "login/device/code",
    _Resp({"device_code": "dc", "user_code": "WXYZ-1234",
           "verification_uri": "https://github.com/login/device",
           "expires_in": 900, "interval": 1}),
)
fake.queue(
    "login/oauth/access_token",
    _Resp({"error": "authorization_pending"}),
    _Resp({"access_token": "gho_ok", "refresh_token": "ghr_ok", "expires_in": 28800,
           "token_type": "bearer", "scope": "repo"}),
)
flow = DeviceFlow(client_id="Iv1.test", timeout=30)
dc = flow.start()
check("start() returns the user code", dc.user_code == "WXYZ-1234")
check("first poll is pending (None)", flow.poll_once() is None)
tok = flow.poll_once()
check("second poll yields the token", tok is not None and tok.access_token == "gho_ok")


# ---------------------------------------------------------------------------
print("[3] device flow -- errors")
fake = FakeRequests()
github_auth.requests = fake
fake.queue("login/device/code",
           _Resp({"device_code": "dc", "user_code": "AAAA", "interval": 1, "expires_in": 900}))
fake.queue("login/oauth/access_token", _Resp({"error": "access_denied"}))
flow = DeviceFlow(client_id="Iv1.test")
flow.start()
try:
    flow.poll_once()
    check("access_denied raises", False)
except GitHubAuthError as exc:
    check("access_denied raises", "cancel" in str(exc).lower())

flow2 = DeviceFlow(client_id="")
try:
    flow2.start()
    check("missing client id raises", False)
except GitHubAuthError:
    check("missing client id raises", True)


# ---------------------------------------------------------------------------
print("[4] refresh")
fake = FakeRequests()
github_auth.requests = fake
fake.queue("login/oauth/access_token",
           _Resp({"access_token": "gho_new", "expires_in": 28800, "token_type": "bearer"}))
old = GitHubToken(access_token="gho_old", refresh_token="ghr_keep", expires_at=1)
new = refresh(old, client_id="Iv1.test")
check("new access token", new.access_token == "gho_new")
check("refresh token carried over when omitted", new.refresh_token == "ghr_keep")

no_rt = GitHubToken(access_token="x")
try:
    refresh(no_rt, client_id="Iv1.test")
    check("refresh without a refresh token raises", False)
except GitHubAuthError:
    check("refresh without a refresh token raises", True)


# ---------------------------------------------------------------------------
print("[5] token vault round-trip")
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "github.bin"
    store = GitHubTokenStore(path)
    check("empty vault -> None", store.load() is None)
    saved = store.save(GitHubToken(access_token="gho_v", refresh_token="ghr_v", expires_at=0))
    check("save reports success", saved is True)
    check("file exists", path.exists())
    back = store.load()
    check("load returns the token", back is not None and back.access_token == "gho_v")
    raw = path.read_bytes()
    check("blob is not plaintext json", not raw.lstrip().startswith(b"{"))
    store.clear()
    check("clear removes it", store.load() is None)


# ---------------------------------------------------------------------------
print("[6] secret_store rejects a foreign / corrupt file")
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "x.bin"
    p.write_bytes(b"not encrypted, not json")
    check("garbage -> None", secret_store.EncryptedJsonStore(p).load() is None)
    p.write_text('{"hello": 1}', encoding="utf-8")
    check("bare json tolerated", secret_store.EncryptedJsonStore(p).load() == {"hello": 1})


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
