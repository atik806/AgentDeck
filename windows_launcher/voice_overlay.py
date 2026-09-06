"""The floating voice-to-text widget that hovers over the terminal area.

A small draggable strip: a mic toggle + a dense mirrored waveform, styled after
a voice-memo recorder. It is a child of the panel window (see
``terminal_panel._build_voice`` for why it is parented there and kept over the
panes with :meth:`set_bounds`).

Driven by these setters:

* :meth:`set_state` -- ``idle`` / ``loading`` / ``listening`` / ``error`` /
  ``unavailable``. Drives the mic glyph, the wave motion, the strip's edge, and
  a short caption that the wave crossfades to.
* :meth:`set_level` -- per-block mic RMS while listening; the wave swells from
  the centre out.
* :meth:`set_partial` -- dim interim transcript shown over the wave.
* :meth:`flash_text` -- a finished utterance; shown over the wave for a beat,
  then it fades back.

The widget owns no audio code -- it emits :attr:`toggle_requested` /
:attr:`dismiss_requested` and reflects what :class:`VoiceEngine` reports.
Colours come from :mod:`theme`, and it repaints itself when the app theme flips.
"""

from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

import theme

__all__ = ["VoiceOverlay", "mic_icon"]

# The strip footprint. Kept deliberately small -- it floats over live terminal
# output, so it should read as a control chip, not a panel.
_W, _H = 230, 42

#: Corner radius of the strip (a rounded rectangle, not a full pill).
_RADIUS = 12.0

#: Bars in the waveform.
_BARS = 40

#: A smooth taper (low at the ends, full in the middle) -- the silhouette the
#: bars are drawn inside, so the wave reads as one rounded "clip" rather than a
#: row of sticks. Filled in at import.
_ENV: list[float] = []
for _i in range(_BARS):
    _t = _i / (_BARS - 1)
    _ENV.append(0.16 + 0.84 * math.sin(math.pi * _t) ** 0.72)
del _i, _t


# ---------------------------------------------------------------------------
# Theme helpers -- one place that maps a voice state onto palette tokens.
# ---------------------------------------------------------------------------

def _c(token: str) -> QColor:
    return theme.qcolor(token)


def _wave_color(mode: str) -> QColor:
    """Monochrome by design -- the "on air" cue is the strip's red edge, not a
    coloured wave (matches the voice-memo reference)."""
    if mode == "listening":
        return _c("voice_wave")          # a brighter grey while live
    return _c("voice_wave_idle")         # a quiet grey otherwise


def _edge_color(state: str) -> QColor:
    if state in ("listening", "error"):
        return _c("voice_border_rec")     # the "recording" ring
    if state == "loading":
        return _c("voice_border")
    return _c("voice_border")


def mic_icon(px: int = 18, color: Optional[str] = None) -> QIcon:
    """A small drawn microphone -- reliable where an emoji font isn't.

    Used for the panel toolbar's voice toggle; the overlay's own mic button
    draws its glyph inline (it changes with state).
    """
    qc = QColor(color) if color else _c("text_muted")
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    _paint_mic(p, px / 2.0, px / 2.0, px / 18.0, qc)
    p.end()
    return QIcon(pm)


def _paint_mic(p: QPainter, cx: float, cy: float, s: float, c: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(cx - 3.3 * s, cy - 7.4 * s, 6.6 * s, 10.2 * s),
                      3.3 * s, 3.3 * s)
    pen = QPen(c, 1.6 * s)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(cx - 5.8 * s, cy - 5.2 * s, 11.6 * s, 11.6 * s), 200 * 16, 140 * 16)
    p.drawLine(QPointF(cx, cy + 3.1 * s), QPointF(cx, cy + 6.6 * s))
    p.drawLine(QPointF(cx - 3.0 * s, cy + 6.6 * s), QPointF(cx + 3.0 * s, cy + 6.6 * s))


# ---------------------------------------------------------------------------
# Mic button
# ---------------------------------------------------------------------------

