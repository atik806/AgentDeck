"""Offline coverage for the animated update download/install dialog.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_update_progress.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

import theme
from update_progress import UpdateProgressDialog

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


print("[1] fresh dialog is in the downloading state, pulsing")
dlg = UpdateProgressDialog("9.9.9")
check("title says downloading", dlg._title.text() == "Downloading update")
check("bar is determinate 0..100", (dlg._bar.minimum(), dlg._bar.maximum()) == (0, 100))
check("glyph pulse is running", dlg._pulse.state() == QAbstractAnimation.Running)
check("pulse loops forever", dlg._pulse.loopCount() == -1)

print("[2] set_progress advances the bar and the detail line")
dlg.set_progress(42)
check("bar value tracks percent", dlg._bar.value() == 42)
check("detail shows version + percent",
      "9.9.9" in dlg._detail.text() and "42%" in dlg._detail.text())
dlg.set_progress(150)
check("percent is clamped to 100", dlg._bar.value() == 100)

print("[3] start_installing switches to the indeterminate sweep")
dlg.start_installing()
check("title says installing", dlg._title.text() == "Installing update")
check("bar is indeterminate", (dlg._bar.minimum(), dlg._bar.maximum()) == (0, 0))
check("still pulsing", dlg._pulse.state() == QAbstractAnimation.Running)
check("progress is ignored once installing",
      (dlg.set_progress(10), dlg._bar.maximum())[1] == 0)

print("[4] finish stops the animation and closes")
dlg.finish()
check("pulse stopped", dlg._pulse.state() != QAbstractAnimation.Running)
check("dialog hidden", not dlg.isVisible())

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
