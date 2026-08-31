"""Offline tests for account.py -- the Supabase account controller, no network.

`supabase_auth` is faked wholesale in sys.modules before `account` is imported,
so this runs with or without the real backend module present. Run:

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_account.py
"""

import os
import pathlib
import sys
import tempfile
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---------------------------------------------------------------------------
# Fake supabase_auth -- installed BEFORE `import account`
# ---------------------------------------------------------------------------

_fake = types.ModuleType("supabase_auth")


class AuthError(Exception):
    pass


class Session:
    def __init__(self, access_token="at", refresh_token="rt", expires_at=None, user=None):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at if expires_at is not None else int(time.time()) + 3600
        self.user = user or {
            "id": "u-123",
            "email": "sam@example.com",
            "user_metadata": {"full_name": "Sam Tester", "avatar_url": "https://x/a.png"},
        }

    @property
    def email(self):
        return self.user.get("email", "")

    @property
    def display_name(self):
        md = self.user.get("user_metadata", {})
        return md.get("full_name") or md.get("name") or (self.email.split("@")[0] if self.email else "")

    @property
    def avatar_url(self):
        md = self.user.get("user_metadata", {})
        return md.get("avatar_url") or md.get("picture") or ""

    @property
    def user_id(self):
        return self.user.get("id", "")

    def is_expired(self, skew_seconds=60):
        return time.time() >= self.expires_at - skew_seconds

    def to_dict(self):
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "user": self.user,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["access_token"], d["refresh_token"], d["expires_at"], d["user"])


_STORE: dict = {}


class SessionStore:
    def __init__(self, path=None):
        pass

    def load(self):
        d = _STORE.get("session")
        return Session.from_dict(d) if d else None

    def save(self, session):
        _STORE["session"] = session.to_dict()

    def clear(self):
        _STORE.pop("session", None)


class GoogleSignIn:
    PREFERRED_PORT = 51737
    next_result = None
    raise_exc = None

    def __init__(self, **kw):
        pass

    @property
    def redirect_uri(self):
        return "http://127.0.0.1:51737"

    def run(self, should_cancel=lambda: False):
        for _ in range(6):
            if should_cancel():
                raise AuthError("sign-in cancelled")
            time.sleep(0.02)
        if GoogleSignIn.raise_exc is not None:
            raise GoogleSignIn.raise_exc
        return GoogleSignIn.next_result or Session()


def refresh(session, **kw):
    if getattr(refresh, "fail", False):
        raise AuthError("refresh failed")
    return Session(access_token="at2", refresh_token="rt2", user=session.user)


def sign_out(session, **kw):
    _STORE.setdefault("signouts", []).append(1)


def fetch_user(access_token, **kw):
    return {"id": "u-123"}


def rest_select(table, access_token, *, params=None, url=None, key=None):
    n = getattr(rest_select, "fail_next", 0)
    if n > 0:
        rest_select.fail_next = n - 1
        raise Exception("401 Unauthorized")
    if table == "profiles":
        plan = getattr(rest_select, "profile_plan", "pro")
        row = {"id": "u-123", "plan": plan, "display_name": "Sam Tester"}
        row["plan_expires_at"] = getattr(rest_select, "profile_expires_at", None)
        return [row]
    if table == "user_settings":
        return [{"data": {"font_size": 14, "layout": "columns", "not_a_synced_key": 99}}]
    return []


def rest_upsert(table, row, access_token, *, url=None, key=None):
    _STORE.setdefault("upserts", []).append((table, row))
    return [row]


def rest_insert(table, row, access_token, *, url=None, key=None):
    if getattr(rest_insert, "fail", False):
        raise Exception("boom: insert failed")
    _STORE.setdefault("inserts", []).append((table, row))


def build_pkce():
    return ("verifier", "challenge")


for _name in (
    "AuthError", "Session", "SessionStore", "GoogleSignIn",
    "refresh", "sign_out", "fetch_user", "rest_select", "rest_upsert",
    "rest_insert", "build_pkce",
):
    setattr(_fake, _name, globals()[_name])