class _MicButton(QPushButton):
    """A small, flat, borderless toggle; glyph + tint follow the state."""

    _R = 10.5

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("voiceMic")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(22, 22)
        self.setFocusPolicy(Qt.NoFocus)
        self._state = "idle"
        self._pulse = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(33)
        self._pulse_timer.timeout.connect(self._tick)

    def set_state(self, state: str) -> None:
        self._state = state
        if state in ("listening", "loading"):
            if not self._pulse_timer.isActive():
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._pulse = 0.0
        self.setEnabled(state != "unavailable")
        self.update()

    def _tick(self) -> None:
        self._pulse = (self._pulse + 0.04) % 1.0
        self.update()

    def _fg(self) -> QColor:
        if self._state == "listening":
            return _c("on_accent")
        if self._state == "error":
            return _c("voice_border_rec")
        if self._state == "unavailable":
            return _c("voice_wave_idle")
        if self._state == "loading":
            return _c("text_muted")
        return _c("text") if self.underMouse() else _c("text_muted")

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self._R
        cx, cy = self.width() / 2.0, self.height() / 2.0

        # One faint expanding ring while active -- a quiet "alive" tick.
        if self._pulse_timer.isActive():
            ring = QColor(_c("voice_border_rec") if self._state == "listening"
                          else _c("text_faint"))
            grow = 4.0 * self._pulse
            ring.setAlphaF(0.18 * (1.0 - self._pulse))
            p.setPen(Qt.NoPen)
            p.setBrush(ring)
            p.drawEllipse(QRectF(cx - r - grow, cy - r - grow,
                                 2 * (r + grow), 2 * (r + grow)))

        if self._state == "listening":
            p.setPen(Qt.NoPen)
            p.setBrush(_c("voice_border_rec"))
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        elif self.underMouse() and self.isEnabled():
            p.setPen(Qt.NoPen)
            p.setBrush(_c("surface_hover"))
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

        fg = self._fg()
        if self._state == "loading":
            pen = QPen(fg, 2.0)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx - 5.2, cy - 5.2, 10.4, 10.4),
                      int(-self._pulse * 360 * 16), 250 * 16)
        elif self._state == "listening":
            p.setPen(Qt.NoPen)
            p.setBrush(fg)
            p.drawRoundedRect(QRectF(cx - 4.2, cy - 4.2, 8.4, 8.4), 2.2, 2.2)
        else:
            _paint_mic(p, cx, cy, 0.8, fg)


# ---------------------------------------------------------------------------
# Waveform (also renders the caption the wave crossfades to)
# ---------------------------------------------------------------------------

