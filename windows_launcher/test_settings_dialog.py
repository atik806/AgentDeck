"""Offline tests for the Settings dialog -- focus on the Updates section, which
now hosts the "Check for updates" button (moved off the toolbar). Run:

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_settings_dialog.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import theme
from config import DEFAULT_CONFIG
from settings_dialog import SettingsDialog

app = QApplication(sys.argv)
theme.init({})

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


class FakeUpdater(QObject):
    available = Signal(str, str)
    up_to_date = Signal()
    progress = Signal(int)
    ready = Signal(str)
    error = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, enabled=True, reason=""):
        super().__init__()
        self._enabled = enabled
        self._reason = reason
        self.busy = False
        self.checks = 0

    @property
    def enabled(self):
        return self._enabled

    @property
    def unavailable_reason(self):
        return self._reason

    def check(self, *, silent=False):
        self.checks += 1


def new_cfg():
    c = dict(DEFAULT_CONFIG)
    return c


# ---------------------------------------------------------------------------
print("[1] no updater -> button disabled, version line shown")
d = SettingsDialog(new_cfg(), current_version="1.2.3")
check("check button exists", hasattr(d, "_check_btn"))
check("check button disabled", not d._check_btn.isEnabled())
check("status names the current version", "1.2.3" in d._upd_status.text())
d.close()


# ---------------------------------------------------------------------------
print("[2] disabled updater -> reason surfaced")
u = FakeUpdater(enabled=False, reason="updates are managed by the installed build")
d = SettingsDialog(new_cfg(), updater=u, current_version="1.2.3")
check("button disabled", not d._check_btn.isEnabled())
check("reason shown", "installed build" in d._upd_status.text())
d.close()


# ---------------------------------------------------------------------------
print("[3] enabled updater -> button works, signals drive the status line")
u = FakeUpdater(enabled=True)
d = SettingsDialog(new_cfg(), updater=u, current_version="1.2.3")
check("button enabled", d._check_btn.isEnabled())

d._check_btn.click()
check("click calls updater.check()", u.checks == 1)
check("status shows 'Checking'", "Checking" in d._upd_status.text())

u.busy_changed.emit(True)
check("busy disables the button", not d._check_btn.isEnabled())
u.busy_changed.emit(False)
check("idle re-enables the button", d._check_btn.isEnabled())

u.up_to_date.emit()
check("up-to-date message", "latest" in d._upd_status.text())

u.available.emit("2.0.0", "notes")
check("available message names the version", "2.0.0" in d._upd_status.text())

u.progress.emit(37)
check("progress message", "37%" in d._upd_status.text())

u.ready.emit("2.0.0")
check("ready message mentions restart", "restart" in d._upd_status.text().lower())

u.error.emit("network down")
check("error message surfaced", "network down" in d._upd_status.text())


# ---------------------------------------------------------------------------
print("[4] closing the dialog drops its updater connections")
d.done(0)
before = d._upd_status.text()
u.up_to_date.emit()
u.progress.emit(99)
check("no status change after close", d._upd_status.text() == before)
check("connection list cleared", d._upd_conns == [])


# ---------------------------------------------------------------------------
print("[5] changing channel writes config + nudges a restart")
c = new_cfg()
u = FakeUpdater(enabled=True)
d = SettingsDialog(c, updater=u, current_version="1.2.3")
d._channel.setCurrentIndex(d._channel.findData("beta"))
check("config updated to beta", c["update_channel"] == "beta")
check("restart hint shown", "estart" in d._upd_status.text())
d.close()


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