_fake.SUPABASE_URL = "https://fake.supabase.co"
_fake.SUPABASE_KEY = "sb_publishable_fake"
sys.modules["supabase_auth"] = _fake

# ---------------------------------------------------------------------------

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import account
from account import AccountController, CLOUD_KEYS, _filter_cloud

# keep the real config file untouched, and cache the avatar in a temp dir
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="agentdeck-acct-"))
account._get_config_dir = lambda: _TMP
account.save_config = lambda cfg: None

# fake requests.get so avatar fetch never hits the network
import requests as _requests


class _Resp:
    content = b"PNGDATA"
    status_code = 200

    def raise_for_status(self):
        pass


_requests.get = lambda *a, **k: _Resp()

app = QApplication(sys.argv)

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


def pump(until, ms=2000):
    """Spin the event loop until `until()` is true or `ms` elapses."""
    loop = QEventLoop()
    hit = {"v": False}

    def tick():
        if until():
            hit["v"] = True
            loop.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(15)
    QTimer.singleShot(ms, loop.quit)
    tick()
    if not hit["v"]:
        loop.exec()
    timer.stop()
    return hit["v"]


def fresh(config=None):
    _STORE.clear()
    return AccountController(config if config is not None else {})


# ---------------------------------------------------------------------------
print("[1] no stored session -> needs login")
c = fresh()
check("not signed in", c.is_signed_in is False)
check("needs_login() true", c.needs_login() is True)
check("user is None", c.user is None)
check("email falls back to config", c.email == "")

print("[2] a signed-in account is mandatory -- no config knob skips it")
c2 = fresh({"skip_login": True})  # stale key must have no effect
check("needs_login() stays true regardless of a leftover skip_login", c2.needs_login() is True)

print("[3] a stored, valid session is restored on construct")
_STORE.clear()
_STORE["session"] = Session().to_dict()
c3 = AccountController({})
check("is_signed_in", c3.is_signed_in is True)
check("needs_login() false", c3.needs_login() is False)
check("email from session", c3.email == "sam@example.com")
check("display_name from metadata", c3.display_name == "Sam Tester")
check("plan defaults to free", c3.plan == "free")

print("[4] sign in with Google drives the flow and emits signed_in")
c4 = fresh()
events = {"signed_in": [], "signed_out": [], "busy": [], "error": [], "avatar": [], "profile": []}
c4.signed_in.connect(lambda u: events["signed_in"].append(u))
c4.signed_out.connect(lambda: events["signed_out"].append(1))
c4.busy_changed.connect(lambda b: events["busy"].append(b))
c4.error.connect(lambda m: events["error"].append(m))
c4.avatar_ready.connect(lambda b: events["avatar"].append(b))
c4.profile_ready.connect(lambda p: events["profile"].append(p))
GoogleSignIn.next_result = Session()
GoogleSignIn.raise_exc = None
c4.sign_in_with_google()
check("busy went True immediately", events["busy"][:1] == [True])
pump(lambda: events["signed_in"] and events["busy"][-1:] == [False])
check("signed_in fired once", len(events["signed_in"]) == 1)
check("busy toggled True then False", events["busy"] == [True, False])
check("session persisted to the store", "session" in _STORE)
check("controller is signed in", c4.is_signed_in is True)
check("no error", events["error"] == [])

print("[5] avatar + profile fetch kick off after sign-in")
pump(lambda: events["avatar"] and events["profile"])
check("avatar_ready delivered bytes", events["avatar"] and events["avatar"][-1] == b"PNGDATA")
check("profile_ready delivered the row", bool(events["profile"]))
check("plan picked up from profile", c4.plan == "pro")

print("[5c] an expired plan_expires_at makes plan read back as free")
from datetime import datetime, timedelta, timezone as _tz

rest_select.profile_plan = "pro"
rest_select.profile_expires_at = (datetime.now(_tz.utc) - timedelta(days=1)).isoformat()
c5c = fresh()
GoogleSignIn.next_result = Session()
ev5c = []
c5c.profile_ready.connect(ev5c.append)
c5c.sign_in_with_google()
pump(lambda: ev5c)
check("raw_plan is still the stored 'pro'", c5c.raw_plan == "pro")
check("effective plan is 'free' once expired", c5c.plan == "free")
check("plan_expires_at exposed", bool(c5c.plan_expires_at))
check("expired Pro gets no settings sync", c5c.pull_cloud_settings() is None)

