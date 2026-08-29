"""Offline tests for supabase_auth.py -- no network, no Qt. Run:

    .venv\\Scripts\\python.exe test_supabase_auth.py
"""

import base64
import hashlib
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import supabase_auth
from supabase_auth import (
    AuthError,
    GoogleSignIn,
    Session,
    SessionStore,
    build_pkce,
)

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


def raises(fn, exc=Exception):
    try:
        fn()
        return False
    except exc:
        return True


# ---------------------------------------------------------------------------
print("[1] build_pkce")
verifier, challenge = build_pkce()
check("verifier length in 43..128", 43 <= len(verifier) <= 128)
check("verifier is base64url (no + / =)", not any(c in verifier for c in "+/="))
check("challenge is base64url (no + / =)", not any(c in challenge for c in "+/="))
_expected = base64.urlsafe_b64encode(
    hashlib.sha256(verifier.encode()).digest()
).rstrip(b"=").decode()
check("challenge == s256(verifier)", challenge == _expected)
check("successive verifiers differ", build_pkce()[0] != build_pkce()[0])


# ---------------------------------------------------------------------------
print("[2] Session")
_tok = {
    "access_token": "a1",
    "refresh_token": "r1",
    "expires_in": 3600,
    "user": {
        "id": "u1",
        "email": "sam.doe@example.com",
        "user_metadata": {"full_name": "Sam Doe", "avatar_url": "http://img/x.png"},
    },
}
s = Session.from_token_response(_tok)
check("tokens parsed", s.access_token == "a1" and s.refresh_token == "r1")
check("expires_in -> absolute epoch", s.expires_at > time.time() + 3000)
check("not expired now", s.is_expired() is False)
check("email", s.email == "sam.doe@example.com")
check("user_id", s.user_id == "u1")
check("display_name = full_name", s.display_name == "Sam Doe")
check("avatar_url = avatar_url", s.avatar_url == "http://img/x.png")

s_past = Session("a", "r", int(time.time()) - 10, {"email": "z@z.com"})
check("expired when expires_at is in the past", s_past.is_expired() is True)
check("display_name falls back to email local part", s_past.display_name == "z")
check("avatar_url empty when no metadata", s_past.avatar_url == "")

s_name = Session("a", "r", 0, {
    "email": "q@q.com",
    "user_metadata": {"name": "Q Person", "picture": "p.jpg"},
})
check("display_name = name", s_name.display_name == "Q Person")
check("avatar_url = picture fallback", s_name.avatar_url == "p.jpg")

s_bare = Session("a", "r", 0, {})
check("display_name last-resort is 'there'", s_bare.display_name == "there")

_round = Session.from_dict(s.to_dict())
check(
    "to_dict / from_dict round trip",
    _round.access_token == s.access_token
    and _round.refresh_token == s.refresh_token
    and _round.expires_at == s.expires_at
    and _round.user == s.user,
)
check("from_dict rejects a non-object", raises(lambda: Session.from_dict("nope"), ValueError))
check(
    "from_token_response honours an absolute expires_at",
    Session.from_token_response({"access_token": "x", "refresh_token": "y", "expires_at": 111}).expires_at == 111,
)


# ---------------------------------------------------------------------------
print("[3] SessionStore")
_dir = Path(tempfile.mkdtemp())
store = SessionStore(_dir / "session.bin")
check("missing file -> None", store.load() is None)

store.save(s)
check("save writes the file", (_dir / "session.bin").exists())
loaded = store.load()
check("round trip: access token", loaded is not None and loaded.access_token == "a1")
check("round trip: refresh token", loaded is not None and loaded.refresh_token == "r1")
check("round trip: user object", loaded is not None and loaded.user.get("id") == "u1")

store.clear()
check("clear() removes the store", store.load() is None)
store.clear()  # idempotent
check("clear() is idempotent", True)

(_dir / "session.bin").write_bytes(b"\x00\x01 not valid at all")
check("garbage bytes -> None (no raise)", store.load() is None)
(_dir / "session.bin").write_bytes(supabase_auth._MAGIC_PLAIN + b"{not json")
check("plain magic + bad json -> None", store.load() is None)
(_dir / "session.bin").write_bytes(supabase_auth._MAGIC_DPAPI + b"corrupt-cipher")
check("dpapi magic + corrupt blob -> None", store.load() is None)
(_dir / "session.bin").write_bytes(json.dumps(s.to_dict()).encode())
check("legacy unprefixed plain JSON still loads", store.load() is not None)
(_dir / "session.bin").write_bytes(
    supabase_auth._MAGIC_PLAIN + json.dumps({"access_token": "", "refresh_token": ""}).encode()
)
check("session without tokens -> None", store.load() is None)


# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _browser_hitting(path, *, echo_state=False):
    """Return a fake open_browser that, in a thread, calls the loopback back.

    Supabase's PKCE flow echoes no ``state`` on the loopback callback (the
    default here). ``echo_state=True`` simulates a provider that does, so the
    positive match path is exercised too.
    """

    def _open(authorize_url):
        query = parse_qs(urlsplit(authorize_url).query)
        target = query["redirect_to"][0] + path
        if echo_state and query.get("state"):
            sep = "&" if "?" in target else "?"
            target += f"{sep}state={query['state'][0]}"

        def _hit():
            time.sleep(0.15)
            try:
                urllib.request.urlopen(target, timeout=5).read()
            except Exception:
                pass

        threading.Thread(target=_hit, daemon=True).start()

    return _open


