"""The floating voice-to-text widget that hovers over the terminal area.

A small draggable pill: a mic toggle + an equaliser. It is a child of the panel
window (see ``terminal_panel._build_voice`` for why it is parented there and
kept over the panes with :meth:`set_bounds`).

Driven by three setters:

* :meth:`set_state` -- ``idle`` / ``loading`` / ``listening`` / ``error`` /
  ``unavailable``. Drives the mic glyph, the bar colour, the pill border, and a
  short caption that the bars crossfade to.
* :meth:`set_level` -- per-block mic RMS while listening; the bars react.
* :meth:`flash_text` -- a finished utterance; shown over the bars for a beat,
  then they fade back.

The widget owns no audio code -- it emits :attr:`toggle_requested` and reflects
what :class:`VoiceEngine` reports.
"""

from __future__ import annotations

import math
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
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

__all__ = ["VoiceOverlay", "mic_icon"]

# The overlay's own blue accent, distinct from the panel's.
_ACCENT = "#4c8dff"
_REC = "#ff4d4d"
_LOAD = "#f0a43a"
_IDLE_BAR = "#5a5a5a"
_TILE_TOP = "#242424"
_TILE_BOT = "#191919"
_BORDER = "#3a3a3a"
_MUTED = "#9a9a9a"

_W, _H = 210, 38

#: A fixed, gently uneven "resting" equaliser shape (0..1 per bar).
_REST = [0.30, 0.50, 0.72, 0.94, 0.60, 0.40, 0.66, 0.90, 0.52, 0.34, 0.44, 0.62, 0.30]