rest_select.profile_expires_at = (datetime.now(_tz.utc) + timedelta(days=30)).isoformat()
c5d = fresh()
GoogleSignIn.next_result = Session()
ev5d = []
c5d.profile_ready.connect(ev5d.append)
c5d.sign_in_with_google()
pump(lambda: ev5d)
check("a future expiry keeps Pro", c5d.plan == "pro")
rest_select.profile_expires_at = None  # restore for later cases

print("[6] sign out clears the session and re-arms login")
c4.sign_out()
pump(lambda: events["signed_out"])
check("signed_out fired", len(events["signed_out"]) == 1)
check("store cleared", "session" not in _STORE)
check("server sign-out was attempted", _STORE.get("signouts"))
check("needs_login() true again", c4.needs_login() is True)
check("account_email cleared", c4._config.get("account_email", "") == "")

print("[7] a second sign-in while busy is ignored")
c7 = fresh()
GoogleSignIn.next_result = Session()
c7.sign_in_with_google()
busy_after_first = c7._busy
c7.sign_in_with_google()  # must be a no-op
check("still exactly one worker", len(c7._workers) == 1 and busy_after_first is True)
pump(lambda: c7.is_signed_in)

print("[8] cancelling an in-flight sign-in reports nothing and stays signed out")
c8 = fresh()
errs = []
c8.error.connect(errs.append)
c8.sign_in_with_google()
c8.cancel_sign_in()
pump(lambda: not c8._busy)
check("no error surfaced for a user cancel", errs == [])
check("still signed out", c8.is_signed_in is False)

print("[9] a 'provider not enabled' failure gives a friendly message")
c9 = fresh()
msgs = []
c9.error.connect(msgs.append)
GoogleSignIn.raise_exc = Exception("Unsupported provider: provider is not enabled")
c9.sign_in_with_google()
pump(lambda: msgs)
GoogleSignIn.raise_exc = None
check("friendly provider-disabled message",
      msgs and "try again later" in msgs[-1].lower())

print("[10] an expired stored session refreshes on construct")
_STORE.clear()
_STORE["session"] = Session(expires_at=int(time.time()) - 10).to_dict()
refresh.fail = False
c10 = AccountController({})
check("still counts as signed in during refresh", c10.is_signed_in is True)
pump(lambda: c10.session is not None and c10.session.access_token == "at2")
check("token was refreshed", c10.session.access_token == "at2")
check("refreshed session persisted", _STORE["session"]["access_token"] == "at2")

print("[11] a dead refresh token signs the user out")
_STORE.clear()
_STORE["session"] = Session(expires_at=int(time.time()) - 10).to_dict()
refresh.fail = True
c11 = AccountController({})
outs = []
c11.signed_out.connect(lambda: outs.append(1))
pump(lambda: outs)
refresh.fail = False
check("signed_out after a failed refresh", len(outs) == 1)
check("not signed in", c11.is_signed_in is False)

print("[12] cloud settings are filtered to the sync whitelist")
check("_filter_cloud drops unknown keys",
      _filter_cloud({"font_size": 14, "window_width": 999, "junk": 1}) == {"font_size": 14})
check("every whitelisted key is allowed through",
      _filter_cloud({k: 1 for k in CLOUD_KEYS}) == {k: 1 for k in CLOUD_KEYS})

print("[13] pull_cloud_settings returns the filtered cloud data")
c13 = fresh({"account_cloud_sync": True})
GoogleSignIn.next_result = Session()
c13.sign_in_with_google()
pump(lambda: c13.is_signed_in)
pulled = c13.pull_cloud_settings()
check("pull returned the synced keys only",
      pulled == {"font_size": 14, "layout": "columns"})
c13b = fresh({"account_cloud_sync": False})
check("pull is None when sync is off", c13b.pull_cloud_settings() is None)

