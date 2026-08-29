"""Offline tests for the sign-in front door. Run:

    .venv\\Scripts\\python.exe test_login_window.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog

from login_window import LoginWindow

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
    """Just the surface LoginWindow touches."""

    signed_in = Signal(dict)
    signed_out = Signal()
    error = Signal(str)
    busy_changed = Signal(bool)
    avatar_ready = Signal(bytes)
    profile_ready = Signal(dict)

    def __init__(self):
        super().__init__()
        self.calls = []

    def sign_in_with_google(self):
        self.calls.append("sign_in")
        self.busy_changed.emit(True)

    def cancel_sign_in(self):
        self.calls.append("cancel")
        self.busy_changed.emit(False)


# ---------------------------------------------------------------------------
print("[1] renders signed-out, idle")
acc = FakeAccount()
w = LoginWindow(acc, {})
check("primary is the Google button", w._primary.text() == "Continue with Google")
check("primary enabled", w._primary.isEnabled())
check("secondary is the quit button (no signed-out path)",
      w._link.text() == "Quit AgentDeck")
check("no result mode yet", w.result_mode() == "")
check("status line hidden", not w._status.isVisibleTo(w))


# ---------------------------------------------------------------------------
print("[2] the secondary button rejects the dialog (quit), never accepts")
acc = FakeAccount()
w = LoginWindow(acc, {})
done = []
w.finished.connect(done.append)
w._on_link()
check("dialog rejected", done == [QDialog.Rejected])
check("no result mode -- nothing signed in", w.result_mode() == "")
check("account was never touched", acc.calls == [])


# ---------------------------------------------------------------------------
print("[3] Google button starts sign-in and shows the waiting state")
acc = FakeAccount()
w = LoginWindow(acc, {})
finished = []
w.finished.connect(finished.append)
w._on_primary()
check("sign_in_with_google called once", acc.calls == ["sign_in"])
check("primary switches to waiting", w._primary.text().startswith("Waiting"))
check("primary disabled while waiting", not w._primary.isEnabled())
check("secondary becomes Cancel", w._link.text() == "Cancel")

w._on_link()  # Cancel
check("cancel_sign_in called", acc.calls == ["sign_in", "cancel"])
check("back to idle after cancel", w._primary.text() == "Continue with Google")
check("dialog still open after cancel", finished == [] and w.result_mode() == "")


# ---------------------------------------------------------------------------
print("[4] signed_in accepts the dialog in signed-in mode")
acc = FakeAccount()
w = LoginWindow(acc, {})
done = []
w.finished.connect(done.append)
w._on_primary()
acc.signed_in.emit({"id": "u1", "email": "a@b.com"})
check("dialog accepted", done == [QDialog.Accepted])
check("mode is signed-in", w.result_mode() == "signed-in")


# ---------------------------------------------------------------------------
print("[5] an error resets the button and shows the message")
acc = FakeAccount()
w = LoginWindow(acc, {})
w._on_primary()
acc.error.emit("Google sign-in isn't enabled for this AgentDeck project yet.")
check("button back to idle", w._primary.text() == "Continue with Google" and w._primary.isEnabled())
check("secondary back to the quit button",
      w._link.text() == "Quit AgentDeck")
check("status visible with the message",
      w._status.isVisibleTo(w) and "isn't enabled" in w._status.text())
check("no result mode (still open)", w.result_mode() == "")

# a later busy=False must not wipe an accepted mode
acc2 = FakeAccount()
w2 = LoginWindow(acc2, {})
w2._on_primary()
acc2.signed_in.emit({"id": "x"})
w2._on_busy(False)
check("busy=False after accept keeps signed-in mode", w2.result_mode() == "signed-in")


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
