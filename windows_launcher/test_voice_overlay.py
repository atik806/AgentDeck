"""Offline widget tests for the floating voice overlay.

Just the widget -- no engine, no audio. Run:

    .venv\\Scripts\\python.exe test_voice_overlay.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from voice_overlay import VoiceOverlay, mic_icon

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


area = QWidget()
area.resize(900, 600)
overlay = VoiceOverlay(area)
moved = []
overlay.moved.connect(moved.append)
toggles = []
overlay.toggle_requested.connect(lambda: toggles.append(1))


# ---------------------------------------------------------------------------
print("[1] compact fixed size")
check("small footprint", overlay.width() <= 220 and overlay.height() <= 42)
check("mic icon renders", not mic_icon(16).isNull())


# ---------------------------------------------------------------------------
print("[2] state drives the mic, the bars and the caption")
for state, eq_mode, enabled, has_cap in [
    ("idle", "idle", True, False),
    ("loading", "loading", True, True),
    ("listening", "listening", True, False),
    ("error", "idle", True, True),
    ("unavailable", "idle", False, True),
]:
    overlay.set_state(state)
    check(f"{state}: bar mode {eq_mode}", overlay._eq._mode == eq_mode)
    check(f"{state}: mic enabled == {enabled}", overlay._mic.isEnabled() is enabled)
    check(f"{state}: caption {'set' if has_cap else 'empty'}",
          bool(overlay.caption_text()) is has_cap)

check("bar timer runs only while animated",
      (overlay.set_state("listening") or overlay._eq._timer.isActive())
      and (overlay.set_state("idle") or not overlay._eq._timer.isActive()))
check("mic pulse timer runs only while active",
      (overlay.set_state("loading") or overlay._mic._pulse_timer.isActive())
      and (overlay.set_state("idle") or not overlay._mic._pulse_timer.isActive()))


# ---------------------------------------------------------------------------
print("[3] a transcription flashes over the bars, then they return")
overlay.set_state("listening")
overlay.flash_text("list the files in this directory")
check("caption carries the transcript", "list the files" in overlay.caption_text())
check("crossfade runs toward the caption", overlay._cap_anim.endValue() == 1.0)
overlay._revert(overlay._revert_token)
check("after the transcript, listening has no caption (just bars)",
      overlay.caption_text() == "")
check("crossfade runs back toward the bars", overlay._cap_anim.endValue() == 0.0)

overlay.set_state("loading")
overlay.flash_text("hello world")
overlay._revert(overlay._revert_token)
check("reverting during loading restores the loading caption",
      overlay.caption_text() == "loading model…")

# model-download progress
overlay.set_state("loading")
overlay.set_progress(42)
check("progress shows a percentage in the caption", "42%" in overlay.caption_text())
overlay.set_state("listening")
check("changing state clears the progress caption", "42%" not in overlay.caption_text())
overlay.set_state("idle")
overlay.set_progress(80)
check("progress is ignored when not loading", "80%" not in overlay.caption_text())


# ---------------------------------------------------------------------------
print("[4] mic button and Ctrl+X ask to toggle")
overlay._mic.click()
check("mic click -> toggle_requested", len(toggles) == 1)
overlay.keyPressEvent(QKeyEvent(
    QEvent.KeyPress, Qt.Key_X, Qt.ControlModifier, "\x18"))
check("Ctrl+X -> toggle_requested", len(toggles) == 2)
overlay.keyPressEvent(QKeyEvent(
    QEvent.KeyPress, Qt.Key_X, Qt.ControlModifier | Qt.ShiftModifier, ""))
check("Ctrl+Shift+X is left for the panel", len(toggles) == 2)


# ---------------------------------------------------------------------------
print("[5] clamps itself inside its bounds")
overlay.move(QPoint(10_000, 10_000))
overlay.clamp_into_parent()
check("clamped to the bottom-right",
      overlay.x() == area.width() - overlay.width()
      and overlay.y() == area.height() - overlay.height())
overlay.move(QPoint(-500, -500))
overlay.clamp_into_parent()
check("clamped back to the top-left", overlay.pos() == QPoint(0, 0))


# ---------------------------------------------------------------------------
print("[6] set_available(False) disables the mic with a reason")
overlay.set_available(False, "pywhispercpp not installed")
check("state is unavailable", overlay._state == "unavailable")
check("mic disabled", overlay._mic.isEnabled() is False)
check("reason in the tooltip", "pywhispercpp" in overlay._mic.toolTip())


# ---------------------------------------------------------------------------
print("[7] drag follows the cursor and keeps the grab point fixed")
#
# The old bug: press stored (globalPos - frameGeometry().topLeft()), mixing
# screen and parent coordinates -- only visible when the window is not at
# screen origin, so this parent fakes a screen offset.

class ShiftedParent(QWidget):
    OFFSET = QPoint(120, 70)

    def mapFromGlobal(self, p):
        return QPoint(int(p.x()), int(p.y())) - self.OFFSET


sp = ShiftedParent()
sp.resize(1600, 1000)
ov = VoiceOverlay(sp)
ov.set_bounds(QRect(0, 0, 1600, 1000))
moved2 = []
ov.moved.connect(moved2.append)

ov.move(400, 300)
press = QMouseEvent(QEvent.MouseButtonPress, QPointF(18, 12), QPointF(538, 382),
                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
ov.mousePressEvent(press)
check("press stores a LOCAL grab point inside the widget",
      ov._press_local == QPoint(18, 12)
      and 0 <= ov._press_local.x() <= ov.width())
check("dragging flag set", ov._dragging is True)

ov.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(0, 0), QPointF(638, 422),
                              Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
check("widget followed the cursor by exactly the drag delta",
      ov.pos() == QPoint(500, 340))
check("grab point still under the cursor (no teleport)",
      ov.pos() + ov._press_local == QPoint(518, 352))

ov.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(0, 0),
                                 QPointF(638, 422), Qt.LeftButton, Qt.NoButton,
                                 Qt.NoModifier))
check("release emits moved with the final position",
      moved2 and moved2[-1] == QPoint(500, 340))
check("not dragging after release", ov._dragging is False)

ov.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(10, 10),
                               QPointF(630, 420), Qt.LeftButton, Qt.LeftButton,
                               Qt.NoModifier))
ov.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(0, 0),
                              QPointF(9000, 9000), Qt.NoButton, Qt.LeftButton,
                              Qt.NoModifier))
check("drag past the edge clamps inside the bounds",
      ov.x() + ov.width() <= 1600 and ov.y() + ov.height() <= 1000)


# ---------------------------------------------------------------------------
print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
