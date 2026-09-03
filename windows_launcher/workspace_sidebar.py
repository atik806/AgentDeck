"""The WORKSPACES sidebar: a list of workspaces with a switcher and controls.

Pure view. It reads ``.name`` and ``.pane_count`` off whatever workspace objects
it is handed and emits plain signals back -- it never touches a pane or a shell.
``TerminalPanel`` owns the workspaces and decides what a click means.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import theme
from notes_panel import note_icon
from plugins_panel import plugin_icon

__all__ = ["WorkspaceSidebar"]


def _sidebar_qss() -> str:
    t = theme.color
    return f"""
QWidget#workspaceSidebar {{ background: {t('sidebar_bg')}; }}
QWidget#wsNav {{ background: {t('sidebar_bg')}; }}
QToolButton#navBtn {{
    color: {t('sidebar_text')}; background: transparent; border: none; text-align: left;
    padding: 7px 10px; font-size: 12px; border-radius: 6px;
}}
QToolButton#navBtn:hover {{ background: {t('sidebar_hover')}; color: {t('text')}; }}
QToolButton#navBtn:checked {{ background: {t('sidebar_active')}; color: {t('text')}; }}
QFrame#navRule {{ background: {t('separator')}; max-height: 1px; border: none; }}
QWidget#wsHeader {{ background: {t('sidebar_bg')}; }}
QLabel#wsTitle {{
    color: {t('sidebar_heading')}; font-size: 11px; font-weight: bold; letter-spacing: 0.5px;
}}
QLabel#wsCount {{
    color: {t('sidebar_heading')}; background: {t('sidebar_badge_bg')}; border-radius: 7px;
    padding: 0 5px; font-size: 10px;
}}
QToolButton#wsAdd {{
    color: {t('sidebar_text')}; background: transparent; border: none; font-size: 16px;
}}
QToolButton#wsAdd:hover {{ color: {t('text')}; background: {t('sidebar_hover')}; border-radius: 4px; }}

QFrame#wsRow {{ background: transparent; border-radius: 6px; }}
QFrame#wsRow[active="true"] {{ background: {t('sidebar_active')}; }}
QLineEdit#wsName {{
    color: {t('sidebar_text')}; background: transparent; border: none; font-size: 12px;
    padding: 0;
}}
QFrame#wsRow[active="true"] QLineEdit#wsName {{ color: {t('text')}; }}
QLineEdit#wsName:!read-only {{
    background: {t('window_bg')}; border: 1px solid {t('accent')}; border-radius: 3px;
}}
QLabel#wsBadge {{
    color: {t('sidebar_badge_text')}; background: {t('sidebar_badge_bg')}; border-radius: 7px;
    padding: 0 5px; font-size: 10px;
}}
QFrame#wsRow[active="true"] QLabel#wsBadge {{ background: {t('sidebar_hover')}; color: {t('text')}; }}
QToolButton#wsEdit, QToolButton#wsClose {{
    color: {t('sidebar_badge_text')}; background: transparent; border: none; font-size: 11px;
}}
QToolButton#wsEdit:hover {{ color: {t('text')}; background: {t('sidebar_hover')}; border-radius: 3px; }}
QToolButton#wsClose:hover {{ color: {t('on_accent')}; background: {t('danger_hover')}; border-radius: 3px; }}

QScrollArea {{ background: {t('sidebar_bg')}; border: none; }}
QScrollBar:vertical {{ background: {t('sidebar_bg')}; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t('border')}; border-radius: 4px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


class _ActivityDot(QWidget):
    """A small dot that glows while an agent is working in its workspace.

    Idle it paints nothing -- the row simply shows no dot. Busy it paints a
    solid dot wrapped in a soft halo whose radius and opacity breathe on a
    loop, so a workspace with an agent mid-task stands out in the list even
    when it is not the one on screen. The widget keeps its slot in the row
    layout either way, so switching between the two never shifts the badge.
    """

    _SIZE = 16

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(self._SIZE, self._SIZE)
        # A dot over a clickable row must not eat the click that selects it.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._busy = False
        self._phase = 0.0

        self._pulse = QVariantAnimation(self)
        self._pulse.setStartValue(0.0)
        self._pulse.setEndValue(1.0)
        self._pulse.setDuration(1400)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse.valueChanged.connect(self._on_pulse)

    def _on_pulse(self, value) -> None:
        self._phase = float(value)
        self.update()

    def set_busy(self, busy: bool) -> None:
        busy = bool(busy)
        if busy == self._busy:
            return
        self._busy = busy
        if busy:
            self.setToolTip("An agent is working in this workspace")
            self._pulse.start()
        else:
            self._pulse.stop()
            self.setToolTip("")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._busy:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)

        centre = QPointF(self.width() / 2, self.height() / 2)
        base = QColor(theme.color("activity"))

        # Halo: swells from ~4px to ~8px and fades as it grows.
        halo_r = 4.0 + 4.0 * self._phase
        glow = QColor(base)
        glow.setAlpha(int(150 * (1.0 - self._phase)) + 25)
        transparent = QColor(base)
        transparent.setAlpha(0)
        gradient = QRadialGradient(centre, halo_r)
        gradient.setColorAt(0.0, glow)
        gradient.setColorAt(1.0, transparent)
        painter.setBrush(gradient)
        painter.drawEllipse(centre, halo_r, halo_r)

        # Core dot: steady, always fully opaque.
        painter.setBrush(base)
        painter.drawEllipse(centre, 3.0, 3.0)
        painter.end()


