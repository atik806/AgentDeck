"""The WORKSPACES sidebar: a list of workspaces with a switcher and controls.

Pure view. It reads ``.name`` and ``.pane_count`` off whatever workspace objects
it is handed and emits plain signals back -- it never touches a pane or a shell.
``TerminalPanel`` owns the workspaces and decides what a click means.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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

from plugins_panel import plugin_icon

__all__ = ["WorkspaceSidebar"]


_SIDEBAR_QSS = """
QWidget#workspaceSidebar { background: #141414; }
QWidget#wsNav { background: #141414; }
QToolButton#navBtn {
    color: #b7b7b7; background: transparent; border: none; text-align: left;
    padding: 7px 10px; font-size: 12px; border-radius: 6px;
}
QToolButton#navBtn:hover { background: #1f1f1f; color: #ffffff; }
QToolButton#navBtn:checked { background: #23232b; color: #ffffff; }
QFrame#navRule { background: #232323; max-height: 1px; border: none; }
QWidget#wsHeader { background: #141414; }
QLabel#wsTitle {
    color: #8a8a8a; font-size: 10px; font-weight: bold; letter-spacing: 1px;
}
QLabel#wsCount {
    color: #8a8a8a; background: #262626; border-radius: 7px;
    padding: 0 5px; font-size: 10px;
}
QToolButton#wsAdd {
    color: #b0b0b0; background: transparent; border: none; font-size: 16px;
}
QToolButton#wsAdd:hover { color: #ffffff; background: #2d2d2d; border-radius: 4px; }

QFrame#wsRow { background: transparent; border-radius: 6px; }
QFrame#wsRow[active="true"] { background: #23232b; }
QLineEdit#wsName {
    color: #cfcfcf; background: transparent; border: none; font-size: 12px;
    padding: 0;
}
QFrame#wsRow[active="true"] QLineEdit#wsName { color: #ffffff; }
QLineEdit#wsName:!read-only {
    background: #101014; border: 1px solid #3b78ff; border-radius: 3px;
}
QLabel#wsBadge {
    color: #9a9a9a; background: #2b2b2b; border-radius: 7px;
    padding: 0 5px; font-size: 10px;
}
QFrame#wsRow[active="true"] QLabel#wsBadge { background: #34343e; color: #cfcfcf; }
QToolButton#wsEdit, QToolButton#wsClose {
    color: #9a9a9a; background: transparent; border: none; font-size: 11px;
}
QToolButton#wsEdit:hover { color: #ffffff; background: #2d2d2d; border-radius: 3px; }
QToolButton#wsClose:hover { color: #ffffff; background: #c02020; border-radius: 3px; }

QScrollArea { background: #141414; border: none; }
QScrollBar:vertical { background: #141414; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #2d2d2d; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


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
        row.setSpacing(8)

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
            f"background: {workspace.accent}; color: #ffffff;"
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
        row.addWidget(self._badge)
        row.addWidget(self._edit)
        row.addWidget(self._close)

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
    #: The "+" button was pressed.
    created = Signal()
    #: A row's close button was pressed. Carries the workspace.
    closed = Signal(object)
    #: A row was renamed inline. Carries the workspace and the new name.
    renamed = Signal(object, str)

    WIDTH = 214

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("workspaceSidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- nav strip: sits above the WORKSPACES list, one row per destination.
        nav = QWidget(self)
        nav.setObjectName("wsNav")
        nav.setAttribute(Qt.WA_StyledBackground, True)
        nav_box = QVBoxLayout(nav)
        nav_box.setContentsMargins(6, 6, 6, 5)
        nav_box.setSpacing(2)

        self._plugins_btn = QToolButton(nav)
        self._plugins_btn.setObjectName("navBtn")
        self._plugins_btn.setText("Plugins")
        self._plugins_btn.setIcon(plugin_icon(16))
        self._plugins_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._plugins_btn.setCheckable(True)
        self._plugins_btn.setCursor(Qt.PointingHandCursor)
        self._plugins_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._plugins_btn.clicked.connect(lambda: self.plugins_selected.emit())
        nav_box.addWidget(self._plugins_btn)
        root.addWidget(nav)

        rule = QFrame(self)
        rule.setObjectName("navRule")
        rule.setFixedHeight(1)
        root.addWidget(rule)

        header = QWidget(self)
        header.setObjectName("wsHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        head = QHBoxLayout(header)
        head.setContentsMargins(12, 10, 8, 8)
        head.setSpacing(6)

        title = QLabel("WORKSPACES", header)
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
        root.addWidget(header)

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
        root.addWidget(self._scroll, 1)

        self.setStyleSheet(_SIDEBAR_QSS)

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

    def set_plugins_active(self, active: bool) -> None:
        """Reflect whether the PLUGINS view (not a workspace) is on screen."""
        self._plugins_btn.setChecked(bool(active))
