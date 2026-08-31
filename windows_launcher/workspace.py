"""One workspace: a group of terminal panes with its own layout.

A :class:`Workspace` is what used to be the body of ``TerminalPanel`` -- the
list of panes, the nested :class:`QSplitter` tree that arranges them, and the
active/expanded bookkeeping. Pulling it into its own widget is what lets the
window hold several of them (one visible at a time in a ``QStackedWidget``) and
switch between them from the sidebar, with every hidden workspace's shells still
running.

Panes are arranged with nested splitters rather than a ``QGridLayout`` so the
user gets draggable dividers for free. Relayout reparents the existing pane
widgets instead of rebuilding them, which is what keeps a running shell alive
across a layout change.
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import theme
from terminal_view import TerminalView

__all__ = [
    "Workspace",
    "TerminalPane",
    "MAX_PANES",
    "LAYOUT_GRID",
    "LAYOUT_COLUMNS",
    "LAYOUT_ROWS",
    "grid_dims",
]

MAX_PANES = 16

LAYOUT_GRID = "grid"
LAYOUT_COLUMNS = "columns"
LAYOUT_ROWS = "rows"


def grid_dims(count: int) -> tuple[int, int]:
    """``(rows, cols)`` for laying ``count`` panes out near-square.

    The single source of truth for the grid layout: ``_build_tree`` arranges
    the real panes with it, and the setup wizard draws its tile previews with
    it, so the preview matches what actually opens. ``cols`` is
    ``ceil(sqrt(n))`` (columns-first, the way people read a grid).
    """
    count = max(1, int(count))
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    return rows, cols

#: Header button glyphs for the expand/restore toggle. Both are present in
#: Segoe UI, the Windows UI font, so neither falls back to a tofu box.
_EXPAND_GLYPH = "⤢"
_RESTORE_GLYPH = "⤡"


# ---------------------------------------------------------------------------
# Pane
# ---------------------------------------------------------------------------

class TerminalPane(QFrame):
    """One terminal plus the header strip that labels it."""

    #: The user asked to close this pane.
    close_requested = Signal(object)

    #: The user asked to expand this pane to fill the panel, or to restore it.
    expand_requested = Signal(object)

    #: This pane's terminal took focus.
    activated = Signal(object)

    def __init__(
        self,
        index: int,
        shell: str,
        font_size: int,
        scrollback: int,
        parent: Optional[QWidget] = None,
        *,
        cwd: Optional[str] = None,
        startup_command: Optional[str] = None,
    ):
        super().__init__(parent)
        self._index = index
        self._shell = shell
        self._scrollback = scrollback
        self._font_size = font_size
        self._cwd = cwd
        self._startup_command = startup_command
        self._active = False
        self._expanded = False

        self.setFrameShape(QFrame.NoFrame)

        # A soft accent halo around whichever pane has focus -- Qt QSS has no
        # box-shadow, so the "selected" glow is a graphics effect. Dormant
        # (disabled) until _refresh_style lights it for the active pane.
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(18)
        self._glow.setColor(QColor(theme.color("accent")))
        self._glow.setEnabled(False)
        self.setGraphicsEffect(self._glow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- header --
        self._header = QWidget(self)
        # Named so the pane stylesheet can target it; must be set before the
        # first setStyleSheet or the QWidget#paneHeaderHost rule won't match.
        self._header.setObjectName("paneHeaderHost")
        # A plain QWidget ignores a stylesheet background inherited from an
        # ancestor unless it is told to draw a styled background.
        self._header.setAttribute(Qt.WA_StyledBackground, True)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(8, 3, 4, 3)
        header_layout.setSpacing(6)

        self._badge = QLabel(str(index + 1), self._header)
        self._badge.setObjectName("paneBadge")
        self._title = QLabel("", self._header)
        self._title.setObjectName("paneTitle")
        self._title.setTextInteractionFlags(Qt.NoTextInteraction)

        self._restart_btn = QPushButton("↻", self._header)
        self._restart_btn.setObjectName("paneRestart")
        self._restart_btn.setCursor(Qt.PointingHandCursor)
        self._restart_btn.setFixedWidth(22)
        self._restart_btn.setToolTip("Restart this shell")
        self._restart_btn.clicked.connect(self.restart)

        self._expand_btn = QPushButton(_EXPAND_GLYPH, self._header)
        self._expand_btn.setObjectName("paneExpand")
        self._expand_btn.setCursor(Qt.PointingHandCursor)
        self._expand_btn.setFixedWidth(22)
        self._expand_btn.setToolTip("Expand pane (Ctrl+Shift+E)")
        self._expand_btn.clicked.connect(lambda: self.expand_requested.emit(self))

        close_btn = QPushButton("✕", self._header)
        close_btn.setObjectName("paneClose")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedWidth(22)
        close_btn.setToolTip("Close pane (Ctrl+Shift+W)")
        close_btn.clicked.connect(lambda: self.close_requested.emit(self))

        header_layout.addWidget(self._badge)
        header_layout.addWidget(self._title, 1)
        header_layout.addWidget(self._expand_btn)
        header_layout.addWidget(self._restart_btn)
        header_layout.addWidget(close_btn)
        outer.addWidget(self._header)

        # -- terminal --
        self.view = self._make_view()
        outer.addWidget(self.view, 1)

        self._was_alive = self.view.is_alive()
        self._refresh_style()

        if self.view.error:
            self._set_title(f"failed to start: {self.view.error}")

    # -- terminal wiring ---------------------------------------------------

    def _make_view(self) -> TerminalView:
        view = TerminalView(
            shell=self._shell,
            font_size=self._font_size,
            scrollback=self._scrollback,
            cwd=self._cwd,
            startup_command=self._startup_command,
            parent=self,
        )
        view.title_changed.connect(self._on_title)
        view.exited.connect(self._on_exited)
        view.focus_gained.connect(lambda: self.activated.emit(self))
        self._set_title(view.shell_label)
        return view

    def _on_title(self, title: str) -> None:
        # Shells set the title to the running command, which is genuinely
        # useful as a pane label -- but cmd prefixes the full exe path.
        cleaned = title.split(" - ", 1)[-1] if " - " in title else title
        self._set_title(cleaned.strip() or self.view.shell_label)

    def _set_title(self, text: str) -> None:
        self._title.setText(text)
        self._title.setToolTip(text)

    def _on_exited(self, code: int) -> None:
        self._was_alive = False
        self._set_title(f"{self.view.shell_label} — exited ({code})")
        self._refresh_style()

    def poll(self) -> None:
        """Catch a shell that died without the session noticing.

        The reader thread normally reports the exit, so this is a backstop --
        and it must stay cheap, because it runs once a second per pane.
        Re-applying a stylesheet forces a full re-polish of the widget tree, so
        only do it when the state has actually flipped.
        """
        alive = self.view.is_alive()
        if alive == self._was_alive:
            return
        self._was_alive = alive
        self._refresh_style()

    def restart(self) -> None:
        """Replace a dead shell with a fresh one, keeping the pane in place."""
        old = self.view
        old.close_session()

        self.view = self._make_view()
        self.layout().replaceWidget(old, self.view)
        old.deleteLater()

        self._was_alive = self.view.is_alive()
        self._refresh_style()
        self.focus_terminal()

    # -- appearance --------------------------------------------------------

    @property
    def index(self) -> int:
        return self._index

    def set_index(self, index: int) -> None:
        self._index = index
        self._badge.setText(str(index + 1))

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self._refresh_style()

    def set_expanded(self, expanded: bool) -> None:
        """Point the toggle the other way once the panel has acted on it."""
        self._expanded = expanded
        self._expand_btn.setText(_RESTORE_GLYPH if expanded else _EXPAND_GLYPH)
        self._expand_btn.setToolTip(
            "Restore layout (Ctrl+Shift+E)" if expanded
            else "Expand pane (Ctrl+Shift+E)"
        )
        self._refresh_style()

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_font_size(self, size: int) -> None:
        self._font_size = size
        self.view.set_font_size(size)

    def is_alive(self) -> bool:
        return self.view.is_alive()

    def is_busy(self) -> bool:
        """True while this pane's shell is actively producing output."""
        return self.view.is_busy()

    def apply_theme(self) -> None:
        """Repaint the pane chrome + its terminal for the current theme."""
        self.view.apply_theme()
        self._refresh_style()

    def _refresh_style(self) -> None:
        dead = not self.view.is_alive()
        t = theme.color
        if dead:
            border = t("pane_border_dead")
            badge_fg = t("on_accent")
        elif self._active:
            border = t("pane_border_active")
            badge_fg = t("on_accent")
        else:
            border = t("pane_border")
            # the badge sits on the (muted) border colour when the pane is
            # idle -- a light-on-light / dark-on-dark clash if it used on_accent
            badge_fg = t("text_muted")

        header_bg = t("pane_header_bg_active") if self._active else t("pane_header_bg")
        accent = t("accent")
        on_accent = t("on_accent")

        # Light the focus halo on the active (live) pane; keep it off for idle
        # and dead panes so the eye lands on the one taking keystrokes.
        glow = getattr(self, "_glow", None)
        if glow is not None:
            glow.setColor(QColor(t("pane_border_dead") if dead else accent))
            glow.setEnabled(self._active)
        title = t("pane_title_dead") if dead else t("pane_title")
        self.setStyleSheet(
            f"""
            TerminalPane {{
                border: 1px solid {border};
                border-radius: 10px;
                background: {theme.color('term_bg')};
            }}
            QWidget#paneHeaderHost {{
                background: {header_bg};
                border-top-left-radius: 9px;
                border-top-right-radius: 9px;
            }}
            QLabel#paneBadge {{
                color: {badge_fg};
                background: {border};
                border-radius: 7px;
                padding: 0 6px;
                font-size: 10px;
                font-weight: bold;
            }}
            QLabel#paneTitle {{
                color: {title};
                font-size: 11px;
                font-weight: {"bold" if self._active else "normal"};
            }}
            QPushButton#paneClose, QPushButton#paneRestart,
            QPushButton#paneExpand {{
                color: {t('pane_title')};
                background: transparent;
                border: none;
                font-size: 11px;
                padding: 1px 4px;
            }}
            QPushButton#paneExpand {{
                color: {on_accent if self._expanded else t('pane_title')};
                background: {accent if self._expanded else 'transparent'};
            }}
            QPushButton#paneClose:hover {{ color: {on_accent}; background: {t('danger_hover')}; }}
            QPushButton#paneRestart:hover {{ color: {on_accent}; background: {accent}; }}
            QPushButton#paneExpand:hover {{ color: {on_accent}; background: {accent}; }}
            """
        )

    # -- focus -------------------------------------------------------------

    def focus_terminal(self) -> None:
        self.view.canvas.setFocus(Qt.OtherFocusReason)

    def close_session(self) -> None:
        self.view.close_session()


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

