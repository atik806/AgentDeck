"""Offline widget tests for the toolbar account chip + help menu.

No network, no real AccountController -- a tiny fake stands in. Run:

    .venv\\Scripts\\python.exe test_navbar.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from navbar import AccountChip, HelpButton, circular_avatar, gear_icon, help_icon

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


def png_bytes(color=0xFF3366CC):
    img = QImage(10, 10, QImage.Format_RGB32)
    img.fill(color)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


class FakeAccount(QObject):
    signed_in = Signal(dict)
    signed_out = Signal()
    error = Signal(str)
    busy_changed = Signal(bool)
    avatar_ready = Signal(bytes)
    profile_ready = Signal(dict)

    def __init__(self):
        super().__init__()
        self._in = False
        self._name = "Ada Lovelace"
        self._plan = "free"
        self._plan_expires_at = None
        self.avatar_calls = 0
        self.sign_in_calls = 0

    @property
    def is_signed_in(self):
        return self._in

    @property
    def display_name(self):
        return self._name if self._in else ""

    @property
    def email(self):
        return "ada@example.com" if self._in else ""

    @property
    def plan(self):
        # Mirror AccountController.plan: a lapsed Pro reads back as free.
        import entitlements
        if not entitlements.plan_active(self._plan, self._plan_expires_at):
            return "free"
        return self._plan

    def fetch_avatar(self):
        self.avatar_calls += 1

    def sign_in_with_google(self):
        self.sign_in_calls += 1


# ---------------------------------------------------------------------------
print("[1] drawn art renders")
check("circular_avatar fallback (no data)", not circular_avatar(None, 32, "A").isNull())
check("circular_avatar fallback size", circular_avatar(None, 32, "A").size().width() == 32)
check("circular_avatar from bytes", not circular_avatar(png_bytes(), 40, "A").isNull())
check("circular_avatar bad bytes -> fallback",
      not circular_avatar(b"not an image", 24, "Z").isNull()
      and circular_avatar(b"not an image", 24, "Z").height() == 24)
check("circular_avatar empty fallback text ok", not circular_avatar(None, 20, "").isNull())
check("gear icon", not gear_icon(16).isNull())
check("help icon", not help_icon(16).isNull())


# ---------------------------------------------------------------------------
print("[2] AccountChip reflects sign-in state")
acc = FakeAccount()
chip = AccountChip(acc)
check("signed out -> 'Sign in' label", chip._display_label() == "Sign in")
w_out = chip.sizeHint().width()

acc._in = True
acc.signed_in.emit({})
check("signed in -> shows the name", chip._display_label() == "Ada Lovelace")
check("signed in fetched the avatar", acc.avatar_calls >= 1)
check("chip grew for the name", chip.sizeHint().width() > w_out)
check("chip height matches the toolbar", chip.sizeHint().height() == 27)
check("free plan badge", chip._plan_text() == "FREE")

acc._plan = "pro"
chip.refresh()
check("pro plan badge", chip._plan_text() == "PRO")

from datetime import datetime, timedelta, timezone as _tz
acc._plan_expires_at = (datetime.now(_tz.utc) - timedelta(hours=1)).isoformat()
chip.refresh()
check("lapsed pro shows FREE badge", chip._plan_text() == "FREE")
acc._plan_expires_at = (datetime.now(_tz.utc) + timedelta(days=5)).isoformat()
chip.refresh()
check("future expiry still shows PRO", chip._plan_text() == "PRO")
acc._plan_expires_at = None

acc._name = "Grace Hopper"
chip.refresh()
check("refresh picks up a new name", chip._display_label() == "Grace Hopper")

acc.avatar_ready.emit(png_bytes())
check("avatar bytes stored from the signal", chip._avatar_bytes is not None)

acc._in = False
acc.signed_out.emit()
check("signed out again -> 'Sign in'", chip._display_label() == "Sign in")

# painting must not raise in either state
chip.resize(chip.sizeHint())
chip.grab()
acc._in = True
chip.refresh()
chip.grab()
check("paints in both states without raising", True)


# ---------------------------------------------------------------------------
print("[3] HelpButton menu")
hb = HelpButton()
menu = hb.menu()
acts = [a for a in menu.actions() if not a.isSeparator()]
check("four menu entries", len(acts) == 4)
check("entries are the expected ones",
      [a.text() for a in acts] ==
      ["Documentation", "Keyboard shortcuts", "Report an issue", "About AgentDeck"])

keys = []
about = []
hb.shortcuts_requested.connect(lambda: keys.append(1))
hb.about_requested.connect(lambda: about.append(1))
for a in acts:
    if a.text() == "Keyboard shortcuts":
        a.trigger()
    if a.text() == "About AgentDeck":
        a.trigger()
check("Keyboard shortcuts emits shortcuts_requested", keys == [1])
check("About emits about_requested", about == [1])


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
