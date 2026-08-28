"""Offline tests for the AgentDeck launch splash. Run:

    .venv\\Scripts\\python.exe test_agentdeck_splash.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from agentdeck_splash import AgentDeckSplash, show_splash

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


# ---------------------------------------------------------------------------
print("[1] paints at every stage without an icon")
s = AgentDeckSplash(QIcon())
for p in (0.0, 0.25, 0.5, 0.75, 1.0):
    s._set_p(p)
    pm = s.grab()
    check(f"grab at p={p} produced a pixmap", not pm.isNull())
s.deleteLater()


# ---------------------------------------------------------------------------
print("[2] _ramp maps sub-windows to 0..1")
s = AgentDeckSplash(QIcon())
s._p = 0.0
check("before the window -> 0", s._ramp(0.2, 0.6) == 0.0)
s._p = 0.4
check("mid window -> 0.5", abs(s._ramp(0.2, 0.6) - 0.5) < 1e-9)
s._p = 0.9
check("after the window -> 1", s._ramp(0.2, 0.6) == 1.0)
s.deleteLater()


# ---------------------------------------------------------------------------
print("[3] skip jumps straight to the fade-out and emits finished")
s = AgentDeckSplash(QIcon())
done = []
s.finished.connect(lambda: done.append(1))
s.start()
s._skip()
check("progress snapped to full", s._p == 1.0)
check("closing flag set", s._closing)
s._fade.setCurrentTime(s._fade.duration())   # fast-forward the fade
check("finished fired after the fade", done == [1])
s.deleteLater()


# ---------------------------------------------------------------------------
print("[4] show_splash(enabled=False) is a no-op and returns immediately")
show_splash(QIcon(), enabled=False)
check("returned without raising", True)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