class Workspace(QWidget):
    """A named group of terminal panes and the splitter tree arranging them."""

    #: Panes were added, removed, renumbered, expanded, or the workspace was
    #: renamed -- anything the sidebar or status bar needs to redraw for.
    changed = Signal()

    #: A pane in this workspace took focus. Carries the pane.
    active_pane_changed = Signal(object)

    #: The last pane was closed. Carries the workspace, for the panel to decide
    #: whether that means "close this workspace" or "close the app".
    empty = Signal(object)

    #: A transient message for the window's status bar.
    notice = Signal(str)

    def __init__(
        self,
        name: str,
        accent: str,
        *,
        shell: str,
        font_size: int,
        scrollback: int,
        layout_mode: str,
        cwd: Optional[str] = None,
        startup_command: Optional[str] = None,
        max_panes: int = MAX_PANES,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.name = name
        self.accent = accent

        self._shell = shell
        self._font_size = font_size
        self._scrollback = scrollback
        self._layout_mode = layout_mode
        # Plan cap on panes (Free = 4, Pro = MAX_PANES). Clamped to the hard
        # ceiling either way; TerminalPanel keeps it current on a plan change.
        self._max_panes = max(1, min(MAX_PANES, int(max_panes)))
        # Folder the panes start in, and (for the initial batch only) a command
        # to run once each shell is up -- the setup wizard's working folder and
        # chosen agent.
        self._cwd = cwd
        self._startup_command = startup_command

        self._panes: list[TerminalPane] = []
        self._active: Optional[TerminalPane] = None
        # The pane expanded to fill the workspace, if any. The others stay alive
        # and parented here, just hidden -- see _build_tree.
        self._zoomed: Optional[TerminalPane] = None
        self._root: Optional[QWidget] = None

        self._body = QVBoxLayout(self)
        # A small inset so the rounded panes float off the window edge and the
        # sidebar, rather than sitting flush against them.
        self._body.setContentsMargins(6, 6, 6, 6)
        self._body.setSpacing(0)

    # -- lifecycle -------------------------------------------------------------

    def initialize(self, count: int) -> None:
        """Fill a fresh workspace with ``count`` panes and lay them out once."""
        count = max(1, min(self._max_panes, int(count)))
        for _ in range(count):
            self._spawn_pane(with_startup=True)
        self._relayout()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        # Becoming the visible workspace should put the caret in a terminal, not
        # leave it on the sidebar the user just clicked.
        QTimer.singleShot(0, self.focus_active)

    # -- pane collection -----------------------------------------------------

    @property
    def panes(self) -> list[TerminalPane]:
        return self._panes

    @property
    def pane_count(self) -> int:
        return len(self._panes)

    @property
    def active_pane(self) -> Optional[TerminalPane]:
        return self._active

    @property
    def is_zoomed(self) -> bool:
        return self._zoomed is not None

    def _spawn_pane(self, *, with_startup: bool = False) -> TerminalPane:
        pane = TerminalPane(
            index=len(self._panes),
            shell=self._shell,
            font_size=self._font_size,
            scrollback=self._scrollback,
            cwd=self._cwd,
            startup_command=self._startup_command if with_startup else None,
        )
        pane.close_requested.connect(self.close_pane)
        pane.expand_requested.connect(self.toggle_zoom)
        pane.activated.connect(self.set_active)
        self._panes.append(pane)
        return pane

    def add_pane(self, focus: bool = True) -> Optional[TerminalPane]:
        if len(self._panes) >= self._max_panes:
            if self._max_panes < MAX_PANES:
                self.notice.emit(
                    f"Free plan: {self._max_panes} panes per workspace — "
                    f"upgrade to Pro for up to {MAX_PANES}"
                )
            else:
                self.notice.emit(f"Pane limit reached ({MAX_PANES})")
            return None

        pane = self._spawn_pane()
        # A new pane the user cannot see is not much use, so adding one leaves
        # the expanded view.
        self._zoomed = None
        self._relayout()
        if focus:
            pane.focus_terminal()
        return pane

    def close_pane(self, pane: TerminalPane) -> None:
        if pane not in self._panes:
            return

        if len(self._panes) == 1:
            # The panel decides what closing the final pane means.
            self.empty.emit(self)
            return

        position = self._panes.index(pane)
        self._panes.remove(pane)
        if self._zoomed is pane:
            self._zoomed = None
        pane.close_session()
        pane.setParent(None)
        pane.deleteLater()

        if self._active is pane:
            self._active = None

        self._relayout()

        if self._panes:
            self._panes[min(position, len(self._panes) - 1)].focus_terminal()

    def close_active_pane(self) -> None:
        pane = self._active or (self._panes[0] if self._panes else None)
        if pane is not None:
            self.close_pane(pane)

    # -- focus / cycling ---------------------------------------------------

    def set_active(self, pane: TerminalPane) -> None:
        if self._active is pane:
            return
        if self._active is not None:
            self._active.set_active(False)
        self._active = pane
        pane.set_active(True)
        self.active_pane_changed.emit(pane)

    def cycle(self, step: int) -> None:
        if len(self._panes) < 2:
            return
        current = self._panes.index(self._active) if self._active in self._panes else 0
        self.reveal(self._panes[(current + step) % len(self._panes)])

    def focus_index(self, index: int) -> None:
        if 0 <= index < len(self._panes):
            self.reveal(self._panes[index])

    def reveal(self, pane: TerminalPane) -> None:
        """Focus a pane, bringing it into view first if one is expanded.

        Switching panes while expanded moves the expansion rather than dropping
        out of it: focusing a hidden pane would otherwise put the keyboard
        somewhere the user cannot see.
        """
        if self._zoomed is not None and self._zoomed is not pane:
            self._set_zoom(pane)
        pane.focus_terminal()

    def focus_active(self) -> None:
        target = self._active if self._active in self._panes else None
        if target is None and self._panes:
            target = self._panes[0]
        if target is not None:
            target.focus_terminal()

    # -- expand ----------------------------------------------------------------

    def toggle_zoom(self, pane: TerminalPane) -> None:
        if pane not in self._panes:
            return
        self._set_zoom(None if self._zoomed is pane else pane)
        pane.focus_terminal()

    def toggle_zoom_active(self) -> None:
        # An expanded pane is the only one on screen, so the shortcut belongs to
        # it whatever the focus bookkeeping currently says.
        pane = self._zoomed or self._active or (self._panes[0] if self._panes else None)
        if pane is not None:
            self.toggle_zoom(pane)

    def _set_zoom(self, pane: Optional[TerminalPane]) -> None:
        if pane is self._zoomed:
            return

        if pane is not None and len(self._panes) < 2:
            self.notice.emit("Only one pane -- nothing to expand")
            return

        self._zoomed = pane
        self._relayout()
        self.notice.emit(
            f"Expanded pane {pane.index + 1}" if pane is not None
            else "Restored layout"
        )

    # -- settings ------------------------------------------------------------

    def set_name(self, name: str) -> None:
        name = name.strip()
        if name and name != self.name:
            self.name = name
            self.changed.emit()

    def set_layout_mode(self, mode: str) -> None:
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        self._relayout()

    def set_font_size(self, size: int) -> None:
        self._font_size = size
        for pane in self._panes:
            pane.set_font_size(size)

    def set_shell(self, shell: str) -> None:
        self._shell = shell

    def set_max_panes(self, limit: int) -> None:
        """Update the plan cap on panes (e.g. after the account plan resolves).
        Never removes panes that already exist."""
        self._max_panes = max(1, min(MAX_PANES, int(limit)))

    @property
    def max_panes(self) -> int:
        return self._max_panes

    # -- status ------------------------------------------------------------

    def poll(self) -> None:
        for pane in self._panes:
            pane.poll()

    def running_count(self) -> int:
        return sum(1 for pane in self._panes if pane.is_alive())

    def any_alive(self) -> bool:
        return any(pane.is_alive() for pane in self._panes)

    def is_busy(self) -> bool:
        """True while any live pane is actively producing output.

        The sidebar reads this as "an agent is working in this workspace"
        and glows the workspace's activity dot while it holds.
        """
        return any(pane.is_alive() and pane.is_busy() for pane in self._panes)

    def shutdown(self) -> None:
        for pane in self._panes:
            pane.close_session()

    # -- layout ------------------------------------------------------------

    def _relayout(self) -> None:
        """Rebuild the splitter tree around the existing panes.

        Panes are detached first so tearing down the old splitters cannot take
        their terminals with them -- the shells keep running throughout.
        """
        if self._zoomed is not None and self._zoomed not in self._panes:
            self._zoomed = None

        for position, pane in enumerate(self._panes):
            pane.set_index(position)
            pane.setParent(None)
            pane.set_expanded(pane is self._zoomed)

        if self._root is not None:
            self._body.removeWidget(self._root)
            self._root.setParent(None)
            self._root.deleteLater()
            self._root = None

        self._root = self._build_tree()
        self._body.addWidget(self._root)

        # Adding a freshly built widget to a layout does not show it: a new child
        # widget starts hidden, and Qt only cascades visibility downwards from an
        # explicit show(). Without this, every relayout after the first (add a
        # pane, close a pane, change the layout mode, expand a pane) would leave
        # a blank workspace behind, with the shells running invisibly.
        self._root.show()

        # Only now that every pane is parented again -- into the tree, or to the
        # workspace if it is hidden behind an expanded one -- can visibility be
        # set safely. Doing it while a pane is parentless would promote it to a
        # stray top-level window for the instant before it is reparented.
        for pane in self._panes:
            pane.setVisible(self._zoomed is None or pane is self._zoomed)

        self.changed.emit()

    def _build_tree(self) -> QWidget:
        count = len(self._panes)
        if count == 0:
            return QWidget(self)

        if self._zoomed is not None:
            # The hidden panes keep their shells and their screens; they are
            # simply not in the tree. Parenting them to the workspace stops Qt
            # from promoting them to stray top-level windows. _relayout hides
            # them once this tree is in place.
            for pane in self._panes:
                if pane is not self._zoomed:
                    pane.setParent(self)
            return self._splitter(Qt.Horizontal, [self._zoomed])

        # Always return a splitter, never a bare pane: _relayout deletes the old
        # root, and a pane must outlive that so its shell keeps running.
        if self._layout_mode == LAYOUT_COLUMNS:
            return self._splitter(Qt.Horizontal, self._panes)
        if self._layout_mode == LAYOUT_ROWS:
            return self._splitter(Qt.Vertical, self._panes)

        # Grid: a column of rows, each row a splitter of panes. Near-square is
        # what people expect from "grid", and it keeps every pane usable.
        _, cols = grid_dims(count)
        rows: list[list[TerminalPane]] = [
            self._panes[i : i + cols] for i in range(0, count, cols)
        ]

        if len(rows) == 1:
            return self._splitter(Qt.Horizontal, rows[0])

        outer = QSplitter(Qt.Vertical, self)
        outer.setChildrenCollapsible(False)
        outer.setHandleWidth(8)
        for row in rows:
            outer.addWidget(
                row[0] if len(row) == 1 else self._splitter(Qt.Horizontal, row)
            )
        outer.setSizes([10_000] * len(rows))
        self._style_splitter(outer)
        return outer

    def _splitter(
        self, orientation: Qt.Orientation, panes: list[TerminalPane]
    ) -> QSplitter:
        splitter = QSplitter(orientation, self)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        for pane in panes:
            splitter.addWidget(pane)
        # Equal weights; QSplitter divides proportionally, so any large equal
        # number gives evenly sized panes regardless of the window size.
        splitter.setSizes([10_000] * len(panes))
        self._style_splitter(splitter)
        return splitter

    @staticmethod
    def _style_splitter(splitter: QSplitter) -> None:
        splitter.setStyleSheet(
            f"""
            QSplitter::handle {{ background: {theme.color('splitter')}; }}
            QSplitter::handle:hover {{ background: {theme.color('accent')}; }}
            """
        )

    def apply_theme(self) -> None:
        """Fan a theme change out to every pane and splitter handle."""
        for pane in self._panes:
            pane.apply_theme()
        for splitter in self.findChildren(QSplitter):
            self._style_splitter(splitter)
