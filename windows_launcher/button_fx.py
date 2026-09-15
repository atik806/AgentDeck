"""App-wide subtle hover/press feedback for every push/tool button.

Across the app, plain ``QPushButton``/``QToolButton`` instances (~96
construction sites in ~18 files) get their hover/pressed feedback purely from
static QSS colour swaps -- Qt stylesheets have no transitions, so today it's
an instant flat-colour jump. This installs one ``QApplication``-level event
filter that adds a small animated accent-coloured glow on hover and a brief
glow pulse on press, without touching any of those individual construction
sites -- it catches every current *and future* button automatically.

Usage: ``button_fx.install(app)`` once, right after the ``QApplication`` is
built (see ``main.py``). ``button_fx.exempt(widget)`` opts a specific button
out -- needed for one that already owns a ``QGraphicsEffect`` of its own,
since Qt effects don't nest (a widget can carry only one at a time -- see
``README.md``'s note on this). The settings/gear toolbar button is exempted
this way: it already carries the pulsing "update available" halo installed by
``TerminalPanel._install_update_glow``.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QPushButton,
    QToolButton,
    QWidget,
)

import theme

__all__ = ["install", "exempt"]

#: Rest / hover / press blur radii (px), kept small so it reads as a quiet
#: bit of polish on every button in the app, not a light show.
_REST_BLUR = 0.0
_HOVER_BLUR = 11.0
_PRESS_BLUR = 20.0
_HOVER_MS = 150
_PRESS_MS = 90

_NO_ANIMATION_PROP = "noAnimation"


def exempt(widget: QWidget) -> None:
    """Opt ``widget`` out of ButtonFx -- e.g. one that owns its own
    ``QGraphicsEffect`` already, which a second effect would silently clobber."""
    widget.setProperty(_NO_ANIMATION_PROP, True)


class ButtonFx(QObject):
    """QApplication-level event filter: hover glow + press pulse on every
    ``QPushButton``/``QToolButton``, current and future."""

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._entries: "dict[int, tuple[QGraphicsDropShadowEffect, QPropertyAnimation]]" = {}
        theme.manager().changed.connect(self._on_theme_changed)

    # -- effect lifecycle -----------------------------------------------------

    def _entry_for(self, widget: QWidget):
        key = id(widget)
        entry = self._entries.get(key)
        if entry is not None:
            return entry

        effect = QGraphicsDropShadowEffect(widget)
        effect.setOffset(0, 0)
        effect.setColor(QColor(theme.color("accent")))
        effect.setBlurRadius(_REST_BLUR)
        effect.setEnabled(False)
        widget.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"blurRadius", self)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def _on_finished(fx=effect) -> None:
            if fx.blurRadius() <= _REST_BLUR + 0.01:
                fx.setEnabled(False)

        anim.finished.connect(_on_finished)

        entry = (effect, anim)
        self._entries[key] = entry
        widget.destroyed.connect(lambda _obj=None, k=key: self._entries.pop(k, None))
        return entry

    def _animate_to(self, widget: QWidget, target: float, duration: int) -> None:
        effect, anim = self._entry_for(widget)
        effect.setEnabled(True)
        anim.stop()
        anim.setDuration(duration)
        anim.setStartValue(effect.blurRadius())
        anim.setEndValue(target)
        anim.start()

    # -- event filter -----------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if isinstance(obj, (QPushButton, QToolButton)) and not obj.property(_NO_ANIMATION_PROP):
            t = event.type()
            if t == QEvent.Enter and obj.isEnabled():
                self._animate_to(obj, _HOVER_BLUR, _HOVER_MS)
            elif t == QEvent.Leave:
                self._animate_to(obj, _REST_BLUR, _HOVER_MS)
            elif t == QEvent.MouseButtonPress and obj.isEnabled():
                self._animate_to(obj, _PRESS_BLUR, _PRESS_MS)
            elif t == QEvent.MouseButtonRelease and obj.isEnabled():
                target = _HOVER_BLUR if obj.underMouse() else _REST_BLUR
                self._animate_to(obj, target, _HOVER_MS)
        return super().eventFilter(obj, event)

    # -- theme -------------------------------------------------------------

    def _on_theme_changed(self, _mode: str) -> None:
        """Keep any cached glow colour in step with the active accent token
        (colour scheme or light/dark switch), not a value baked in from
        whatever scheme was active the first time a button was hovered."""
        accent = QColor(theme.color("accent"))
        for effect, _anim in self._entries.values():
            effect.setColor(accent)


_installed: Optional[ButtonFx] = None


def install(app: QApplication) -> ButtonFx:
    """Install the event filter once; safe to call more than once."""
    global _installed
    if _installed is None:
        _installed = ButtonFx(app)
        app.installEventFilter(_installed)
    return _installed
