"""The floating voice-to-text widget that hovers over the terminal area.

A small draggable capsule: a mic toggle + an equaliser. It is a child of the
panel window (see ``terminal_panel._build_voice`` for why it is parented there
and kept over the panes with :meth:`set_bounds`).

Driven by three setters:

* :meth:`set_state` -- ``idle`` / ``loading`` / ``listening`` / ``error`` /
  ``unavailable``. Drives the mic glyph, the bar colour, the capsule border, and
  a short caption that the bars crossfade to.
* :meth:`set_level` -- per-block mic RMS while listening; the bars react.
* :meth:`flash_text` -- a finished utterance; shown over the bars for a beat,
  then they fade back.

The widget owns no audio code -- it emits :attr:`toggle_requested` and reflects
what :class:`VoiceEngine` reports. Colours come from :mod:`theme`, and it
repaints itself when the app theme flips.
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

import theme

__all__ = ["VoiceOverlay", "mic_icon"]

# The capsule footprint. Kept deliberately small -- it floats over live
# terminal output, so it should read as a control chip, not a panel. A touch
# wider/taller than the original 168x30 to fit a line of interim transcript.
_W, _H = 208, 34

#: A calm, symmetric "resting" equaliser arch (0..1 per bar).
_REST = [0.30, 0.46, 0.64, 0.82, 0.92, 0.82, 0.64, 0.46, 0.30]


# ---------------------------------------------------------------------------
# Theme helpers -- one place that maps a voice state onto palette tokens.
# ---------------------------------------------------------------------------

def _c(token: str) -> QColor:
    return theme.qcolor(token)


def _bar_color(mode: str) -> QColor:
    if mode == "listening":
        return _c("voice_wave")      # the blue->teal waveform
    if mode == "loading":
        return _c("pro")             # a warm "working" amber
    return _c("voice_wave_idle")     # idle: a quiet grey


def _edge_color(state: str) -> QColor:
    if state == "listening":
        return _c("voice_border_rec")  # the "recording" ring
    if state == "loading":
        return _c("pro")
    if state == "error":
        return _c("voice_border_rec")
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
    """A small round button; glyph + colour follow the state."""

    _R = 10.0

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
        self._pulse = (self._pulse + 0.045) % 1.0
        self.update()

    def _fg(self) -> QColor:
        if self._state == "listening":
            return _c("on_accent")
        if self._state == "loading":
            return _c("pro")
        if self._state == "error":
            return _c("voice_border_rec")
        if self._state == "unavailable":
            return _c("voice_wave_idle")
        return _c("text_muted") if not self.underMouse() else _c("text")

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self._R
        cx, cy = self.width() / 2.0, self.height() / 2.0

        # A soft expanding ring while listening / loading.
        if self._pulse_timer.isActive():
            grow = 4.0 * math.sin(self._pulse * math.pi)
            ring = QColor(_c("voice_border_rec") if self._state == "listening" else _c("pro"))
            ring.setAlphaF(0.22 * (1.0 - self._pulse))
            p.setPen(Qt.NoPen)
            p.setBrush(ring)
            p.drawEllipse(QRectF(cx - r - grow, cy - r - grow,
                                 2 * (r + grow), 2 * (r + grow)))

        if self._state == "listening":
            disc = _c("voice_border_rec")
            edge = _c("voice_border_rec")
        elif self.underMouse() and self.isEnabled():
            disc = _c("surface_hover")
            edge = _c("border_hover")
        else:
            disc = _c("surface")
            edge = _c("border")
        p.setPen(QPen(edge, 1))
        p.setBrush(disc)
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

        fg = self._fg()
        if self._state == "loading":
            pen = QPen(fg, 2.0)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx - 5.0, cy - 5.0, 10, 10),
                      int(-self._pulse * 360 * 16), 250 * 16)
        elif self._state == "listening":
            p.setPen(Qt.NoPen)
            p.setBrush(fg)
            p.drawRoundedRect(QRectF(cx - 4.1, cy - 4.1, 8.2, 8.2), 2, 2)
        else:
            _paint_mic(p, cx, cy, 0.74, fg)


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
        # Per-bar heights (0..1), eased every tick so nothing snaps.
        self._heights = [0.0] * self._BARS
        self._caption = ""
        self._cap_alpha = 0.0         # 0 = bars, 1 = caption
        self._cap_color = QColor(theme.color("text_muted"))
        self._cap_italic = False
        self._cap_elide = Qt.ElideRight
        self._timer = QTimer(self)
        self._timer.setInterval(28)
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
        self._phase += 0.24
        self._level += (self._target - self._level) * 0.30
        self._target *= 0.90

        n = self._BARS
        for i in range(n):
            if self._mode == "listening":
                wob = 0.5 + 0.5 * math.sin(self._phase + i * 0.62)
                goal = 0.16 + (0.12 + 0.9 * self._level) * (0.32 + 0.68 * wob)
            elif self._mode == "loading":
                lead = (self._phase * 1.5) % n
                d = min((i - lead) % n, (lead - i) % n)
                goal = 0.20 + 0.70 * max(0.0, 1.0 - d / 2.2)
            else:
                goal = 0.22 + _REST[i] * 0.5
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
        bar_dim = 1.0 - 0.85 * self._cap_alpha

        n = self._BARS
        bw = 2.6
        gap = (w - n * bw) / (n - 1) if n > 1 else 0.0
        mid = h / 2.0
        cap = h - 4.0
        col = _bar_color(self._mode)

        if bar_dim > 0.03:
            for i in range(n):
                frac = self._heights[i] if self._timer.isActive() \
                    else 0.22 + _REST[i] * 0.5
                bh = max(bw, min(cap, frac * h))
                x = i * (bw + gap)
                r = QRectF(x, mid - bh / 2.0, bw, bh)

                if self._mode == "listening" and bar_dim > 0.4:
                    glow = QColor(col)
                    glow.setAlphaF(0.24 * bar_dim)
                    p.setPen(Qt.NoPen)
                    p.setBrush(glow)
                    p.drawRoundedRect(r.adjusted(-1.3, -1.3, 1.3, 1.3), bw, bw)

                c = QColor(col)
                c.setAlphaF(bar_dim)
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(r, bw / 2.0, bw / 2.0)

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

#: Caption shown per state (empty = just the bars).
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
    """A small draggable voice capsule that sits above the terminal panes."""

    #: The user clicked the mic, or pressed Ctrl+X while the widget had focus.
    toggle_requested = Signal()

    #: A bare Enter was pressed while the capsule held keyboard focus (it steals
    #: focus on a click/drag). The panel routes this to the active pane so
    #: "press Enter to stop dictation" still works from here.
    submit_requested = Signal()

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
        row.setContentsMargins(14, 4, 12, 4)
        row.setSpacing(8)

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
        """Dim, italic interim transcript shown over the bars while listening.

        No auto-revert -- cleared by the next :meth:`set_state` or the final
        :meth:`flash_text`. A blank string drops back to the bars.
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
        """The caption currently shown (or the pending one). Empty = just bars."""
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
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not (event.modifiers() & (
                Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier))
        ):
            # The capsule grabbed focus on a click/drag; a bare Enter here still
            # means "run the line / stop dictating".
            self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- paint --------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rad = (self.height() - 1.5) / 2.0
        rect = QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5)
        path = QPainterPath()
        path.addRoundedRect(rect, rad, rad)

        grad = QLinearGradient(0, 0, 0, self.height())
        top = _c("voice_bg")
        grad.setColorAt(0.0, top.lighter(106))
        grad.setColorAt(1.0, top.darker(108))
        p.fillPath(path, grad)

        # A hairline top highlight for a touch of depth -- barely-there so it
        # doesn't read as a line on the light (Latte) capsule.
        hi = QColor(_c("voice_text"))
        hi.setAlphaF(0.06)
        p.setPen(QPen(hi, 1))
        p.drawLine(QPointF(rad, 1.4), QPointF(self.width() - rad, 1.4))

        p.setPen(QPen(_edge_color(self._state), 1.3))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # grab handle: two short columns of dots
        p.setPen(Qt.NoPen)
        dot = _c("text_faint")
        dot.setAlphaF(0.55)
        p.setBrush(dot)
        gx, gy = 5.0, self.height() / 2.0 - 5.0
        for cxi in range(2):
            for cyi in range(3):
                p.drawEllipse(QRectF(gx + cxi * 3.5, gy + cyi * 5.0, 1.7, 1.7))
