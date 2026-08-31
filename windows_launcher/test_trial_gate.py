"""Offline tests for the trial-ended gate. Run:

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_trial_gate.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog

import trial_gate
from trial_gate import TrialGateDialog

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

    def __init__(self, *, allowed=False):
        super().__init__()
        self.access_allowed = allowed
        self.calls = []

    def fetch_profile(self):
        self.calls.append("fetch_profile")

    def sign_out(self):
        self.calls.append("sign_out")
        self.signed_out.emit()


_urls = []
trial_gate.QDesktopServices.openUrl = lambda url: _urls.append(url.toString())


# ---------------------------------------------------------------------------
print("[1] renders, does not let the user in on its own")
acc = FakeAccount(allowed=False)
d = TrialGateDialog(acc, {})
check("primary is the upgrade button", d._primary.text() == "Upgrade to Pro")
check("re-check button present", "re-check" in d._recheck.text())
check("quit link present", d._link.text() == "Sign out & quit")
check("status hidden", not d._status.isVisibleTo(d))


# ---------------------------------------------------------------------------
print("[2] Upgrade opens the pricing page and keeps the dialog open")
_urls.clear()
acc = FakeAccount(allowed=False)
d = TrialGateDialog(acc, {})
done = []
d.finished.connect(done.append)
d._on_upgrade()
check("pricing URL opened", _urls and "vibeflow.tech/agentdeck" in _urls[-1])
check("dialog still open", done == [])
check("hint shown", d._status.isVisibleTo(d))


# ---------------------------------------------------------------------------
print("[3] re-check: still no plan -> stays open with a message")
acc = FakeAccount(allowed=False)
d = TrialGateDialog(acc, {})
done = []
d.finished.connect(done.append)
d._on_recheck()
check("fetch_profile called", acc.calls == ["fetch_profile"])
acc.profile_ready.emit({})           # profile came back, still not allowed
check("dialog still open", done == [])
check("re-check button restored", "re-check" in d._recheck.text())
check("error-ish status visible", d._status.isVisibleTo(d))


# ---------------------------------------------------------------------------
print("[4] re-check: an active plan now -> dialog accepts")
acc = FakeAccount(allowed=False)
d = TrialGateDialog(acc, {})
done = []
d.finished.connect(done.append)
d._on_recheck()
acc.access_allowed = True             # payment synced
acc.profile_ready.emit({})
check("dialog accepted", done == [QDialog.Accepted])


# ---------------------------------------------------------------------------
print("[5] Sign out & quit rejects the dialog")
acc = FakeAccount(allowed=False)
d = TrialGateDialog(acc, {})
done = []
d.finished.connect(done.append)
d._on_quit()
check("sign_out called", "sign_out" in acc.calls)
check("dialog rejected", done == [QDialog.Rejected])


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
