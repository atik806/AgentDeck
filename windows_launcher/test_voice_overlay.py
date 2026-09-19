"""Offline widget tests for the floating voice overlay.

Just the widget -- no engine, no audio. Run:

    .venv\\Scripts\\python.exe test_voice_overlay.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QKeyEvent, QMouseEvent, QPainter, QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

from voice_overlay import (
    _MARK_GAP, _MARK_PX, _W, _WORDMARK, VoiceOverlay, _lockup_width,
    _paint_mark, mic_icon,
)

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
check("small footprint", overlay.width() <= 230 and overlay.height() <= 42)
check("mic icon renders", not mic_icon(16).isNull())
check("waveform is dense", overlay._eq._BARS >= 24)


# ---------------------------------------------------------------------------
print("[2] state drives the mic, the bars and the caption")
for state, eq_mode, enabled, has_cap, brand in [
    ("idle", "idle", True, False, 1.0),
    ("loading", "loading", True, True, 0.0),
    ("listening", "listening", True, False, 0.0),
    ("error", "idle", True, True, 0.0),
    ("unavailable", "idle", False, True, 0.0),
]:
    overlay.set_state(state)
    check(f"{state}: bar mode {eq_mode}", overlay._eq._mode == eq_mode)
    check(f"{state}: mic enabled == {enabled}", overlay._mic.isEnabled() is enabled)
    check(f"{state}: caption {'set' if has_cap else 'empty'}",
          bool(overlay.caption_text()) is has_cap)
    check(f"{state}: brand {'shown' if brand else 'hidden'}",
          overlay.brand_target() == brand)

check("bar timer runs whenever bars are on screen",
      (overlay.set_state("listening") or overlay._eq._timer.isActive())
      and (overlay.set_state("loading") or overlay._eq._timer.isActive()))
# ...and parks once the lockup has the strip to itself. The crossfade needs an
# event loop, so drive the property here the way the animation would.
overlay.set_state("idle")
check("bar timer still runs while the lockup is fading in",
      overlay._eq._timer.isActive())
overlay._eq.brandAlpha = 1.0
check("bar timer parks once the lockup is fully up (no idle repaint)",
      not overlay._eq._timer.isActive())
overlay._eq.brandAlpha = 0.4
check("bar timer restarts as soon as a bar could show again",
      overlay._eq._timer.isActive())
overlay._eq.brandAlpha = 1.0
overlay.set_state("listening")
check("a state change restarts the timer too", overlay._eq._timer.isActive())
overlay._eq.brandAlpha = 0.0
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

# interim partial transcript
overlay.set_state("listening")
overlay.set_partial("open the config file and")
check("partial text shows in the caption", "config file" in overlay.caption_text())
check("partial is italic + left-elided",
      overlay._eq._cap_italic and overlay._eq._cap_elide == Qt.ElideLeft)
overlay.set_partial("")
check("blank partial drops back to the bars", overlay.caption_text() == "")
overlay.set_partial("half a sentence")
overlay.set_state("idle")
check("changing state clears a partial", "half a sentence" not in overlay.caption_text())
overlay.set_state("listening")
overlay.set_partial("first draft")
overlay.flash_text("First draft done")
check("a final transcript overrides the partial",
      "First draft done" in overlay.caption_text() and not overlay._eq._cap_italic)


# ---------------------------------------------------------------------------
print("[3d] backlog indicator shows a 'catching up' hint, yields to a partial")
overlay.set_state("listening")
overlay.set_backlog(2)
check("backlog count shown", "catching up" in overlay.caption_text()
      and "2" in overlay.caption_text())
overlay.set_backlog(1)
check("singular phrasing for one queued segment",
      overlay.caption_text() == "catching up…")
overlay.set_backlog(0)
check("backlog clears back to the bars", overlay.caption_text() == "")

overlay.set_backlog(3)
check("backlog shown again", "catching up" in overlay.caption_text())
overlay.set_partial("half a sentence")
check("an active partial wins over a nonzero backlog",
      "half a sentence" in overlay.caption_text())
overlay.set_backlog(4)
check("backlog update while a partial is showing does not steal the caption",
      "half a sentence" in overlay.caption_text())
overlay.set_partial("")
check("clearing the partial does not resurrect a stale backlog caption on its own",
      overlay.caption_text() == "")

overlay.set_backlog(2)
overlay.set_state("idle")
check("changing state clears any leftover backlog text",
      "catching up" not in overlay.caption_text())
overlay.set_state("listening")
check("no stray backlog caption after a fresh listening state",
      overlay.caption_text() == "")

# Regression: a real backlog session interleaves set_backlog with flash_text
# (one flash per finished segment). The next segment's set_backlog(0) must
# not blank out a flash that hasn't reverted yet.
overlay.set_state("listening")
overlay.set_backlog(1)                      # one more sentence still queued
overlay.flash_text("first sentence done")   # this segment's decode just finished
check("flash text shows right after a backlog hint",
      overlay.caption_text() == "first sentence done")
overlay.set_backlog(0)                      # the queued sentence was just dequeued
check("a draining backlog does not blank out a still-pending flash",
      overlay.caption_text() == "first sentence done")
overlay._revert(overlay._revert_token)
check("the flash still reverts normally afterwards", overlay.caption_text() == "")


# ---------------------------------------------------------------------------
print("[3e] the AgentDeck lockup is the resting look")
overlay.set_state("idle")
check("idle shows the lockup", overlay.brand_target() == 1.0)
overlay.set_state("listening")
check("the hotkey trades the lockup for the wave", overlay.brand_target() == 0.0)

# The headline flow: dictate, the transcript flashes, then the strip settles
# back onto the lockup rather than onto a bare wave.
overlay.flash_text("open the config file")
check("a transcript keeps the lockup away while it shows",
      overlay.brand_target() == 0.0)
overlay.set_state("idle")                       # Ctrl+Shift+X / Enter stopped it
check("stopping brings the lockup straight back", overlay.brand_target() == 1.0)

overlay.flash_text("a late transcript")
check("a flash over a resting strip hides the lockup",
      overlay.brand_target() == 0.0 and overlay.caption_text() == "a late transcript")
overlay._revert(overlay._revert_token)
check("once the flash reverts, the lockup returns",
      overlay.brand_target() == 1.0 and overlay.caption_text() == "")

overlay.set_state("listening")
overlay.set_partial("half a sentence")
check("a partial never brings the lockup up mid-dictation",
      overlay.brand_target() == 0.0)
overlay.set_partial("")
check("clearing a partial goes back to the wave, not the lockup",
      overlay.brand_target() == 0.0)
overlay.set_backlog(2)
check("a backlog hint leaves the lockup alone", overlay.brand_target() == 0.0)
overlay.set_state("loading")
check("loading shows its caption, not the lockup", overlay.brand_target() == 0.0)

# The lockup has to fit the centre area at the shipped chrome font, or
# _paint_brand drops the wordmark and shows the mark alone. The strip is never
# shown in this test, so run the layout by hand first -- otherwise the
# waveform reports QWidget's default 100px and this measures nothing.
import theme as _th

overlay.layout().activate()

_f = _th.chrome_font(8)
_f.setWeight(QFont.DemiBold)
_f.setLetterSpacing(QFont.AbsoluteSpacing, 0.4)
_lockup = _MARK_PX + _MARK_GAP + QFontMetricsF(_f).horizontalAdvance(_WORDMARK)
check(f"mark + wordmark fit the strip ({_lockup:.0f}px of {overlay._eq.width()}px)",
      _lockup <= overlay._eq.width())

# The mark is painted, not loaded: the packaged build excludes QtSvg and ships
# only icon.ico, so this has to hold up with no asset on disk at all.
_pm = QPixmap(64, 64)
_pm.fill(Qt.transparent)
_p = QPainter(_pm)
_p.setRenderHint(QPainter.Antialiasing, True)
_paint_mark(_p, 32.0, 32.0, 56.0)
_p.end()
_img = _pm.toImage()
_seen = {_img.pixelColor(x, y).rgb()
         for x in range(64) for y in range(64)
         if _img.pixelColor(x, y).alpha() > 250}


def _near(token, tol=26):
    want = QColor(_th.color(token))
    return any(abs(((rgb >> 16) & 255) - want.red()) <= tol
               and abs(((rgb >> 8) & 255) - want.green()) <= tol
               and abs((rgb & 255) - want.blue()) <= tol for rgb in _seen)


check("the mark draws with no asset file", len(_seen) > 20)
check("the mark carries the scheme's accent (the chevron)", _near("accent"))
check("the mark carries the cursor block", _near("activity"))


def _render(widget):
    pm = QPixmap(widget.size())
    pm.fill(Qt.transparent)
    widget.render(pm)
    return pm.toImage()


overlay.set_state("idle")
overlay._eq.capAlpha = 0.0
overlay._eq.brandAlpha = 0.0
_bars = _render(overlay._eq)
overlay._eq.brandAlpha = 1.0
_brand = _render(overlay._eq)
check("the strip really repaints into the lockup", _bars != _brand)
overlay._eq.brandAlpha = 0.0


# ---------------------------------------------------------------------------
print("[3f] the strip shrinks to the lockup and grows back for the wave")
overlay.layout().activate()
rest = overlay._rest_width()
check(f"resting width hugs the lockup ({rest}px vs {_W}px wide)", rest < _W)
check("resting width still holds the mic and the whole lockup",
      rest >= overlay._mic.width() + _lockup_width())

overlay.set_state("idle")
check("idle asks for the resting width", overlay.width_target() == rest)
check("hidden strips resize with no animation to wait on", overlay.width() == rest)
overlay.set_state("listening")
check("the wave asks for the full width", overlay.width_target() == _W)
overlay.set_state("idle")
overlay.flash_text("a transcript that wants room")
check("a caption at rest widens the strip too", overlay.width_target() == _W)
overlay._revert(overlay._revert_token)
check("and it shrinks back once the caption goes",
      overlay.width_target() == rest)

# Which edge stays put. The panel auto-places the strip in the terminal area's
# bottom-right corner, so a chip parked there must not creep inward every time
# it resizes; one dragged into open space keeps its left edge (and the mic)
# where the user put it.
overlay.set_bounds(QRect(0, 0, 900, 600))
overlay.move(900 - overlay.width() - 12, 600 - overlay.height() - 12)
_right = overlay.x() + overlay.width()
overlay.stripWidth = _W
check("a corner chip grows from its right edge",
      overlay.x() + overlay.width() == _right)
overlay.stripWidth = rest
check("...and shrinks back to the same corner",
      overlay.x() + overlay.width() == _right)

overlay.move(QPoint(40, 40))
_left = overlay.x()
overlay.stripWidth = _W
check("a chip in open space keeps its left edge", overlay.x() == _left)
overlay.stripWidth = rest
check("...both ways", overlay.x() == _left)

overlay.move(QPoint(880, 40))          # hard against the right bound
overlay.stripWidth = _W
check("a grow that would overflow is clamped back inside",
      overlay.x() + overlay.width() <= 900)

# A right-anchored resize moves the widget, and the panel stores a left edge,
# so the strip has to report where it landed -- once, on settle, not per frame.
_settles = []
overlay.moved.connect(_settles.append)
overlay.move(900 - overlay.width() - 12, 40)
overlay.stripWidth = _W                     # anchored right -> shifts x
check("a shifting resize is remembered", overlay._width_shifted is True)
overlay._on_width_settled()
check("settling reports the new position once", len(_settles) == 1)
overlay._on_width_settled()
check("and does not report it again", len(_settles) == 1)
overlay.set_state("idle")


# ---------------------------------------------------------------------------
print("[3c] voice tokens exist for both themes")
import theme as _t
for mode in ("dark", "light"):
    for tok in ("voice_bg", "voice_border", "voice_border_rec", "voice_wave",
                "voice_wave_idle", "voice_partial_text", "voice_text"):
        check(f"{mode}:{tok}", bool(_t.color(tok, mode)))


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

submits = []
overlay.submit_requested.connect(lambda: submits.append(1))
overlay.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "\r"))
check("bare Enter -> submit_requested (panel stops dictation, does not run the line)",
      submits == [1])
overlay.keyPressEvent(QKeyEvent(
    QEvent.KeyPress, Qt.Key_Return, Qt.ShiftModifier, "\r"))
check("Shift+Enter is not a submit", submits == [1])
check("Enter did not also toggle", len(toggles) == 2)


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
print("[6b] the × dismisses without starting a drag")
dismissed = []
overlay.set_available(True)
overlay.move(QPoint(40, 40))
overlay.dismiss_requested.connect(lambda: dismissed.append(1))
cr = overlay._close_rect()
pt = QPointF(cr.center())
overlay.mousePressEvent(QMouseEvent(
    QEvent.MouseButtonPress, pt, QPointF(0, 0),
    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
check("clicking the × emits dismiss_requested", dismissed == [1])
check("the × press did not start a drag", overlay._dragging is False)

before = overlay.pos()
inside = QPointF(overlay.width() / 2.0, overlay.height() / 2.0)
overlay.mousePressEvent(QMouseEvent(
    QEvent.MouseButtonPress, inside, QPointF(0, 0),
    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
check("pressing the body still starts a drag", overlay._dragging is True)
overlay.mouseReleaseEvent(QMouseEvent(
    QEvent.MouseButtonRelease, inside, QPointF(0, 0),
    Qt.LeftButton, Qt.NoButton, Qt.NoModifier))


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
print("[9] the wave actually animates")
#
# Regression: the resting curve used to move ~1px over a 7s cycle while the
# paint floor pinned every bar under `bw` to a dead minimum, so the strip
# rendered as a static row of dots. Measure the drawn bar heights the way
# paintEvent does and require real motion in every mode -- including a
# listening pause, where the mic level is ~0.

from voice_overlay import _MIN_BAR  # noqa: E402

probe = VoiceOverlay(area)
eq = probe._eq
eq._timer.stop()          # drive _tick by hand; no wall-clock dependency


def sweep(mode, ticks=600, level=None, warm=200):
    """Steady-state per-bar (min, max) drawn height, in pixels."""
    eq.set_mode(mode)
    w, h = eq.width(), eq.height()
    n = eq._BARS
    bw = min(3.4, (w / n) * 0.64)
    span, floor = h - 6.0, bw * _MIN_BAR
    for _ in range(warm + ticks):
        if level is not None:
            eq.set_level(level)
        eq._tick()
    lo = [9e9] * n
    hi = [-9e9] * n
    for _ in range(ticks):
        if level is not None:
            eq.set_level(level)
        eq._tick()
        for i in range(n):
            bh = max(floor, eq._heights[i] * span)
            lo[i] = min(lo[i], bh)
            hi[i] = max(hi[i], bh)
    return lo, hi, floor, span


for mode, level, min_swing in [
    ("idle", None, 3.0),
    ("loading", None, 3.0),
    ("listening", 0.0, 2.0),      # a pause must still ripple
    ("listening", 0.03, 6.0),     # quiet speech must read clearly
]:
    lo, hi, floor, span = sweep(mode, level=level)
    swing = [hi[i] - lo[i] for i in range(len(hi))]
    label = f"{mode}" + ("" if level is None else f" @ rms={level}")
    check(f"{label}: centre bars move ({max(swing):.1f}px)",
          max(swing) >= min_swing)
    check(f"{label}: no bar is frozen at the floor",
          not any(h <= floor + 1e-6 for h in hi))
    check(f"{label}: stays inside the strip",
          max(hi) <= span + 0.01)

# The level curve has to lift ordinary speech, not just a shout.
eq.set_mode("listening")
eq.set_level(0.0)
check("silence reads as zero", eq._target == 0.0)
eq.set_level(0.02)
quiet = eq._target
eq.set_level(0.10)
loud = eq._target
check("quiet speech already uses a third of the range", quiet >= 0.30)
check("louder speech still reads louder", loud > quiet)
check("level is clamped to 1.0", (eq.set_level(5.0), eq._target)[1] == 1.0)

probe.deleteLater()

# ---------------------------------------------------------------------------
print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
