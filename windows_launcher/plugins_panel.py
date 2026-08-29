"""The PLUGINS view -- a full-area panel the sidebar's nav strip swaps in.

Pure scaffold for now: the sidebar's "Plugins" button routes here (see
``terminal_panel._show_plugins``) and the panel shows a styled empty state.
Real plugin management lands in a later pass; keep the empty state and the
:func:`plugin_icon` helper -- the sidebar button reuses the icon.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

__all__ = ["PluginsPanel", "plugin_icon"]


def plugin_icon(px: int = 18, color: str = "#b7b7b7") -> QIcon:
    """A drawn puzzle-piece -- an emoji glyph renders broken in this Qt build."""
    px = max(8, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    u = px / 16.0

    body = QPainterPath()
    body.addRoundedRect(QRectF(2 * u, 5 * u, 12 * u, 9 * u), 2 * u, 2 * u)
    body.addEllipse(QRectF(6 * u, 1.6 * u, 4 * u, 4 * u))  # top knob
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(body.simplified())

    # Punch a socket out of the left edge so it reads as a plug-in.
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.drawEllipse(QRectF(0.4 * u, 8.0 * u, 3.6 * u, 3.6 * u))
    p.end()
    return QIcon(pm)


_QSS = """
QWidget#pluginsPanel { background: #161616; }
QLabel#pluginsTitle { color: #e6e6e6; font-size: 16px; font-weight: 700; }
QLabel#pluginsBody { color: #8a8a8a; font-size: 12px; }
"""


class PluginsPanel(QWidget):
    """Full-area panel shown when the sidebar's "Plugins" nav item is active."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("pluginsPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(10)
        root.addStretch(1)

        icon = QLabel(self)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(plugin_icon(56, "#4a4a4a").pixmap(56, 56))

        title = QLabel("No plugins yet", self)
        title.setObjectName("pluginsTitle")
        title.setAlignment(Qt.AlignCenter)

        body = QLabel("Plugin support for AgentDeck is coming soon.", self)
        body.setObjectName("pluginsBody")
        body.setAlignment(Qt.AlignCenter)

        root.addWidget(icon)
        root.addWidget(title)
        root.addWidget(body)
        root.addStretch(1)