class _WorkspaceRow(QFrame):
    """One selectable row: swatch, editable name, pane-count badge, close."""

    clicked = Signal(object)
    close_clicked = Signal(object)
    renamed = Signal(object, str)

    def __init__(self, workspace, active: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self._ws = workspace
        self._active = active

        self.setObjectName("wsRow")
        self.setProperty("active", "true" if active else "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(6)

        # Accent bar down the left edge, only drawn for the active row.
        self._accent = QFrame(self)
        self._accent.setFixedWidth(3)
        self._accent.setStyleSheet(
            f"background: {workspace.accent if active else 'transparent'};"
            " border-radius: 1px;"
        )

        self._swatch = QLabel((workspace.name[:1] or "?").upper(), self)
        self._swatch.setObjectName("wsSwatch")
        self._swatch.setAlignment(Qt.AlignCenter)
        self._swatch.setFixedSize(18, 18)
        self._swatch.setStyleSheet(
            f"background: {workspace.accent}; color: {theme.color('on_accent')};"
            " border-radius: 5px; font-size: 9px; font-weight: bold;"
        )

        self._name = QLineEdit(workspace.name, self)
        self._name.setObjectName("wsName")
        self._name.setReadOnly(True)
        self._name.setFrame(False)
        self._name.setCursorPosition(0)
        # While read-only the field must not swallow the click that selects the
        # row; double-click flips this back on so the text can be edited.
        self._name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._name.editingFinished.connect(self._commit_rename)

        # Glows while an agent is working in this workspace; sits just left of
        # the pane-count badge and keeps its slot whether it is lit or not.
        self._dot = _ActivityDot(self)

        self._badge = QLabel(str(workspace.pane_count), self)
        self._badge.setObjectName("wsBadge")
        self._badge.setAlignment(Qt.AlignCenter)

        self._edit = QToolButton(self)
        self._edit.setObjectName("wsEdit")
        self._edit.setText("✎")
        self._edit.setCursor(Qt.PointingHandCursor)
        self._edit.setFixedSize(18, 18)
        self._edit.setToolTip("Rename workspace")
        self._edit.setVisible(active)
        self._edit.clicked.connect(self._begin_rename)

        self._close = QToolButton(self)
        self._close.setObjectName("wsClose")
        self._close.setText("✕")
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setFixedSize(18, 18)
        self._close.setToolTip("Close workspace")
        self._close.setVisible(active)
        self._close.clicked.connect(lambda: self.close_clicked.emit(self._ws))

        row.addWidget(self._accent)
        row.addWidget(self._swatch)
        row.addWidget(self._name, 1)
        row.addWidget(self._dot)
        row.addWidget(self._badge)
        row.addWidget(self._edit)
        row.addWidget(self._close)

        self._sync_activity()

    # -- activity -------------------------------------------------------------

    @property
    def workspace(self):
        return self._ws

    def _sync_activity(self) -> None:
        """Light or clear the glow dot from the workspace's live busy state."""
        probe = getattr(self._ws, "is_busy", None)
        self._dot.set_busy(bool(probe()) if callable(probe) else False)

    # -- interaction -----------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802
        self._edit.setVisible(True)
        self._close.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._edit.setVisible(self._active)
        self._close.setVisible(self._active)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._name.isReadOnly():
            self.clicked.emit(self._ws)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self._begin_rename()
        event.accept()

    def _begin_rename(self) -> None:
        self._name.setReadOnly(False)
        self._name.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._name.setFocus(Qt.MouseFocusReason)
        self._name.selectAll()
        # The :!read-only stylesheet rule only takes effect after a re-polish.
        self._name.style().unpolish(self._name)
        self._name.style().polish(self._name)

    def _commit_rename(self) -> None:
        if self._name.isReadOnly():
            return
        self._name.setReadOnly(True)
        self._name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._name.deselect()
        self._name.setCursorPosition(0)
        self._name.style().unpolish(self._name)
        self._name.style().polish(self._name)

        new_name = self._name.text().strip()
        if new_name and new_name != self._ws.name:
            self.renamed.emit(self._ws, new_name)
        else:
            self._name.setText(self._ws.name)


class WorkspaceSidebar(QWidget):
    """The column of workspaces plus the header that adds and counts them."""

    #: A row was clicked. Carries the workspace.
    selected = Signal(object)
    #: The "Plugins" nav button was pressed.
    plugins_selected = Signal()
    #: The "Notes" nav button was pressed.
    notes_selected = Signal()
    #: The "+" button was pressed.
    created = Signal()
    #: A row's close button was pressed. Carries the workspace.
    closed = Signal(object)
    #: A row was renamed inline. Carries the workspace and the new name.
    renamed = Signal(object, str)

    WIDTH = 232

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("workspaceSidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- nav strip: pinned to the top, above the Workspaces header; one
        #    row per destination ("Plugins", "Notes").
        nav = QWidget(self)
        nav.setObjectName("wsNav")
        nav.setAttribute(Qt.WA_StyledBackground, True)
        nav_box = QVBoxLayout(nav)
        nav_box.setContentsMargins(6, 8, 6, 6)
        nav_box.setSpacing(2)

        def _nav_button(text: str, icon, on_click) -> QToolButton:
            btn = QToolButton(nav)
            btn.setObjectName("navBtn")
            btn.setText(text)
            btn.setIcon(icon)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(on_click)
            nav_box.addWidget(btn)
            return btn

        self._plugins_btn = _nav_button(
            "Plugins", plugin_icon(16), lambda: self.plugins_selected.emit()
        )
        self._notes_btn = _nav_button(
            "Notes", note_icon(16), lambda: self.notes_selected.emit()
        )

        # The nav strip is pinned to the top of the sidebar, above the
        # Workspaces header -- see the addWidget order below. This rule
        # separates it from the header.
        rule = QFrame(self)
        rule.setObjectName("navRule")
        rule.setFixedHeight(1)

        header = QWidget(self)
        header.setObjectName("wsHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        head = QHBoxLayout(header)
        head.setContentsMargins(12, 10, 8, 8)
        head.setSpacing(6)

        title = QLabel("Workspaces", header)
        title.setObjectName("wsTitle")
        self._count = QLabel("0", header)
        self._count.setObjectName("wsCount")

        add = QToolButton(header)
        add.setObjectName("wsAdd")
        add.setText("+")
        add.setCursor(Qt.PointingHandCursor)
        add.setFixedSize(20, 20)
        add.setToolTip("New workspace (Ctrl+Shift+N)")
        add.clicked.connect(lambda: self.created.emit())

        head.addWidget(title)
        head.addWidget(self._count)
        head.addStretch(1)
        head.addWidget(add)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = QWidget()
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(6, 4, 6, 6)
        self._list.setSpacing(2)
        self._list.addStretch(1)
        self._scroll.setWidget(inner)

        # Top-pinned nav strip: the nav buttons, then a hairline rule, then
        # the Workspaces header and the scrolling workspace list.
        root.addWidget(nav)
        root.addWidget(rule)
        root.addWidget(header)
        root.addWidget(self._scroll, 1)

        self.setStyleSheet(_sidebar_qss())

    def apply_theme(self) -> None:
        """Re-skin for the current light/dark theme."""
        self.setStyleSheet(_sidebar_qss())

    def refresh(self, workspaces, active) -> None:
        """Rebuild every row. Cheap: there are only ever a handful of these."""
        while self._list.count() > 1:  # keep the trailing stretch
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for workspace in workspaces:
            row = _WorkspaceRow(workspace, workspace is active)
            row.clicked.connect(self.selected)
            row.close_clicked.connect(self.closed)
            row.renamed.connect(self.renamed)
            self._list.insertWidget(self._list.count() - 1, row)

        self._count.setText(str(len(workspaces)))

    def refresh_activity(self) -> None:
        """Update every row's glow dot from its workspace's live busy state.

        Cheap enough for the panel's 1 s status tick: no row is rebuilt, each
        dot just flips on or off (and only the rows that changed repaint).
        """
        for i in range(self._list.count()):
            row = self._list.itemAt(i).widget()
            if isinstance(row, _WorkspaceRow):
                row._sync_activity()

    def set_plugins_active(self, active: bool) -> None:
        """Reflect whether the PLUGINS view (not a workspace) is on screen."""
        self._plugins_btn.setChecked(bool(active))

    def set_notes_active(self, active: bool) -> None:
        """Reflect whether the NOTES view (not a workspace) is on screen."""
        self._notes_btn.setChecked(bool(active))
