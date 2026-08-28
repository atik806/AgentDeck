"""The AgentDeck launch animation -- the first thing you see.

A small frameless splash: the app mark fades and scales in, the **AgentDeck**
wordmark slides up, a tagline follows, and a blue->green accent line sweeps
across underneath. It plays for about a second and a half, then fades out and
hands control to the setup wizard.

``main.py`` calls :func:`show_splash` before it builds anything else. The splash
runs on its own nested ``QEventLoop`` so ``main`` stays a straight line; a hard
timeout guarantees it can never wedge startup, and a click or a key press skips
straight to the fade-out. ``--no-splash`` (or ``show_splash: false`` in config)
turns it off entirely.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEventLoop,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

__all__ = ["AgentDeckSplash", "show_splash"]

# Catppuccin Mocha -- the palette the app marks already use.
_BASE = QColor("#1e1e2e")
_BORDER = QColor("#313244")
_TEXT = QColor("#cdd6f4")
_MUTED = QColor("#9399b2")
_BLUE = QColor("#89b4fa")
_GREEN = QColor("#a6e3a1")

_TAGLINE = "Every terminal · every agent · one deck"

_GROW_MS = 1500
_FADE_MS = 420
_SAFETY_MS = 4000


class AgentDeckSplash(QWidget):
    """The splash widget. Emits :attr:`finished` once it has faded out."""

    finished = Signal()

    def __init__(self, icon: Optional[QIcon] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.SplashScreen | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(480, 288)

        self._pix = None
        if icon is not None and not icon.isNull():
            pix = icon.pixmap(96, 96)
            self._pix = pix if not pix.isNull() else None

        self._p = 0.0          # 0..1 animation progress
        self._closing = False

        self._grow = QVariantAnimation(self)
        self._grow.setStartValue(0.0)
        self._grow.setEndValue(1.0)
        self._grow.setDuration(_GROW_MS)
        self._grow.setEasingCurve(QEasingCurve.OutCubic)
        self._grow.valueChanged.connect(self._set_p)
        self._grow.finished.connect(self._begin_close)
        self._fade: Optional[QPropertyAnimation] = None

    # -- playback ------------------------------------------------------------

    def start(self) -> None:
        self._grow.start()

    def _set_p(self, value) -> None:
        self._p = float(value)
        self.update()

    def _begin_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(_FADE_MS)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InCubic)
        fade.finished.connect(self.finished)
        fade.start()
        self._fade = fade

    def _skip(self) -> None:
        self._grow.stop()
        self._p = 1.0
        self.update()
        self._begin_close()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._skip()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self._skip()

    # -- painting ----------------------------------------------------------

    def _ramp(self, start: float, end: float) -> float:
        """This element's own 0..1 progress within the ``start..end`` window."""
        if self._p <= start:
            return 0.0
        if self._p >= end:
            return 1.0
        return (self._p - start) / (end - start)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        card = self.rect().adjusted(12, 12, -12, -12)
        path = QPainterPath()
        path.addRoundedRect(card, 18, 18)
        p.fillPath(path, _BASE)
        p.setPen(_BORDER)
        p.drawPath(path)

        cx = self.width() / 2

        # -- app mark: fade + a small scale-up --------------------------------
        if self._pix is not None:
            a = self._ramp(0.0, 0.45)
            if a > 0:
                scale = 0.86 + 0.14 * a
                w = self._pix.width() * scale
                h = self._pix.height() * scale
                p.setOpacity(a)
                p.drawPixmap(int(cx - w / 2), int(58 - h / 2 + 24),
                             int(w), int(h), self._pix)
                p.setOpacity(1.0)

        # -- wordmark: fade + slide up ---------------------------------------
        a = self._ramp(0.20, 0.62)
        if a > 0:
            p.setOpacity(a)
            f = QFont("Segoe UI", 30)
            f.setWeight(QFont.Bold)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 0.5)
            p.setFont(f)
            p.setPen(_TEXT)
            dy = (1.0 - a) * 12
            p.drawText(0, int(150 + dy), self.width(), 44,
                       Qt.AlignHCenter, "AgentDeck")
            p.setOpacity(1.0)

        # -- accent underline: sweeps left -> right -------------------------
        sweep = self._ramp(0.28, 1.0)
        if sweep > 0:
            full = 190.0
            w = full * sweep
            bar = QPainterPath()
            bar.addRoundedRect(cx - full / 2, 198, w, 3, 1.5, 1.5)
            grad = QLinearGradient(cx - full / 2, 0, cx + full / 2, 0)
            grad.setColorAt(0.0, _BLUE)
            grad.setColorAt(1.0, _GREEN)
            p.fillPath(bar, grad)

        # -- tagline --------------------------------------------------------
        a = self._ramp(0.52, 0.92)
        if a > 0:
            p.setOpacity(a)
            f = QFont("Segoe UI", 10)
            p.setFont(f)
            p.setPen(_MUTED)
            p.drawText(0, 214, self.width(), 22, Qt.AlignHCenter, _TAGLINE)
            p.setOpacity(1.0)


def show_splash(icon: Optional[QIcon] = None, *, enabled: bool = True) -> None:
    """Play the splash and return once it has finished (or been skipped).

    A no-op when ``enabled`` is false or there is no running ``QApplication``.
    """
    if not enabled:
        return
    app = QApplication.instance()
    if app is None:
        return

    splash = AgentDeckSplash(icon)

    loop = QEventLoop()
    splash.finished.connect(loop.quit)
    # A broken animation must never stop the app from starting.
    QTimer.singleShot(_SAFETY_MS, loop.quit)

    screen = app.primaryScreen()
    if screen is not None:
        center = screen.availableGeometry().center()
        splash.move(center - QPoint(splash.width() // 2, splash.height() // 2))

    splash.show()
    splash.raise_()
    splash.activateWindow()
    splash.start()

    loop.exec()

    splash.close()
    splash.deleteLater()