def mic_icon(px: int = 18, color: str = "#d6d6d6") -> QIcon:
    """A small drawn microphone -- reliable where an emoji font isn't.

    Used for the panel toolbar's voice toggle; the overlay's own mic button
    draws its glyph inline (it changes with state).
    """
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    _paint_mic(p, px / 2.0, px / 2.0, px / 18.0, QColor(color))
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
    """A small round button; glyph + colour follow the state."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("voiceMic")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(26, 26)
        self.setFocusPolicy(Qt.NoFocus)
        self._state = "idle"
        self._pulse = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(40)
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
        self._pulse = (self._pulse + 0.05) % 1.0
        self.update()

    def _fg(self) -> QColor:
        return {
            "listening": QColor("#ffffff"),
            "loading": QColor(_LOAD),
            "error": QColor(_REC),
            "unavailable": QColor("#606060"),
        }.get(self._state, QColor("#dcdcdc"))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = 12.0
        cx, cy = self.width() / 2.0, self.height() / 2.0

        if self._pulse_timer.isActive():
            grow = 5.0 * math.sin(self._pulse * math.pi)
            ring = QColor(_REC if self._state == "listening" else _LOAD)
            ring.setAlphaF(0.20 * (1.0 - self._pulse))
            p.setPen(Qt.NoPen)
            p.setBrush(ring)
            p.drawEllipse(QRectF(cx - r - grow, cy - r - grow,
                                 2 * (r + grow), 2 * (r + grow)))

        disc = QColor("#2c2c2c")
        if self._state == "listening":
            disc = QColor(_REC)
        elif self.underMouse() and self.isEnabled():
            disc = QColor("#363636")
        p.setPen(QPen(QColor(_REC if self._state == "listening" else "#454545"), 1))
        p.setBrush(disc)
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

        fg = self._fg()
        if self._state == "loading":
            pen = QPen(fg, 2.0)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx - 5.5, cy - 5.5, 11, 11),
                      int(-self._pulse * 360 * 16), 250 * 16)
        elif self._state == "listening":
            p.setPen(Qt.NoPen)
            p.setBrush(fg)
            p.drawRoundedRect(QRectF(cx - 4.6, cy - 4.6, 9.2, 9.2), 2, 2)
        else:
            _paint_mic(p, cx, cy, 0.82, fg)


# ---------------------------------------------------------------------------
# Equaliser (also renders the caption the bars crossfade to)
# ---------------------------------------------------------------------------

class _Equalizer(QWidget):
    _BARS = len(_REST)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._mode = "idle"           # idle | listening | loading
        self._phase = 0.0
        self._level = 0.0
        self._target = 0.0
        self._caption = ""
        self._cap_alpha = 0.0         # 0 = bars, 1 = caption
        self._cap_color = QColor(_MUTED)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # -- bar animation -----------------------------------------------------

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode in ("listening", "loading"):
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._level = self._target = 0.0
        self.update()

    def set_level(self, rms: float) -> None:
        self._target = max(0.0, min(1.0, rms * 7.0))

    def _tick(self) -> None:
        self._phase += 0.34
        self._level += (self._target - self._level) * 0.35
        self._target *= 0.90
        self.update()

    # -- caption ---------------------------------------------------------------

    def set_caption(self, text: str, color: QColor) -> None:
        self._caption = text or ""
        self._cap_color = color

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
        bar_dim = 1.0 - 0.82 * self._cap_alpha

        n = self._BARS
        bw = 3.0
        gap = (w - n * bw) / (n - 1) if n > 1 else 0.0
        mid = h / 2.0
        cap = h - 3.0

        for i in range(n):
            if self._mode == "listening":
                wob = 0.42 + 0.58 * abs(math.sin(self._phase + i * 0.7))
                frac = 0.26 + (0.16 + 0.95 * self._level) * wob
                col = QColor(_ACCENT)
            elif self._mode == "loading":
                lead = (self._phase * 1.7) % n
                d = min((i - lead) % n, (lead - i) % n)
                frac = 0.24 + 0.72 * max(0.0, 1.0 - d / 2.4)
                col = QColor(_LOAD)
            else:
                frac = 0.24 + _REST[i] * 0.5
                col = QColor(_IDLE_BAR)

            bh = max(bw, min(cap, frac * h))
            x = i * (bw + gap)
            r = QRectF(x, mid - bh / 2.0, bw, bh)

            if self._mode == "listening" and bar_dim > 0.4:
                glow = QColor(col)
                glow.setAlphaF(0.28 * bar_dim)
                p.setPen(Qt.NoPen)
                p.setBrush(glow)
                p.drawRoundedRect(r.adjusted(-1.4, -1.4, 1.4, 1.4), bw, bw)

            col.setAlphaF(bar_dim)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(r, bw / 2.0, bw / 2.0)

        if self._cap_alpha > 0.02 and self._caption:
            c = QColor(self._cap_color)
            c.setAlphaF(self._cap_alpha)
            p.setPen(c)
            f = QFont("Segoe UI", 8)
            p.setFont(f)
            text = p.fontMetrics().elidedText(self._caption, Qt.ElideRight, w)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignVCenter | Qt.AlignLeft, text)


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

#: Caption shown per state (empty = just the bars).
_CAPTIONS = {
    "idle": "",
    "loading": "loading model…",
    "listening": "",
    "error": "voice error",
    "unavailable": "voice unavailable",
}
_CAP_COLOR = {
    "loading": QColor(_LOAD),
    "error": QColor(_REC),
    "unavailable": QColor("#b06060"),
}


class VoiceOverlay(QWidget):
    """A small draggable voice pill that sits above the terminal panes."""

    #: The user clicked the mic, or pressed Ctrl+X while the widget had focus.
    toggle_requested = Signal()

    #: The widget was dragged; carries its new top-left in parent coordinates.
    moved = Signal(QPoint)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("voiceOverlay")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.OpenHandCursor)
        self.setFixedSize(_W, _H)
        self.setToolTip("Voice input — Ctrl+Shift+X to start/stop")

        self._state = "idle"
        self._press_local: Optional[QPoint] = None
        self._dragging = False
        self._revert_token = 0
        self._bounds: Optional[QRect] = None

        row = QHBoxLayout(self)
        row.setContentsMargins(15, 6, 13, 6)
        row.setSpacing(9)

        self._mic = _MicButton(self)
        # Wrap rather than chaining clicked(bool) straight into the 0-arg signal:
        # a signal-to-signal connection across an arg-count change is fragile.
        self._mic.clicked.connect(lambda: self.toggle_requested.emit())
        row.addWidget(self._mic)

        self._eq = _Equalizer(self)
        row.addWidget(self._eq, 1)

        self._cap_anim = QPropertyAnimation(self._eq, b"capAlpha", self)
        self._cap_anim.setDuration(200)
        self._cap_anim.setEasingCurve(QEasingCurve.InOutQuad)

        self.set_state("idle")

    # -- public API ----------------------------------------------------------

    def set_state(self, state: str) -> None:
        self._state = state
        self._mic.set_state(state)
        self._mic.setToolTip({
            "idle": "Start voice input  (Ctrl+Shift+X)",
            "loading": "Loading the speech model…",
            "listening": "Listening — click or Ctrl+Shift+X to stop",
            "error": "Voice error — see the status bar",
            "unavailable": "Voice input unavailable",
        }.get(state, ""))
        self._eq.set_mode("listening" if state == "listening"
                          else "loading" if state == "loading"
                          else "idle")
        self._revert_token += 1
        self._apply_caption(_CAPTIONS.get(state, ""),
                            _CAP_COLOR.get(state, QColor(_MUTED)))
        self.update()

    def set_level(self, rms: float) -> None:
        self._eq.set_level(rms)

    def flash_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._revert_token += 1
        token = self._revert_token
        self._apply_caption(text, QColor("#d7e2f0"))
        QTimer.singleShot(2800, lambda: self._revert(token))

    def set_available(self, available: bool, reason: str = "") -> None:
        if not available:
            self.set_state("unavailable")
            if reason:
                self._mic.setToolTip(f"Voice input unavailable: {reason}")

    def caption_text(self) -> str:
        """The caption currently shown (or the pending one). Empty = just bars."""
        return self._eq._caption

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
        self._apply_caption(_CAPTIONS.get(self._state, ""),
                            _CAP_COLOR.get(self._state, QColor(_MUTED)))

    # -- drag -------------------------------------------------------------------
    #
    # A child of the panel window. event.position() is local to this widget
    # (even when the press arrives through a mouse-transparent child); mapping
    # the global cursor into the parent and subtracting that local grab point
    # gives the new top-left in parent coordinates -- the two coordinate spaces
    # never get mixed.

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self.raise_()
            self._press_local = event.position().toPoint()
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging or self._press_local is None:
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
        super().keyPressEvent(event)

    # -- paint --------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5)
        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)

        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(_TILE_TOP))
        grad.setColorAt(1.0, QColor(_TILE_BOT))
        p.fillPath(path, grad)

        border = QColor(_REC if self._state == "listening"
                        else _LOAD if self._state == "loading"
                        else _BORDER)
        p.setPen(QPen(border, 1.4))
        p.drawPath(path)

        # grab handle: two short columns of dots
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#565656"))
        gx, gy = 6.0, self.height() / 2.0 - 6.0
        for cxi in range(2):
            for cyi in range(3):
                p.drawEllipse(QRectF(gx + cxi * 4.0, gy + cyi * 6.0, 1.9, 1.9))