class _Waveform(QWidget):
    """A dense mirrored waveform. Bars ease toward a per-tick goal so nothing
    snaps; the timer runs even when idle so a slim resting trace stays alive."""

    _BARS = _BARS

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._mode = "idle"           # idle | listening | loading
        self._phase = 0.0
        self._level = 0.0
        self._target = 0.0
        self._heights = [0.0] * self._BARS
        self._caption = ""
        self._cap_alpha = 0.0         # 0 = wave, 1 = caption
        self._cap_color = QColor(theme.color("text_muted"))
        self._cap_italic = False
        self._cap_elide = Qt.ElideRight
        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- bar animation -----------------------------------------------------

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode != "listening":
            self._level = self._target = 0.0
        self.update()

    def set_level(self, rms: float) -> None:
        self._target = max(0.0, min(1.0, rms * 7.0))

    def _tick(self) -> None:
        self._phase += 0.22
        self._level += (self._target - self._level) * 0.32
        self._target *= 0.90

        n = self._BARS
        for i in range(n):
            t = i / (n - 1)
            centre_lead = 1.0 - 0.55 * abs(t - 0.5) * 2.0  # centre reacts fuller
            if self._mode == "listening":
                wob = 0.60 + 0.40 * math.sin(self._phase * 1.7 + i * 0.7)
                goal = 0.07 + _ENV[i] * (0.10 + 1.15 * self._level * wob) * centre_lead
            elif self._mode == "loading":
                pos = (self._phase * 0.16) % 1.0
                d = abs(t - pos)
                goal = 0.07 + _ENV[i] * (0.20 + 0.60 * max(0.0, 1.0 - d * 4.5))
            elif self._mode in ("error", "unavailable"):
                goal = 0.06 + _ENV[i] * 0.12
            else:
                breathe = 0.5 + 0.5 * math.sin(self._phase * 0.11 + i * 0.4)
                goal = 0.06 + _ENV[i] * (0.15 + 0.05 * breathe)
            goal = max(0.0, min(1.0, goal))
            self._heights[i] += (goal - self._heights[i]) * 0.34
        self.update()

    # -- caption ---------------------------------------------------------------

    def set_caption(self, text: str, color: QColor,
                    italic: bool = False, elide=Qt.ElideRight) -> None:
        self._caption = text or ""
        self._cap_color = color
        self._cap_italic = italic
        self._cap_elide = elide

    def _get_cap_alpha(self) -> float:
        return self._cap_alpha

    def _set_cap_alpha(self, value: float) -> None:
        self._cap_alpha = value
        self.update()

    capAlpha = Property(float, _get_cap_alpha, _set_cap_alpha)

    # -- paint -------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        wave_dim = 1.0 - 0.88 * self._cap_alpha

        n = self._BARS
        pitch = w / n
        bw = min(3.4, pitch * 0.64)
        rad = bw / 2.0
        mid = h / 2.0
        span = h - 6.0
        col = _wave_color(self._mode)

        if wave_dim > 0.03:
            base = QColor(col)
            base.setAlphaF(wave_dim)
            p.setPen(Qt.NoPen)
            p.setBrush(base)
            for i in range(n):
                bh = max(bw, self._heights[i] * span)
                x = i * pitch + (pitch - bw) / 2.0
                p.drawRoundedRect(QRectF(x, mid - bh / 2.0, bw, bh), rad, rad)

        if self._cap_alpha > 0.02 and self._caption:
            c = QColor(self._cap_color)
            c.setAlphaF(self._cap_alpha)
            p.setPen(c)
            f = QFont("Segoe UI", 8)
            f.setItalic(self._cap_italic)
            p.setFont(f)
            text = p.fontMetrics().elidedText(self._caption, self._cap_elide, w)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignVCenter | Qt.AlignLeft, text)


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

#: Caption shown per state (empty = just the wave).
_CAPTIONS = {
    "idle": "",
    "loading": "loading model…",
    "listening": "",
    "error": "voice error",
    "unavailable": "voice unavailable",
}


def _cap_color(state: str) -> QColor:
    if state == "loading":
        return _c("pro")
    if state in ("error", "unavailable"):
        return _c("voice_border_rec")
    return _c("text_muted")


