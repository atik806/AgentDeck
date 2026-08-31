"""Offline tests for the account / profile dialog. Run:

    .venv\\Scripts\\python.exe test_account_dialog.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton

import account_dialog
from account_dialog import AccountDialog

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


class FakeAccount(QObject):
    signed_in = Signal(dict)
    signed_out = Signal()
    error = Signal(str)
    busy_changed = Signal(bool)
    avatar_ready = Signal(bytes)
    profile_ready = Signal(dict)

    def __init__(self, *, signed_in=True, plan="free", plan_expires_at=None):
        super().__init__()
        self.is_signed_in = signed_in
        self.display_name = "Ada Lovelace"
        self.email = "ada@example.com"
        self.avatar_url = "https://example.com/a.png"
        self.plan = plan
        self.raw_plan = plan
        self.plan_expires_at = plan_expires_at
        self.calls = []

    def fetch_avatar(self):
        self.calls.append("fetch_avatar")

    def fetch_profile(self):
        self.calls.append("fetch_profile")

    def sign_out(self):
        self.calls.append("sign_out")

    def sign_in_with_google(self):
        self.calls.append("sign_in_with_google")


def _labels(d):
    return [w.text() for w in d.findChildren(QLabel)]


# ---------------------------------------------------------------------------
print("[1] signed-in view shows name, email and a FREE badge")
acc = FakeAccount(plan="free")
d = AccountDialog(acc, {"account_cloud_sync": True})
texts = _labels(d)
check("display name shown", "Ada Lovelace" in texts)
check("email shown", "ada@example.com" in texts)
check("plan badge FREE", "FREE" in texts)
check("kicked off avatar + profile refresh",
      acc.calls.count("fetch_avatar") == 1 and acc.calls.count("fetch_profile") == 1)


# ---------------------------------------------------------------------------
print("[2] a PRO plan (or a later profile_ready) shows the PRO badge")
acc = FakeAccount(plan="pro")
d = AccountDialog(acc, {})
check("plan badge PRO", "PRO" in _labels(d))

acc = FakeAccount(plan="free")
d = AccountDialog(acc, {})
acc.plan = "pro"  # the controller resolves the plan before it emits profile_ready
acc.raw_plan = "pro"
acc.profile_ready.emit({"plan": "pro"})
check("profile_ready upgrades the badge", "PRO" in _labels(d))


# ---------------------------------------------------------------------------
print("[2b] a lapsed Pro shows the FREE badge and an 'expired' note")
from datetime import datetime, timedelta, timezone as _tz

_past = (datetime.now(_tz.utc) - timedelta(days=2)).isoformat()
acc = FakeAccount(plan="free", plan_expires_at=_past)
acc.raw_plan = "pro"  # stored plan is pro, but it has lapsed -> effective free
d = AccountDialog(acc, {})
texts = _labels(d)
check("lapsed Pro -> FREE badge", "FREE" in texts and "PRO" not in texts)
check("shows an expired note", any("expired" in t.lower() for t in texts))

_future = (datetime.now(_tz.utc) + timedelta(days=20)).isoformat()
acc = FakeAccount(plan="pro", plan_expires_at=_future)
d = AccountDialog(acc, {})
check("active Pro shows a renews note",
      any("renews" in t.lower() for t in _labels(d)))


# ---------------------------------------------------------------------------
print("[3] the sync checkbox is bound to config + persisted")
saved = []
_real_save = account_dialog.save_config
try:
    account_dialog.save_config = lambda c: saved.append(dict(c))
    cfg = {"account_cloud_sync": True}
    d = AccountDialog(FakeAccount(), cfg)
    box = d.findChild(QCheckBox)
    check("checkbox reflects config", box.isChecked() is True)
    box.setChecked(False)
    check("config updated", cfg["account_cloud_sync"] is False)
    check("save_config called", saved and saved[-1]["account_cloud_sync"] is False)
finally:
    account_dialog.save_config = _real_save


# ---------------------------------------------------------------------------
print("[4] Sign out calls the controller and closes")
acc = FakeAccount()
d = AccountDialog(acc, {})
done = []
d.finished.connect(done.append)
signout = next(b for b in d.findChildren(QPushButton) if b.text() == "Sign out")
signout.click()
check("sign_out called", "sign_out" in acc.calls)
check("dialog accepted", done == [1])


# ---------------------------------------------------------------------------
print("[5] signed_out while open closes the dialog")
acc = FakeAccount()
d = AccountDialog(acc, {})
done = []
d.finished.connect(done.append)
acc.signed_out.emit()
check("closed on signed_out", len(done) == 1)


# ---------------------------------------------------------------------------
print("[6] signed-out view offers Google sign-in")
acc = FakeAccount(signed_in=False)
d = AccountDialog(acc, {})
google = next(b for b in d.findChildren(QPushButton) if "Google" in b.text())
google.click()
check("sign_in_with_google called", "sign_in_with_google" in acc.calls)
check("no avatar fetch when signed out", "fetch_avatar" not in acc.calls)


# ---------------------------------------------------------------------------
print("[7] an avatar_ready payload paints without crashing")
acc = FakeAccount()
d = AccountDialog(acc, {})
# a real PNG, rendered by Qt so the payload is always valid
from PySide6.QtCore import QBuffer
from PySide6.QtGui import QPixmap
_src = QPixmap(8, 8)
_src.fill()
_buf = QBuffer()
_buf.open(QBuffer.WriteOnly)
_src.save(_buf, "PNG")
png = bytes(_buf.data())
acc.avatar_ready.emit(png)
check("avatar label still has a pixmap", not d._avatar.pixmap().isNull())
# garbage bytes -> falls back, still no crash
acc.avatar_ready.emit(b"not an image")
check("garbage avatar bytes fall back cleanly", not d._avatar.pixmap().isNull())


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