_orig_post = supabase_auth.requests.post
_orig_enabled = supabase_auth._google_enabled
supabase_auth._google_enabled = lambda *a, **k: True

print("[4] GoogleSignIn -- happy path")
_fake_token = {
    "access_token": "fake-access",
    "refresh_token": "fake-refresh",
    "expires_in": 3600,
    "user": {"id": "uu", "email": "e@e.com", "user_metadata": {"full_name": "E User"}},
}


def _patched_post(url, **kw):
    assert "grant_type=pkce" in url, url
    body = kw.get("json") or {}
    assert body.get("auth_code") == "testcode", body
    assert body.get("code_verifier"), body
    return _FakeResp(200, _fake_token)


supabase_auth.requests.post = _patched_post
try:
    gs = GoogleSignIn(open_browser=_browser_hitting("/?code=testcode"), timeout=10)
    sess = gs.run()
    check("run() returns a Session", isinstance(sess, Session))
    check("carries the exchanged access token", sess.access_token == "fake-access")
    check("carries the refresh token", sess.refresh_token == "fake-refresh")
    check("redirect_uri is a loopback address", sess and gs.redirect_uri.startswith("http://127.0.0.1:"))
    check("session usable: display name", sess.display_name == "E User")
finally:
    supabase_auth.requests.post = _orig_post

print("[4b] GoogleSignIn -- a matching echoed state is accepted")
supabase_auth.requests.post = _patched_post
try:
    gs = GoogleSignIn(open_browser=_browser_hitting("/?code=testcode", echo_state=True),
                      timeout=10)
    check("run() with echoed state returns a Session", isinstance(gs.run(), Session))
finally:
    supabase_auth.requests.post = _orig_post

print("[5] GoogleSignIn -- provider error callback")
try:
    gs = GoogleSignIn(
        open_browser=_browser_hitting("/?error=access_denied&error_description=You+said+no"),
        timeout=10,
    )
    gs.run()
    check("error callback raises AuthError", False)
except AuthError as exc:
    check("error callback raises AuthError with the description", "you said no" in str(exc).lower())

print("[6] GoogleSignIn -- cancelled")
try:
    GoogleSignIn(open_browser=lambda u: None, timeout=10).run(should_cancel=lambda: True)
    check("cancel raises AuthError", False)
except AuthError as exc:
    check("cancel raises AuthError", "cancel" in str(exc).lower())

print("[7] GoogleSignIn -- provider disabled")
supabase_auth._google_enabled = lambda *a, **k: False
try:
    GoogleSignIn(open_browser=lambda u: None, timeout=5).run()
    check("disabled provider raises AuthError", False)
except AuthError as exc:
    check("disabled provider raises AuthError mentioning 'enabled'", "enabled" in str(exc).lower())
supabase_auth._google_enabled = lambda *a, **k: True

print("[8] GoogleSignIn -- timeout")
try:
    GoogleSignIn(open_browser=lambda u: None, timeout=1.0).run()
    check("timeout raises AuthError", False)
except AuthError as exc:
    check("timeout raises AuthError", "timed out" in str(exc).lower())

print("[9] GoogleSignIn -- token exchange rejected")


def _reject_post(url, **kw):
    return _FakeResp(400, {"error_code": "flow_state_not_found", "msg": "bad code"})


supabase_auth.requests.post = _reject_post
try:
    GoogleSignIn(open_browser=_browser_hitting("/?code=testcode"), timeout=10).run()
    check("bad exchange raises AuthError", False)
except AuthError as exc:
    check("bad exchange raises AuthError with server message", "bad code" in str(exc))
finally:
    supabase_auth.requests.post = _orig_post

print("[9b] GoogleSignIn -- forged state callback is rejected")
try:
    GoogleSignIn(
        open_browser=_browser_hitting(
            "/?code=testcode&state=not-the-real-nonce", echo_state=False
        ),
        timeout=10,
    ).run()
    check("forged state raises AuthError", False)
except AuthError as exc:
    check("forged state raises AuthError", "security check" in str(exc).lower())
finally:
    supabase_auth._google_enabled = _orig_enabled


# ---------------------------------------------------------------------------
print("[10] module surface")
for name in (
    "SUPABASE_URL", "SUPABASE_KEY", "AuthError", "Session", "build_pkce",
    "GoogleSignIn", "refresh", "sign_out", "fetch_user", "rest_select",
    "rest_upsert", "SessionStore",
):
    check(f"exports {name}", hasattr(supabase_auth, name))
check("SUPABASE_URL has no trailing slash", not supabase_auth.SUPABASE_URL.endswith("/"))
check("publishable key only (no service secret)", supabase_auth.SUPABASE_KEY.startswith("sb_publishable_"))


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