class VoiceOverlay(QWidget):
    """A small draggable voice strip that sits above the terminal panes."""

    #: The user clicked the mic, or pressed Ctrl+X while the widget had focus.
    toggle_requested = Signal()

    #: The user clicked the strip's × -- hide the overlay.
    dismiss_requested = Signal()

    #: A bare Enter was pressed while the strip held keyboard focus (it steals
    #: focus on a click/drag). The panel routes this to the active pane so
    #: "press Enter to stop dictation" still works from here.
    submit_requested = Signal()

    #: The widget was dragged; carries its new top-left in parent coordinates.
    moved = Signal(QPoint)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("voiceOverlay")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)
        self.setFixedSize(_W, _H)
        self.setToolTip("Voice input — Ctrl+Shift+X to start/stop")

        self._state = "idle"
        self._press_local: Optional[QPoint] = None
        self._dragging = False
        self._revert_token = 0
        self._bounds: Optional[QRect] = None
        self._hover_close = False
        self._edge_phase = 0.0

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 24, 5)
        row.setSpacing(8)

        self._mic = _MicButton(self)
        # Wrap rather than chaining clicked(bool) straight into the 0-arg signal:
        # a signal-to-signal connection across an arg-count change is fragile.
        self._mic.clicked.connect(lambda: self.toggle_requested.emit())
        row.addWidget(self._mic)

        self._eq = _Waveform(self)
        row.addWidget(self._eq, 1)

        self._cap_anim = QPropertyAnimation(self._eq, b"capAlpha", self)
        self._cap_anim.setDuration(200)
        self._cap_anim.setEasingCurve(QEasingCurve.InOutQuad)

        # A slow breathing pulse on the strip's edge while "on air".
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(50)
        self._edge_timer.timeout.connect(self._edge_tick)

        try:
            theme.manager().changed.connect(self._on_theme_changed)
        except Exception:  # noqa: BLE001 - theming is optional in tests
            pass

        self.set_state("idle")

    # -- public API ----------------------------------------------------------

    def set_state(self, state: str) -> None:
        self._state = state
        self._mic.set_state(state)
        self._mic.setToolTip({
            "idle": "Start voice input  (Ctrl+Shift+X)",
            "loading": "Loading the speech model…",
            "listening": "Listening — click, Ctrl+Shift+X, or Enter to stop",
            "error": "Voice error — see the status bar",
            "unavailable": "Voice input unavailable",
        }.get(state, ""))
        self._eq.set_mode("listening" if state == "listening"
                          else "loading" if state == "loading"
                          else "idle")
        if state in ("listening", "error"):
            if not self._edge_timer.isActive():
                self._edge_timer.start()
        else:
            self._edge_timer.stop()
            self._edge_phase = 0.0
        self._revert_token += 1
        self._apply_caption(_CAPTIONS.get(state, ""), _cap_color(state))
        self.update()

    def set_level(self, rms: float) -> None:
        self._eq.set_level(rms)

    def set_progress(self, pct: int) -> None:
        """Show a first-run model-download percentage in the caption area.

        Only meaningful while ``loading``; cleared by the next :meth:`set_state`.
        """
        if self._state != "loading":
            return
        pct = max(0, min(100, int(pct)))
        self._eq.set_caption(f"model {pct}%", _cap_color("loading"))
        if self._eq.capAlpha < 1.0:
            self._cap_anim.stop()
            self._eq.capAlpha = 1.0
        self._eq.update()

    def set_partial(self, text: str) -> None:
        """Dim, italic interim transcript shown over the wave while listening.

        No auto-revert -- cleared by the next :meth:`set_state` or the final
        :meth:`flash_text`. A blank string drops back to the wave.
        """
        if self._state != "listening":
            return
        text = (text or "").strip()
        if not text:
            self._apply_caption("", _c("voice_partial_text"))
            return
        tail = text[-48:]
        self._revert_token += 1          # cancel a pending flash_text revert
        self._eq.set_caption(tail, _c("voice_partial_text"),
                             italic=True, elide=Qt.ElideLeft)
        if self._eq.capAlpha < 1.0:
            self._cap_anim.stop()
            self._eq.capAlpha = 1.0
        self._eq.update()

    def flash_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._revert_token += 1
        token = self._revert_token
        self._apply_caption(text, _c("voice_text"))
        QTimer.singleShot(2800, lambda: self._revert(token))

    def set_available(self, available: bool, reason: str = "") -> None:
        if not available:
            self.set_state("unavailable")
            if reason:
                self._mic.setToolTip(f"Voice input unavailable: {reason}")
        elif self._state == "unavailable":
            self.set_state("idle")

    def caption_text(self) -> str:
        """The caption currently shown (or the pending one). Empty = just wave."""
        return self._eq._caption

    # -- theme -------------------------------------------------------------

    def _on_theme_changed(self, _mode: str = "") -> None:
        # Repaint everything against the new palette. Re-resolve the caption
        # colour: a system caption tracks its state, a transcript stays "text".
        is_system = self._eq._caption == _CAPTIONS.get(self._state, "")
        self._eq.set_caption(
            self._eq._caption,
            _cap_color(self._state) if is_system else _c("voice_text"),
            italic=self._eq._cap_italic, elide=self._eq._cap_elide,
        )
        self._mic.update()
        self._eq.update()
        self.update()

    # -- caption plumbing ----------------------------------------------------

    def _apply_caption(self, text: str, color: QColor) -> None:
        self._eq.set_caption(text, color)
        self._cap_anim.stop()
        self._cap_anim.setStartValue(self._eq.capAlpha)
        self._cap_anim.setEndValue(1.0 if text else 0.0)
        self._cap_anim.start()

    def _revert(self, token: int) -> None:
        if token != self._revert_token:
            return
        self._apply_caption(_CAPTIONS.get(self._state, ""), _cap_color(self._state))

    def _edge_tick(self) -> None:
        self._edge_phase += 0.09
        self.update()

    # -- drag -------------------------------------------------------------------
    #
    # A child of the panel window. event.position() is local to this widget
    # (even when the press arrives through a mouse-transparent child); mapping
    # the global cursor into the parent and subtracting that local grab point
    # gives the new top-left in parent coordinates -- the two coordinate spaces
    # never get mixed.

    def _close_rect(self) -> QRectF:
        """The × hit target at the right edge (widget coords)."""
        s = 15.0
        return QRectF(self.width() - s - 5.0, (self.height() - s) / 2.0, s, s)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            if self._close_rect().contains(event.position()):
                self.dismiss_requested.emit()
                event.accept()
                return
            self.setFocus(Qt.MouseFocusReason)
            self.raise_()
            self._press_local = event.position().toPoint()
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging or self._press_local is None:
            was = self._hover_close
            self._hover_close = self._close_rect().contains(event.position())
            if was != self._hover_close:
                self.update()
            return
        gp = event.globalPosition().toPoint()
        parent = self.parentWidget()
        if parent is not None:
            top_left = parent.mapFromGlobal(gp) - self._press_local
        else:
            top_left = self.pos() + (event.position().toPoint() - self._press_local)
        self.move(self._clamped(top_left))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
            self._press_local = None
            self.setCursor(Qt.OpenHandCursor)
            self.moved.emit(self.pos())
            event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_close:
            self._hover_close = False
            self.update()
        super().leaveEvent(event)

    def set_bounds(self, rect: Optional[QRect]) -> None:
        """Restrict the overlay to ``rect`` (parent coords); ``None`` = parent."""
        self._bounds = QRect(rect) if rect is not None else None

    def _bound_rect(self) -> Optional[QRect]:
        if self._bounds is not None:
            return self._bounds
        parent = self.parentWidget()
        if parent is not None:
            return QRect(0, 0, parent.width(), parent.height())
        return None

    def _clamped(self, point: QPoint) -> QPoint:
        rect = self._bound_rect()
        if rect is None:
            return point
        x = max(rect.left(), min(rect.right() + 1 - self.width(), point.x()))
        y = max(rect.top(), min(rect.bottom() + 1 - self.height(), point.y()))
        return QPoint(x, y)

    def clamp_into_parent(self) -> None:
        self.move(self._clamped(self.pos()))

    # -- keyboard -------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if (
            event.key() == Qt.Key_X
            and event.modifiers() & Qt.ControlModifier
            and not (event.modifiers() & Qt.ShiftModifier)
        ):
            self.toggle_requested.emit()
            event.accept()
            return
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not (event.modifiers() & (
                Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier))
        ):
            # The strip grabbed focus on a click/drag; a bare Enter here still
            # means "run the line / stop dictating".
            self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- paint --------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        path = QPainterPath()
        path.addRoundedRect(rect, _RADIUS, _RADIUS)

        p.fillPath(path, _c("voice_bg"))

        edge = _edge_color(self._state)
        if self._state in ("listening", "error"):
            pulse = 0.5 + 0.5 * math.sin(self._edge_phase)
            glow = QColor(edge)
            glow.setAlphaF(0.10 + 0.16 * pulse)
            p.setPen(QPen(glow, 3.0))
            p.drawPath(path)
            p.setPen(QPen(edge, 1.3))
        else:
            p.setPen(QPen(edge, 1.1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # The × dismiss affordance -- faint, brighter on hover (reference clips
        # it at the edge; here it's a real hit target).
        cr = self._close_rect()
        x_c = _c("text" if self._hover_close else "text_faint")
        x_c.setAlphaF(0.9 if self._hover_close else 0.45)
        xpen = QPen(x_c, 1.4)
        xpen.setCapStyle(Qt.RoundCap)
        p.setPen(xpen)
        m = 4.0
        p.drawLine(cr.left() + m, cr.top() + m, cr.right() - m, cr.bottom() - m)
        p.drawLine(cr.left() + m, cr.bottom() - m, cr.right() - m, cr.top() + m)