print("[14] push_cloud_settings upserts a filtered payload")
_STORE.pop("upserts", None)
rest_select.profile_plan = "pro"
c14 = fresh({"account_cloud_sync": True})
GoogleSignIn.next_result = Session()
c14.sign_in_with_google()
pump(lambda: c14.is_signed_in and c14.plan == "pro")  # sync needs a resolved Pro plan
c14.push_cloud_settings({"font_size": 20, "window_height": 700, "layout": "rows"})
pump(lambda: _STORE.get("upserts"))
table, row = _STORE["upserts"][-1]
check("upsert targeted user_settings", table == "user_settings")
check("payload filtered to synced keys",
      row["data"] == {"font_size": 20, "layout": "rows"} and row["user_id"] == "u-123")

print("[15] a 401 on a REST call triggers one refresh + retry")
_STORE.pop("upserts", None)
c15 = fresh({"account_cloud_sync": True})
GoogleSignIn.next_result = Session()
c15.sign_in_with_google()
pump(lambda: c15.is_signed_in)
rest_select.fail_next = 1          # first call 401s, retry after refresh succeeds
prof = []
c15.profile_ready.connect(prof.append)
errs15 = []
c15.error.connect(errs15.append)
refresh.fail = False
c15.fetch_profile()
pump(lambda: prof or errs15)
rest_select.fail_next = 0
check("profile still arrives after a transparent re-auth", bool(prof) and errs15 == [])
check("session was swapped to the refreshed token", c15.session.access_token == "at2")

print("[16b] cloud settings sync is gated to Pro even when account_cloud_sync is on")
_STORE.pop("upserts", None)
rest_select.profile_plan = "free"
c16b = fresh({"account_cloud_sync": True})
GoogleSignIn.next_result = Session()
c16b.sign_in_with_google()
pump(lambda: c16b.is_signed_in and c16b.plan == "free")
c16b.push_cloud_settings({"font_size": 20, "layout": "rows"})
pump(lambda: False, ms=250)
check("free plan: nothing pushed", _STORE.get("upserts") is None)
check("free plan: pull returns None", c16b.pull_cloud_settings() is None)
rest_select.profile_plan = "pro"  # restore for any later cases

print("[17] report_error inserts a row when signed in and reporting is on")
_STORE.pop("inserts", None)
rest_insert.fail = False
c17 = fresh({"error_reporting": True})
GoogleSignIn.next_result = Session()
c17.sign_in_with_google()
pump(lambda: c17.is_signed_in)
c17.report_error("something broke", kind="error", context={"source": "test"})
pump(lambda: _STORE.get("inserts"))
itable, irow = _STORE["inserts"][-1]
check("insert targeted app_errors", itable == "app_errors")
check("row carries the message + user + kind",
      irow["message"] == "something broke" and irow["user_id"] == "u-123"
      and irow["kind"] == "error" and irow["context"] == {"source": "test"})

print("[18] report_error is a no-op when signed out or disabled")
_STORE.pop("inserts", None)
c18a = fresh({"error_reporting": True})  # signed out
c18a.report_error("ignored")
c18b = fresh({"error_reporting": False})
GoogleSignIn.next_result = Session()
c18b.sign_in_with_google()
pump(lambda: c18b.is_signed_in)
c18b.report_error("also ignored")
pump(lambda: False, ms=200)  # let any stray worker run
check("nothing inserted while signed out / disabled", _STORE.get("inserts") is None)

print("[19] a failed report is swallowed and never re-emits error")
_STORE.pop("inserts", None)
rest_insert.fail = True
c19 = fresh({"error_reporting": True})
GoogleSignIn.next_result = Session()
c19.sign_in_with_google()
pump(lambda: c19.is_signed_in)
errs19 = []
c19.error.connect(errs19.append)
c19.report_error("will fail to send")
pump(lambda: False, ms=300)
rest_insert.fail = False
check("no error signal from a failed report", errs19 == [])

print("[16] shutdown() is safe and stops workers")
c16 = fresh()
GoogleSignIn.next_result = Session()
c16.sign_in_with_google()
c16.shutdown()
check("no workers left after shutdown", len(c16._workers) == 0)
check("shutdown didn't raise", True)

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
