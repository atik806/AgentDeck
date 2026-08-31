"""The one window: a workspace sidebar, the terminal area, and the toolbar.

Terminals live in :class:`Workspace` widgets (see ``workspace.py``). This window
owns a list of them, shows one at a time in a ``QStackedWidget``, and lets the
sidebar switch between them -- every hidden workspace keeps its shells running.
Toolbar actions and keyboard shortcuts are routed to the active workspace.

The ``_panes`` / ``_relayout`` / ``_zoomed`` names that older callers and the
test suite reach for are kept as thin proxies onto the active workspace.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QIcon,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import entitlements
import theme
from account import AccountController
from account_dialog import AccountDialog
from agents import pretrust_folder, resolve_agent
from navbar import AccountChip, HelpButton, gear_icon, theme_icon
from new_workspace_dialog import NewWorkspaceDialog
from plugins_panel import PluginsPanel
from settings_dialog import SettingsDialog
from config import save_config
from pty_backend import DEFAULT_SHELL, available_shells
from vt_screen import DEFAULT_SCROLLBACK
from workspace import (  # noqa: F401 - _EXPAND_GLYPH/_RESTORE_GLYPH re-exported for tests
    LAYOUT_COLUMNS,
    LAYOUT_GRID,
    LAYOUT_ROWS,
    MAX_PANES,
    _EXPAND_GLYPH,
    _RESTORE_GLYPH,
    TerminalPane,
    Workspace,
)
from workspace_sidebar import WorkspaceSidebar
from voice_engine import VoiceEngine
from voice_overlay import VoiceOverlay, mic_icon
from updater import UpdateController
from version import __version__

__all__ = ["TerminalPanel", "TerminalPane", "Workspace"]

#: The AgentDeck mark, shipped beside this file (see assets/).
_ASSET_ICON = Path(__file__).resolve().parent / "assets" / "icon.ico"

#: Cycled as workspaces are created so each gets a distinct swatch colour.
#: Catppuccin accents -- blue, green, mauve, peach, pink, teal.
_WS_ACCENTS = ["#89b4fa", "#a6e3a1", "#cba6f7", "#fab387", "#f5c2e7", "#94e2d5"]

#: Gap kept between the voice overlay and the terminal-area edges when it is
#: auto-placed or clamped back into view.
_OVERLAY_MARGIN = 12


class TerminalPanel(QMainWindow):
    """All terminals, one window, grouped into switchable workspaces."""

    def __init__(
        self,
        config: Optional[dict] = None,
        *,
        persist_settings: bool = True,
        startup: Optional[dict] = None,
        account: Optional[AccountController] = None,
    ):
        super().__init__()
        self.config = config or {}
        # The Supabase account surface (sign-in state, cloud settings sync,
        # the toolbar chip). main.py builds one and threads it in; direct
        # construction / tests get a fresh one, which is inert until signed in.
        self.account = account if account is not None else AccountController(
            self.config, self
        )
        # Write toolbar/shortcut changes (layout, shell, font size) back to
        # config.json so they survive a restart. Tests pass False to keep their
        # throwaway values out of the real user config.
        self._persist_settings = persist_settings

        # Settings are global: changing the layout, shell or font in the toolbar
        # applies to every workspace, and a new workspace inherits the current
        # values.
        self._layout_setting = self.config.get("layout", LAYOUT_GRID)
        self._font_size = int(self.config.get("font_size", 11))
        self._scrollback = int(self.config.get("scrollback", DEFAULT_SCROLLBACK))
        self._shell = self.config.get("default_shell", DEFAULT_SHELL)

        # The setup wizard's choices (see setup_wizard.py / main.py). When it is
        # not supplied -- direct construction, tests, --no-wizard -- fall back to
        # saved config, so a configured folder / agent still take effect.
        startup = startup or {}
        self._default_count = max(1, min(MAX_PANES, int(
            startup.get("count", self.config.get("default_count", 4))
        )))
        self._working_folder = str(
            startup.get("folder", self.config.get("working_folder", "")) or ""
        )
        if "agent_command" in startup:
            self._startup_command = str(startup.get("agent_command") or "")
        else:
            self._startup_command = resolve_agent(
                self.config.get("agent", "none"),
                self.config.get("agent_command", ""),
            )

        # Seeds the "new workspace" dialog and is updated to whatever the user
        # last picked there, so the next workspace defaults to the same agent.
        self._last_ws_agent = str(
            startup.get("agent_key") or self.config.get("agent", "none") or "none"
        )
        self._last_ws_agent_custom = str(
            startup.get("agent_custom", "") or self.config.get("agent_command", "")
        )

        self._workspaces: list[Workspace] = []
        self._active_ws: Optional[Workspace] = None
        self._ws_seq = 0
        # True while the sidebar's PLUGINS view is showing instead of a workspace.
        self._plugins_active = False
        # Set before anything can show the window: showEvent reads it.
        self._focus_primed = False

        folder_name = Path(self._working_folder).name if self._working_folder else ""
        self.setWindowTitle(
            f"AgentDeck — {folder_name}" if folder_name else "AgentDeck"
        )
        self.resize(
            int(self.config.get("window_width", 1400)),
            int(self.config.get("window_height", 880)),
        )

        # Resolve light/dark once, then follow further toggles.
        theme.init(self.config)
        theme.manager().changed.connect(self._on_theme_changed)
        self._apply_window_chrome()

        # Built before the toolbar so the toolbar can gate the Update button on
        # updater.enabled (False unless this is a Velopack-installed build).
        self.updater = UpdateController(self)

        self._build_toolbar()
        self._build_body()
        self._build_shortcuts()
        self._build_voice()
        self._wire_updater()
        self._wire_account()

        self._add_workspace(
            pane_count=self._default_count,
            startup_command=self._startup_command or None,
        )

        if self.config.get("start_maximized", False):
            self.showMaximized()

        # Poll for shells that exited on their own (typing `exit`), across every
        # workspace, so headers and badges don't go stale.
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self._refresh_status)
        self._watchdog.start()

    # -- initial focus ---------------------------------------------------------
    #
    # Same two-hook dance as before: showEvent is early enough offscreen, but on
    # a real display native activation delivers its focus-in events afterwards,
    # so the first WindowActivate reasserts.

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._focus_active_ws)
        QTimer.singleShot(0, self._position_overlay)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_overlay()

    def event(self, event) -> bool:
        if event.type() == QEvent.WindowActivate and not self._focus_primed:
            self._focus_primed = True
            QTimer.singleShot(0, self._focus_active_ws)
        return super().event(event)

    def _focus_active_ws(self) -> None:
        if self._active_ws is not None:
            self._active_ws.focus_active()

    # -- chrome --------------------------------------------------------------

    def _toolbar_qss(self) -> str:
        t = theme.color
        return f"""
        QToolBar {{
            background: {t('toolbar_bg')}; border: none;
            border-bottom: 1px solid {t('toolbar_border')};
            padding: 6px 10px; spacing: 6px;
        }}
        QToolBar::separator {{
            background: {t('separator')}; width: 1px; margin: 4px 4px;
        }}
        QToolBar QLabel {{
            color: {t('text_faint')}; font-size: 10px; font-weight: 600;
            padding: 0 3px 0 5px;
        }}
        QToolBar QPushButton, QToolBar QToolButton {{
            color: {t('text')}; background: {t('surface')}; border: 1px solid {t('border')};
            border-radius: 6px; padding: 5px 12px; font-size: 11px;
            min-height: 15px;
        }}
        QToolBar QToolButton {{ padding: 5px 7px; }}
        QToolBar QPushButton:hover, QToolBar QToolButton:hover {{
            background: {t('surface_hover')}; border-color: {t('border_hover')};
        }}
        QToolBar QPushButton:pressed, QToolBar QToolButton:pressed {{
            background: {t('surface_pressed')};
        }}
        QToolBar QToolButton:checked {{
            background: {t('accent_soft_bg')}; border-color: {t('accent')}; color: {t('accent_text')};
        }}
        QToolBar QPushButton#toolbarUpdate {{
            background: {t('accent')}; color: {t('on_accent')};
            border: 1px solid {t('accent')}; font-weight: 700;
        }}
        QToolBar QPushButton#toolbarUpdate:hover {{
            background: {t('accent_hover')}; border-color: {t('accent_hover')};
        }}
        QToolBar QPushButton#toolbarUpdate:pressed {{
            background: {t('accent_hover')};
        }}
        QToolBar QPushButton:focus, QToolBar QToolButton:focus,
        QToolBar QComboBox:focus {{ outline: none; }}
        QToolBar QComboBox {{
            color: {t('text')}; background: {t('surface')}; border: 1px solid {t('border')};
            border-radius: 6px; padding: 4px 8px; font-size: 11px; min-height: 15px;
        }}
        QToolBar QComboBox:hover {{ border-color: {t('border_hover')}; }}
        QToolBar QComboBox::drop-down {{ border: none; width: 16px; }}
        QToolBar QComboBox::down-arrow {{
            image: none; width: 0; height: 0; margin-right: 7px;
            border-left: 4px solid transparent; border-right: 4px solid transparent;
            border-top: 5px solid {t('text_muted')};
        }}
        QComboBox QAbstractItemView {{
            color: {t('text')}; background: {t('menu_bg')}; border: 1px solid {t('menu_border')};
            border-radius: 6px; padding: 3px; outline: none;
            selection-background-color: {t('accent')}; selection-color: {t('on_accent')};
        }}
        """

    def _build_toolbar(self) -> None:
        bar = QToolBar("Main", self)
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setStyleSheet(self._toolbar_qss())
        self.addToolBar(bar)

        # -- AgentDeck brand: the mark + wordmark, so the app is named in-window
        #    and not only in the title bar.
        if _ASSET_ICON.exists():
            mark = QLabel(bar)
            mark.setObjectName("brandMark")
            mark.setPixmap(QIcon(str(_ASSET_ICON)).pixmap(20, 20))
            mark.setStyleSheet("padding: 0 2px 0 4px; background: transparent;")
            bar.addWidget(mark)

        self._wordmark = QLabel(bar)
        self._wordmark.setObjectName("brandName")
        self._wordmark.setTextFormat(Qt.RichText)
        bar.addWidget(self._wordmark)

        self._ver_label = QLabel(f"v{__version__}", bar)
        bar.addWidget(self._ver_label)
        self._style_brand()
        bar.addSeparator()

        self._sidebar_btn = QToolButton(bar)
        self._sidebar_btn.setText("☰")
        self._sidebar_btn.setCheckable(True)
        self._sidebar_btn.setChecked(True)
        self._sidebar_btn.setFixedSize(30, 27)
        self._sidebar_btn.setToolTip("Show/hide the workspace sidebar (Ctrl+B)")
        self._sidebar_btn.clicked.connect(lambda: self._toggle_sidebar())
        bar.addWidget(self._sidebar_btn)

        new_ws_btn = QPushButton("＋ Workspace", bar)
        new_ws_btn.setToolTip("New workspace (Ctrl+Shift+N)")
        new_ws_btn.clicked.connect(lambda: self._new_workspace_interactive())
        bar.addWidget(new_ws_btn)

        self._voice_btn = QToolButton(bar)
        self._voice_btn.setIcon(mic_icon(16, "#d6d6d6"))
        self._voice_btn.setCheckable(True)
        self._voice_btn.setFixedSize(30, 27)
        self._voice_btn.setToolTip(
            "Show/hide the voice input widget  ·  Ctrl+Shift+X starts/stops listening"
        )
        self._voice_btn.clicked.connect(lambda: self._toggle_overlay_visible())
        bar.addWidget(self._voice_btn)

        self._update_btn = QPushButton("Update", bar)
        self._update_btn.setObjectName("toolbarUpdate")
        self._update_btn.setToolTip("Check for a newer version of AgentDeck")
        self._update_btn.clicked.connect(lambda: self.updater.check(silent=False))
        # Only an installed (Velopack) build can update itself.
        self._update_btn.setVisible(self.updater.enabled)
        bar.addWidget(self._update_btn)

        bar.addSeparator()

        bar.addWidget(QLabel("Shell"))
        self._shell_combo = QComboBox(bar)
        self._shell_combo.setMinimumWidth(122)
        self._shell_combo.addItem("Auto-detect", DEFAULT_SHELL)
        for key, label, _argv in available_shells():
            self._shell_combo.addItem(label, key)
        index = self._shell_combo.findData(self._shell)
        if index >= 0:
            self._shell_combo.setCurrentIndex(index)
        self._shell_combo.currentIndexChanged.connect(self._on_shell_changed)
        self._shell_combo.setToolTip("Shell used for panes you open from now on")
        bar.addWidget(self._shell_combo)

        bar.addSeparator()

        add_btn = QPushButton("＋ Pane", bar)
        add_btn.setToolTip("New pane (Ctrl+Shift+T)")
        add_btn.clicked.connect(lambda: self._active_ws and self._active_ws.add_pane())
        bar.addWidget(add_btn)

        close_btn = QPushButton("Close Pane", bar)
        close_btn.setToolTip("Close the active pane (Ctrl+Shift+W)")
        close_btn.clicked.connect(self._close_active_pane)
        bar.addWidget(close_btn)

        bar.addSeparator()

        bar.addWidget(QLabel("Layout"))
        self._layout_combo = QComboBox(bar)
        self._layout_combo.setMinimumWidth(96)
        self._layout_combo.addItem("Grid", LAYOUT_GRID)
        self._layout_combo.addItem("Columns", LAYOUT_COLUMNS)
        self._layout_combo.addItem("Rows", LAYOUT_ROWS)
        index = self._layout_combo.findData(self._layout_setting)
        if index >= 0:
            self._layout_combo.setCurrentIndex(index)
        self._layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        bar.addWidget(self._layout_combo)

        bar.addSeparator()

        bar.addWidget(QLabel("Font"))
        smaller = QPushButton("−", bar)
        smaller.setFixedSize(29, 27)
        smaller.setToolTip("Smaller font (Ctrl+-)")
        smaller.clicked.connect(lambda: self._bump_font(-1))
        bar.addWidget(smaller)

        bigger = QPushButton("+", bar)
        bigger.setFixedSize(29, 27)
        bigger.setToolTip("Larger font (Ctrl++)")
        bigger.clicked.connect(lambda: self._bump_font(1))
        bar.addWidget(bigger)

        # -- right cluster: theme · settings · help · account -----------------
        spacer = QWidget(bar)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        bar.addWidget(spacer)

        self._theme_btn = QToolButton(bar)
        self._theme_btn.setIcon(theme_icon(16))
        self._theme_btn.setFixedSize(30, 27)
        self._theme_btn.setCursor(Qt.PointingHandCursor)
        self._theme_btn.setFocusPolicy(Qt.NoFocus)
        self._theme_btn.clicked.connect(self._toggle_theme)
        bar.addWidget(self._theme_btn)

        self._settings_btn = QToolButton(bar)
        self._settings_btn.setIcon(gear_icon(16))
        self._settings_btn.setFixedSize(30, 27)
        self._settings_btn.setCursor(Qt.PointingHandCursor)
        self._settings_btn.setFocusPolicy(Qt.NoFocus)
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.clicked.connect(self._open_settings)
        bar.addWidget(self._settings_btn)

        self._help_btn = HelpButton(bar)
        self._help_btn.shortcuts_requested.connect(self._show_shortcuts)
        self._help_btn.about_requested.connect(self._show_about)
        bar.addWidget(self._help_btn)

        self._account_chip = AccountChip(self.account, bar)
        self._account_chip.clicked.connect(lambda: self._open_account_dialog())
        bar.addWidget(self._account_chip)

        self._toolbar = bar
        self._refresh_theme_button()

    # -- theme --------------------------------------------------------------

    def _style_brand(self) -> None:
        deck = theme.color("accent")
        name = theme.color("text")
        self._wordmark.setText(f"Agent<span style='color:{deck}'>Deck</span>")
        self._wordmark.setStyleSheet(
            f"QLabel#brandName {{ color: {name}; font-size: 13px; font-weight: 800;"
            " padding: 0 8px 0 3px; background: transparent; }"
        )
        self._ver_label.setStyleSheet(
            f"color: {theme.color('text_faint')}; font-size: 10px;"
            " padding: 0 6px 0 0; background: transparent;"
        )

    def _style_status_bar(self) -> None:
        self.statusBar().setStyleSheet(
            f"color: {theme.color('status_text')};"
            f" background: {theme.color('status_bg')}; font-size: 11px;"
        )

    def _apply_window_chrome(self) -> None:
        self.setStyleSheet(
            f"QMainWindow {{ background: {theme.color('window_bg')}; }}"
        )

    def _refresh_theme_button(self) -> None:
        self._theme_btn.setIcon(theme_icon(16))
        nxt = "light" if theme.mode() == "dark" else "dark"
        self._theme_btn.setToolTip(f"Switch to {nxt} mode")

    def _toggle_theme(self) -> None:
        theme.toggle()  # fires theme.manager().changed -> _on_theme_changed

    def _on_theme_changed(self, mode: str) -> None:
        """Re-skin every surface the panel owns for the new light/dark mode."""
        app = QApplication.instance()
        if app is not None:
            theme.apply_palette(app)

        self._apply_window_chrome()
        self._toolbar.setStyleSheet(self._toolbar_qss())
        self._style_brand()
        self._style_status_bar()
        self._refresh_theme_button()
        self._settings_btn.setIcon(gear_icon(16))
        self._help_btn.apply_theme()
        self._account_chip.refresh()

        self._sidebar.apply_theme()
        self._plugins_panel.apply_theme()
        if getattr(self, "_trial_banner", None) is not None:
            self._trial_banner.apply_theme()
        for workspace in self._workspaces:
            workspace.apply_theme()
        self._refresh_sidebar()

        self.config["theme"] = mode
        self._save_settings()

    def _open_settings(self) -> None:
        before = dict(self.config)
        dialog = SettingsDialog(self.config, self)
        dialog.exec()
        # The dialog writes straight into self.config; apply anything with a
        # live effect (only the theme has one -- splash / wizard / update knobs
        # are read fresh where they're used).
        new_theme = str(self.config.get("theme", "system"))
        if new_theme != before.get("theme"):
            theme.set_mode(theme.init(self.config))
        self._save_settings()

    def _build_body(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # A "trial ends in N days — Upgrade" strip; hidden unless the last 3
        # days of a free trial. Refreshed by _refresh_trial_banner().
        from trial_banner import TrialBanner

        self._trial_banner = TrialBanner(central)
        self._trial_banner.upgrade_requested.connect(self._open_upgrade_url)
        self._trial_banner.dismissed.connect(self._on_trial_banner_dismissed)
        self._trial_banner.setVisible(False)
        outer.addWidget(self._trial_banner)

        body = QWidget(central)
        row = QHBoxLayout(body)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)

        self._sidebar = WorkspaceSidebar(central)
        self._sidebar.selected.connect(self._select_workspace)
        self._sidebar.plugins_selected.connect(self._show_plugins)
        self._sidebar.created.connect(self._new_workspace_interactive)
        self._sidebar.closed.connect(self._close_workspace)
        self._sidebar.renamed.connect(self._rename_workspace)

        # The workspace pages live in _ws_stack; _main_stack flips the whole
        # terminal area over to the PLUGINS panel and back. Keeping _ws_stack a
        # workspace-only stack means its .count() still equals the workspace
        # count (older callers + the test suite rely on that).
        self._ws_stack = QStackedWidget(central)
        self._plugins_panel = PluginsPanel(central)
        self._main_stack = QStackedWidget(central)
        self._main_stack.addWidget(self._ws_stack)
        self._main_stack.addWidget(self._plugins_panel)

        row.addWidget(self._sidebar)
        row.addWidget(self._main_stack, 1)
        outer.addWidget(body, 1)
        self.setCentralWidget(central)

        status = self.statusBar()
        self._status_label = QLabel("", status)
        status.addPermanentWidget(self._status_label)
        self._style_status_bar()

    def _build_shortcuts(self) -> None:
        def add(sequence: str, slot) -> None:
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.setShortcutContext(Qt.ApplicationShortcut)
            action.triggered.connect(slot)
            self.addAction(action)

        add("Ctrl+Shift+T", lambda: self._active_ws and self._active_ws.add_pane())
        add("Ctrl+Shift+D", lambda: self._active_ws and self._active_ws.add_pane())
        add("Ctrl+Shift+W", self._close_active_pane)
        add("Ctrl+Shift+R", self._reset_active_pane)
        add("Ctrl+Shift+E", lambda: self._active_ws and self._active_ws.toggle_zoom_active())
        add("Ctrl+Tab", lambda: self._active_ws and self._active_ws.cycle(1))
        add("Ctrl+Shift+Tab", lambda: self._active_ws and self._active_ws.cycle(-1))
        add("Ctrl++", lambda: self._bump_font(1))
        add("Ctrl+=", lambda: self._bump_font(1))
        add("Ctrl+-", lambda: self._bump_font(-1))
        add("Ctrl+0", lambda: self._set_font(11))

        add("Ctrl+Shift+N", lambda: self._new_workspace_interactive())
        add("Ctrl+Shift+X", self._toggle_voice)
        add("Ctrl+B", lambda: self._toggle_sidebar())
        add("Ctrl+Shift+PgDown", lambda: self._cycle_workspace(1))
        add("Ctrl+Shift+PgUp", lambda: self._cycle_workspace(-1))

        for n in range(1, 10):
            add(
                f"Alt+{n}",
                lambda _=False, i=n - 1: self._active_ws and self._active_ws.focus_index(i),
            )

    # -- workspaces -------------------------------------------------------------

    def _next_ws_name(self) -> str:
        self._ws_seq += 1
        return f"Workspace {self._ws_seq}"

    def _peek_ws_name(self) -> str:
        """The name a new workspace would get, without consuming the counter."""
        return f"Workspace {self._ws_seq + 1}"

    def _new_workspace_interactive(self) -> None:
        """Ask which agent to run, then open a workspace running it.

        Wired to the toolbar's ＋ Workspace, the sidebar's +, and Ctrl+Shift+N.
        The plain :meth:`_add_workspace` stays dialog-free for the startup path
        and the tests.
        """
        if len(self._workspaces) >= entitlements.max_workspaces(self.account.plan):
            self._prompt_upgrade(
                "Multiple workspaces",
                "The Free plan runs one workspace. Pro adds unlimited "
                "workspaces & panes, per-workspace folders and agents.",
            )
            return

        dialog = NewWorkspaceDialog(
            default_name=self._peek_ws_name(),
            default_agent=self._last_ws_agent,
            default_custom=self._last_ws_agent_custom,
            default_count=self._default_count,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        picked = dialog.result_choice() or {}
        self._last_ws_agent = picked.get("agent_key", self._last_ws_agent)
        self._last_ws_agent_custom = picked.get("agent_custom", "")

        command = picked.get("agent_command", "") or ""
        # Same courtesy as the setup wizard: pre-accept Claude Code's
        # "trust this folder?" prompt so it opens straight into the session.
        if command and self.config.get("pretrust_agent_folder", False):
            pretrust_folder(command, self._working_folder)

        name = (picked.get("name") or "").strip() or None
        self._add_workspace(
            name=name,
            pane_count=int(picked.get("count", self._default_count)),
            startup_command=command or None,
        )

    def _add_workspace(
        self,
        name: Optional[str] = None,
        pane_count: Optional[int] = None,
        *,
        startup_command: Optional[str] = None,
    ) -> Workspace:
        accent = _WS_ACCENTS[len(self._workspaces) % len(_WS_ACCENTS)]
        # Advance the counter for every workspace so a later default name never
        # collides with an earlier one, even when some were named by hand.
        auto = self._next_ws_name()
        workspace = Workspace(
            name or auto,
            accent,
            shell=self._shell,
            font_size=self._font_size,
            scrollback=self._scrollback,
            layout_mode=self._layout_setting,
            max_panes=int(entitlements.max_panes(self.account.plan)),
            # Every workspace starts in the chosen folder. The first workspace
            # auto-runs the setup wizard's agent; a later one runs whatever the
            # "new workspace" dialog picked (passed in as startup_command).
            cwd=self._working_folder or None,
            startup_command=startup_command,
        )
        # Lay the panes out before wiring `changed`, so the initial relayout does
        # not fire a sidebar refresh for a workspace not yet in the list.
        workspace.initialize(
            pane_count if pane_count is not None else self._default_count
        )
        workspace.changed.connect(self._on_workspace_changed)
        workspace.active_pane_changed.connect(lambda _pane: self._refresh_status())
        workspace.empty.connect(self._on_workspace_empty)
        workspace.notice.connect(
            lambda message: self.statusBar().showMessage(message, 3000)
        )

        self._workspaces.append(workspace)
        self._ws_stack.addWidget(workspace)
        self._select_workspace(workspace)
        return workspace

    def _show_plugins(self) -> None:
        """Swap the terminal area for the PLUGINS panel (sidebar nav strip)."""
        self._plugins_active = True
        self._main_stack.setCurrentWidget(self._plugins_panel)
        overlay = getattr(self, "_voice_overlay", None)
        if overlay is not None:
            overlay.setVisible(False)
        self._refresh_sidebar()

    def _leave_plugins(self) -> None:
        """Back to the workspaces view. No-op when already there."""
        if not self._plugins_active:
            return
        self._plugins_active = False
        self._main_stack.setCurrentWidget(self._ws_stack)
        overlay = getattr(self, "_voice_overlay", None)
        if overlay is not None and self.config.get("voice_overlay_visible", True):
            overlay.setVisible(True)
            self._position_overlay()

    def _select_workspace(self, workspace: Workspace) -> None:
        if workspace not in self._workspaces:
            return
        self._leave_plugins()
        self._active_ws = workspace
        self._ws_stack.setCurrentWidget(workspace)
        self._refresh_sidebar()
        self._refresh_status()
        QTimer.singleShot(0, workspace.focus_active)
        overlay = getattr(self, "_voice_overlay", None)
        if overlay is not None and overlay.isVisible():
            overlay.raise_()

    def _cycle_workspace(self, step: int) -> None:
        if len(self._workspaces) < 2 or self._active_ws is None:
            return
        current = self._workspaces.index(self._active_ws)
        self._select_workspace(self._workspaces[(current + step) % len(self._workspaces)])

    def _close_workspace(self, workspace: Workspace, force: bool = False) -> None:
        if workspace not in self._workspaces:
            return

        if len(self._workspaces) == 1:
            self.statusBar().showMessage("Can't close the last workspace", 3000)
            return

        if not force and workspace.any_alive():
            reply = QMessageBox.question(
                self,
                "Close workspace",
                f"“{workspace.name}” has {workspace.running_count()} "
                f"running shell(s). Close it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        position = self._workspaces.index(workspace)
        self._workspaces.remove(workspace)
        self._ws_stack.removeWidget(workspace)
        workspace.shutdown()
        workspace.deleteLater()

        if self._active_ws is workspace:
            self._active_ws = None
            self._select_workspace(
                self._workspaces[min(position, len(self._workspaces) - 1)]
            )
        else:
            self._refresh_sidebar()
            self._refresh_status()

    def _on_workspace_empty(self, workspace: Workspace) -> None:
        # The last pane of a workspace was closed. Drop the workspace, unless it
        # is the only one -- then it means "close the app".
        if len(self._workspaces) > 1:
            self._close_workspace(workspace)
        else:
            # close_pane deferred the final pane to us without killing its
            # shell; tear it down here so closeEvent doesn't then re-prompt
            # "shells still running" for a pane the user explicitly closed.
            workspace.shutdown()
            self.close()

    def _rename_workspace(self, workspace: Workspace, name: str) -> None:
        workspace.set_name(name)
        self._refresh_sidebar()
        self._refresh_status()

    def _on_workspace_changed(self) -> None:
        self._refresh_sidebar()
        self._refresh_status()

    def _refresh_sidebar(self) -> None:
        active = None if self._plugins_active else self._active_ws
        self._sidebar.refresh(self._workspaces, active)
        self._sidebar.set_plugins_active(self._plugins_active)

    def _toggle_sidebar(self, show: Optional[bool] = None) -> None:
        if show is None:
            show = not self._sidebar.isVisible()
        self._sidebar.setVisible(show)
        self._sidebar_btn.setChecked(show)

    # -- voice input ---------------------------------------------------------

    def _build_voice(self) -> None:
        """The floating voice-to-text widget and the engine that feeds it."""
        self._voice_engine = VoiceEngine(self.config, self)
        # Parented to the window, not the terminal stack: a plain child of a
        # QStackedWidget loses the stacking fight with the current page. The
        # overlay is kept over the terminal area by set_bounds() instead.
        self._voice_overlay = VoiceOverlay(self)

        self._voice_overlay.toggle_requested.connect(self._voice_engine.toggle)
        self._voice_overlay.moved.connect(self._on_voice_moved)
        self._voice_engine.state.connect(self._on_voice_state)
        self._voice_engine.level.connect(self._voice_overlay.set_level)
        self._voice_engine.transcription.connect(self._on_voice_text)
        self._voice_engine.error.connect(self._on_voice_error)

        if not self._voice_engine.available:
            self._voice_overlay.set_available(False, self._voice_engine.import_error)

        visible = bool(self.config.get("voice_overlay_visible", True))
        self._voice_overlay.setVisible(visible)
        self._voice_btn.setChecked(visible)
        self._position_overlay()
        self._voice_overlay.raise_()

    def _overlay_bounds(self) -> QRect:
        """The terminal-area rectangle, in the window's coordinates."""
        area = self._ws_stack
        top_left = area.mapTo(self, QPoint(0, 0))
        return QRect(top_left.x(), top_left.y(), area.width(), area.height())

    def _position_overlay(self) -> None:
        overlay = getattr(self, "_voice_overlay", None)
        if overlay is None:
            return
        overlay.adjustSize()
        bounds = self._overlay_bounds()
        overlay.set_bounds(bounds)

        ox = self.config.get("voice_overlay_x", -1)
        oy = self.config.get("voice_overlay_y", -1)
        if not isinstance(ox, int) or not isinstance(oy, int) or ox < 0 or oy < 0:
            # Auto-place: bottom-right of the terminal area.
            x = bounds.right() + 1 - overlay.width() - _OVERLAY_MARGIN
            y = bounds.bottom() + 1 - overlay.height() - _OVERLAY_MARGIN
        else:
            # Saved as an offset inside the terminal area, so it survives a
            # toolbar-height or window change.
            x = bounds.left() + ox
            y = bounds.top() + oy
        overlay.move(overlay._clamped(QPoint(x, y)))
        if overlay.isVisible():
            overlay.raise_()

    def _voice_gated(self) -> bool:
        """True (and shows the upsell) when the plan can't use voice input."""
        if entitlements.voice_enabled(self._plan()):
            return False
        self._prompt_upgrade(
            "Voice-to-text input",
            "Dictate straight into a terminal with Ctrl+Shift+X on AgentDeck Pro.",
        )
        return True

    def _toggle_voice(self) -> None:
        """Ctrl+Shift+X -- reveal the widget if hidden, then start/stop listening."""
        if self._voice_gated():
            return
        if not self._voice_overlay.isVisible():
            self._set_overlay_visible(True)
        self._voice_engine.toggle()

    def _set_overlay_visible(self, show: bool) -> None:
        self._voice_overlay.setVisible(show)
        self._voice_btn.setChecked(show)
        if show:
            self._position_overlay()
            self._voice_overlay.raise_()
        self.config["voice_overlay_visible"] = bool(show)
        self._save_settings()

    def _toggle_overlay_visible(self) -> None:
        # Showing the widget at all is a Pro action (it's the voice-input UI).
        if not self._voice_overlay.isVisible() and self._voice_gated():
            self._voice_btn.setChecked(False)
            return
        self._set_overlay_visible(not self._voice_overlay.isVisible())

    def _on_voice_state(self, state: str) -> None:
        self._voice_overlay.set_state(state)
        message = {
            "loading": "Voice: loading speech model…",
            "listening": "Voice: listening",
            "unavailable": "Voice input unavailable (audio dependencies missing)",
        }.get(state)
        if message:
            self.statusBar().showMessage(message, 3000)

    def _on_voice_text(self, text: str) -> None:
        self._voice_overlay.flash_text(text)
        pane = self._active
        if pane is not None:
            pane.view.insert_text(text.strip() + " ")

    def _on_voice_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Voice: {message}", 6000)

    def _on_voice_moved(self, pos: QPoint) -> None:
        # Store where it sits *inside the terminal area*, not in the window.
        bounds = self._overlay_bounds()
        self.config["voice_overlay_x"] = max(0, int(pos.x()) - bounds.left())
        self.config["voice_overlay_y"] = max(0, int(pos.y()) - bounds.top())
        self._save_settings()

    # -- panes ---------------------------------------------------------------

    def _close_active_pane(self) -> None:
        if self._active_ws is not None:
            self._active_ws.close_active_pane()

    def _reset_active_pane(self) -> None:
        """Ctrl+Shift+R -- unstick a pane left on the alternate screen."""
        pane = self._active
        if pane is not None:
            pane.view.reset_screen()
            self.statusBar().showMessage("Terminal screen reset", 2000)

    # -- appearance --------------------------------------------------------

    def _save_settings(self) -> None:
        """Persist the global settings so the next startup keeps them."""
        if not self._persist_settings:
            return
        self.config.update(
            {
                "layout": self._layout_setting,
                "font_size": self._font_size,
                "default_shell": self._shell,
            }
        )
        try:
            save_config(self.config)
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"Couldn't save settings: {exc}", 3000)
        # Mirror the change to the signed-in account (no-op when signed out or
        # sync is off).
        self.account.push_cloud_settings(self.config)

    def _bump_font(self, delta: int) -> None:
        self._set_font(self._font_size + delta)

    def _set_font(self, size: int) -> None:
        size = max(6, min(48, size))
        if size == self._font_size:
            return
        self._font_size = size
        for workspace in self._workspaces:
            workspace.set_font_size(size)
        self._save_settings()
        self.statusBar().showMessage(f"Font size {size}", 2000)

    def _on_shell_changed(self) -> None:
        self._shell = self._shell_combo.currentData()
        for workspace in self._workspaces:
            workspace.set_shell(self._shell)
        self._save_settings()
        self.statusBar().showMessage(
            f"New panes will use {self._shell_combo.currentText()}", 3000
        )

    def _on_layout_changed(self) -> None:
        self._set_layout_mode(self._layout_combo.currentData())

    def _set_layout_mode(self, mode: str) -> None:
        self._layout_setting = mode
        for workspace in self._workspaces:
            workspace.set_layout_mode(mode)
        self._save_settings()

    # -- status ------------------------------------------------------------

    def _refresh_status(self) -> None:
        for workspace in self._workspaces:
            workspace.poll()

        # Light the sidebar's glow dot on any workspace with an agent working.
        self._sidebar.refresh_activity()

        workspace = self._active_ws
        if workspace is None:
            self._status_label.setText("")
            return

        parts = [
            f"{len(self._workspaces)} workspace(s)",
            f"{workspace.pane_count} pane(s)",
            f"{workspace.running_count()} running",
        ]
        if workspace.active_pane is not None:
            parts.append(f"active {workspace.active_pane.index + 1}")
        if workspace.is_zoomed:
            parts.append(f"expanded {workspace._zoomed.index + 1}")
        parts.append(
            "Ctrl+Shift+T pane  ·  Ctrl+Shift+N workspace  ·  "
            "Ctrl+B sidebar  ·  Ctrl+Tab next pane"
        )
        self._status_label.setText("   |   ".join(parts))

    # -- active-workspace proxies ----------------------------------------------
    #
    # Older callers -- and the panel test suite -- reach straight for the pane
    # machinery. Forward those onto whichever workspace is on screen.

    @property
    def _panes(self) -> list[TerminalPane]:
        return self._active_ws.panes if self._active_ws is not None else []

    @property
    def _active(self) -> Optional[TerminalPane]:
        return self._active_ws.active_pane if self._active_ws is not None else None

    @property
    def _zoomed(self) -> Optional[TerminalPane]:
        return self._active_ws._zoomed if self._active_ws is not None else None

    @property
    def _root(self) -> Optional[QWidget]:
        return self._active_ws._root if self._active_ws is not None else None

    @property
    def _layout_mode(self) -> str:
        return self._layout_setting

    @_layout_mode.setter
    def _layout_mode(self, mode: str) -> None:
        self._set_layout_mode(mode)

    def _relayout(self) -> None:
        if self._active_ws is not None:
            self._active_ws._relayout()

    def _add_pane(self, relayout: bool = True) -> Optional[TerminalPane]:
        if self._active_ws is None:
            return None
        return self._active_ws.add_pane(focus=relayout)

    def _close_pane(self, pane: TerminalPane) -> None:
        if self._active_ws is not None:
            self._active_ws.close_pane(pane)

    def _toggle_zoom(self, pane: TerminalPane) -> None:
        if self._active_ws is not None:
            self._active_ws.toggle_zoom(pane)

    def _toggle_zoom_active(self) -> None:
        if self._active_ws is not None:
            self._active_ws.toggle_zoom_active()

    def _cycle(self, step: int) -> None:
        if self._active_ws is not None:
            self._active_ws.cycle(step)

    def _focus_index(self, index: int) -> None:
        if self._active_ws is not None:
            self._active_ws.focus_index(index)

    def _set_active(self, pane: TerminalPane) -> None:
        if self._active_ws is not None:
            self._active_ws.set_active(pane)

    # -- updates -----------------------------------------------------------

    def _wire_updater(self) -> None:
        """Connect the UpdateController to the toolbar button and status bar.

        Dormant when the app is not a Velopack install (updater.enabled False):
        the button is hidden and no signal ever fires.
        """
        self._install_update_glow()
        u = self.updater
        u.available.connect(self._on_update_available)
        u.up_to_date.connect(
            lambda: self.statusBar().showMessage("AgentDeck is up to date", 4000)
        )
        u.progress.connect(
            lambda pct: self.statusBar().showMessage(f"Downloading update… {pct}%", 2000)
        )
        u.ready.connect(self._on_update_ready)
        u.error.connect(
            lambda msg: self.statusBar().showMessage(f"Update: {msg}", 6000)
        )
        u.busy_changed.connect(self._update_btn.setDisabled)

    # -- "an update is waiting" glow -----------------------------------------

    #: The Update button's look while a release is waiting -- a solid red that
    #: overrides the toolbar QSS, so the cue survives even where the animated
    #: halo below can't composite (some remote-desktop / software-render paths).
    _UPDATE_GLOW_QSS = (
        "QPushButton { background: #b32a1f; border: 1px solid #ff6a5c;"
        " color: #ffffff; border-radius: 6px; padding: 5px 12px;"
        " font-size: 11px; font-weight: 700; min-height: 15px; }"
        "QPushButton:hover { background: #c9382b; border-color: #ff8577; }"
        "QPushButton:disabled { background: #6d241c; color: #d9a49d;"
        " border-color: #a3463c; }"
    )

    def _install_update_glow(self) -> None:
        """Make the Update button impossible to miss once a release is waiting.

        Two layers: a solid red restyle of the button (:data:`_UPDATE_GLOW_QSS`)
        that always shows, plus a ``QGraphicsDropShadowEffect`` used as a halo
        (offset 0) whose blur radius pulses on a loop. Both are inert -- effect
        disabled, stylesheet cleared -- until :meth:`_set_update_glow` turns them
        on, which only happens when ``updater.available`` fires.
        """
        self._update_glow = QGraphicsDropShadowEffect(self)
        self._update_glow.setColor(QColor("#ff3b30"))
        self._update_glow.setOffset(0, 0)
        self._update_glow.setBlurRadius(0)
        self._update_glow.setEnabled(False)
        self._update_btn.setGraphicsEffect(self._update_glow)

        self._update_pulse = QPropertyAnimation(self._update_glow, b"blurRadius", self)
        self._update_pulse.setDuration(1500)
        self._update_pulse.setKeyValueAt(0.0, 5)
        self._update_pulse.setKeyValueAt(0.5, 22)   # swell
        self._update_pulse.setKeyValueAt(1.0, 5)    # and back, seamlessly
        self._update_pulse.setEasingCurve(QEasingCurve.InOutSine)
        self._update_pulse.setLoopCount(-1)

    def _set_update_glow(self, on: bool) -> None:
        glow = getattr(self, "_update_glow", None)
        if glow is None:
            return
        if on:
            self._update_btn.setText("Update ●")
            self._update_btn.setStyleSheet(self._UPDATE_GLOW_QSS)
            self._update_btn.setToolTip("A new version of AgentDeck is ready to install")
            glow.setEnabled(True)
            if self._update_pulse.state() != QPropertyAnimation.Running:
                self._update_pulse.start()
        else:
            self._update_pulse.stop()
            glow.setEnabled(False)
            glow.setBlurRadius(0)
            self._update_btn.setStyleSheet("")
            self._update_btn.setText("Update")
            self._update_btn.setToolTip("Check for a newer version of AgentDeck")

    def _on_update_available(self, version: str, notes: str) -> None:
        self._set_update_glow(True)
        box = QMessageBox(self)
        box.setWindowTitle("Update available")
        box.setText(
            f"AgentDeck {version} is available.\nYou have {__version__}."
        )
        if notes:
            box.setDetailedText(notes)
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        box.button(QMessageBox.Ok).setText("Download")
        if box.exec() == QMessageBox.Ok:
            self.updater.download()

    def _on_update_ready(self, version: str) -> None:
        # Downloaded and acknowledged -- the glow has done its job.
        self._set_update_glow(False)
        reply = QMessageBox.question(
            self,
            "Restart to update",
            f"AgentDeck {version} is downloaded. Restart now to apply it?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self.statusBar().showMessage(
                "Update will apply next time you restart AgentDeck", 6000
            )
            return
        # The user already consented; tear the shells down explicitly and skip
        # the closeEvent "shells still running" prompt.
        self._shutdown_all()
        self.updater.apply_and_restart()

    # -- account ----------------------------------------------------------

    def _wire_account(self) -> None:
        a = self.account
        a.signed_in.connect(self._on_account_signed_in)
        a.signed_out.connect(self._on_account_signed_out)
        a.error.connect(self._on_account_error)
        # The plan lands here (fresh sign-in, restored session, or a manual
        # refresh); re-run the Free/Pro gates whenever it does.
        a.profile_ready.connect(lambda _p: self._apply_entitlements())

        # A subscription can lapse while the app is open. Two nudges:
        #  * a slow poll re-fetches the profile every 30 min (also picks up an
        #    admin-side plan change generally, not just expiry), and
        #  * a one-shot timer fires at the exact plan_expires_at moment.
        # Both just re-fetch + re-run the gates; the downgrade is non-destructive
        # (panes already open stay, only new ones past the Free cap are blocked).
        self._plan_watch = QTimer(self)
        self._plan_watch.setInterval(30 * 60 * 1000)
        self._plan_watch.timeout.connect(self._recheck_plan)
        self._plan_watch.start()

        self._plan_expiry_timer = QTimer(self)
        self._plan_expiry_timer.setSingleShot(True)
        self._plan_expiry_timer.timeout.connect(self._recheck_plan)

        # The Free tier is a 7-day trial. This one-shot fires the instant it
        # ends so the "upgrade or quit" gate comes up without waiting for the
        # 30-min poll. main.py already handled "trial over at launch".
        self._trial_timer = QTimer(self)
        self._trial_timer.setSingleShot(True)
        self._trial_timer.timeout.connect(self._recheck_trial)

        # Sync the initial UI state to whatever plan we already know (Free until
        # the first profile_ready).
        self._auto_update_checked = False
        self._apply_entitlements()

    def _recheck_plan(self) -> None:
        """Re-pull the profile (if signed in) and re-apply the Free/Pro gates."""
        acc = self.account
        if acc is not None and getattr(acc, "is_signed_in", False):
            try:
                acc.fetch_profile()  # emits profile_ready -> _apply_entitlements
            except Exception:  # noqa: BLE001 - a failed refresh must not crash the loop
                pass
        self._apply_entitlements()

    def _recheck_trial(self) -> None:
        """Re-pull the profile and re-run the trial gate (timer / 30-min poll)."""
        acc = self.account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return
        try:
            acc.fetch_profile()  # profile_ready -> _apply_entitlements -> gate
        except Exception:  # noqa: BLE001
            pass
        self._apply_entitlements()

    def _open_upgrade_url(self) -> None:
        QDesktopServices.openUrl(QUrl(entitlements.UPGRADE_URL))

    def _on_trial_banner_dismissed(self) -> None:
        import time

        self.config["trial_banner_dismissed_on"] = int(time.time() // 86400)
        try:
            save_config(self.config)
        except Exception:  # noqa: BLE001 - a read-only config dir is not fatal
            pass
        if getattr(self, "_trial_banner", None) is not None:
            self._trial_banner.setVisible(False)

    def _arm_trial_timer(self) -> None:
        """(Re)schedule the one-shot gate for the current trial_ends_at."""
        timer = getattr(self, "_trial_timer", None)
        if timer is None or self.account is None:
            return
        exp = entitlements.trial_deadline(getattr(self.account, "trial_ends_at", None))
        if exp is None:
            timer.stop()
            return
        delta = (exp - datetime.now(timezone.utc)).total_seconds()
        if delta <= 0:
            timer.stop()
            return
        timer.start(max(1000, min(int(delta * 1000) + 2000, 6 * 60 * 60 * 1000)))

    def _enforce_trial_block(self) -> None:
        """Trial over, no active plan: show the upgrade-or-quit gate."""
        if getattr(self, "_trial_block_active", False):
            return
        self._trial_block_active = True
        try:
            from trial_gate import TrialGateDialog

            gate = TrialGateDialog(self.account, self.config, icon=self.windowIcon())
            ok = gate.exec() == QDialog.Accepted and self.account.access_allowed
            gate.deleteLater()
            if ok:
                self._apply_entitlements()
                return
            self._force_quit = True
            self.close()
        finally:
            self._trial_block_active = False

    def _refresh_trial_banner(self) -> None:
        banner = getattr(self, "_trial_banner", None)
        if banner is None:
            return
        acc = self.account
        show = False
        if acc is not None and getattr(acc, "is_signed_in", False):
            left = acc.trial_days_left
            if (
                acc.access_allowed
                and not entitlements.is_pro(acc.raw_plan)
                and left is not None
                and 0 <= left <= 3
            ):
                import time

                today = int(time.time() // 86400)
                if self.config.get("trial_banner_dismissed_on", 0) != today:
                    banner.set_days_left(left)
                    show = True
        banner.setVisible(show)

    def _maybe_trial_last_day_modal(self) -> None:
        if getattr(self, "_trial_modal_shown", False) or not self.isVisible():
            return
        acc = self.account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return
        if not acc.access_allowed or entitlements.is_pro(acc.raw_plan):
            return
        left = acc.trial_days_left
        if left is None or left > 1:
            return
        self._trial_modal_shown = True
        when = "today" if left <= 0 else "tomorrow"
        box = QMessageBox(self)
        box.setWindowTitle("Free trial ending")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>Your AgentDeck free trial ends {when}.</b>")
        box.setInformativeText(
            "Upgrade to Pro to keep using AgentDeck — your workspaces and "
            "settings stay exactly as they are."
        )
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Help)
        box.button(QMessageBox.Ok).setText("Continue")
        box.button(QMessageBox.Help).setText("Upgrade")
        if box.exec() == QMessageBox.Help:
            self._open_upgrade_url()

    def _arm_plan_expiry_timer(self) -> None:
        """(Re)schedule the one-shot timer for the current plan_expires_at."""
        timer = getattr(self, "_plan_expiry_timer", None)
        if timer is None:
            return
        exp = entitlements.plan_expiry(getattr(self.account, "plan_expires_at", None))
        if exp is None:
            timer.stop()
            return
        delta = (exp - datetime.now(timezone.utc)).total_seconds()
        if delta <= 0:
            timer.stop()
            return
        # Cap the wait at 6h so a long-running clock drift still gets re-checked.
        timer.start(max(1000, min(int(delta * 1000) + 2000, 6 * 60 * 60 * 1000)))

    def _plan(self) -> str:
        return self.account.plan if self.account is not None else "free"

    def _apply_entitlements(self) -> None:
        """Fold the current plan's limits into the live UI. Idempotent."""
        # Hard gate: the free trial is over and there's no active plan. Only
        # once the window is up (main.py runs this check at launch); guarded so
        # it never re-enters.
        if (
            self.isVisible()
            and self.account is not None
            and getattr(self.account, "is_signed_in", False)
            and not self.account.access_allowed
            and not getattr(self, "_trial_block_active", False)
        ):
            self._enforce_trial_block()
            return

        plan = self._plan()
        pro = entitlements.is_pro(plan)

        # Panes: raise/lower every workspace's cap. Never removes panes; a Pro
        # user whose first workspace was clamped at 4 gets topped up to their
        # configured count.
        cap = int(entitlements.max_panes(plan))
        for ws in self._workspaces:
            ws.set_max_panes(cap)
        # One-shot: a Pro user whose first workspace was clamped to 4 while the
        # plan was still resolving gets topped up to their configured count.
        # Only right after launch (before they've touched anything) and never
        # while a pane is zoomed.
        if pro and not getattr(self, "_entitlements_topped_up", False):
            self._entitlements_topped_up = True
            first = self._workspaces[0] if self._workspaces else None
            want = min(cap, self._default_count)
            if first is not None and not first.is_zoomed and first.pane_count < want:
                for _ in range(want - first.pane_count):
                    first.add_pane(focus=False)

        # Voice button: still visible for Free (so the feature is discoverable),
        # but its tooltip says it's Pro; the click is gated in _toggle_voice.
        if hasattr(self, "_voice_btn"):
            self._voice_btn.setToolTip(
                "Show/hide the voice input widget  ·  Ctrl+Shift+X starts/stops listening"
                if pro else
                "Voice-to-text input is a Pro feature"
            )

        # Background update check on launch is Pro; Free keeps the manual button.
        if pro:
            self._auto_check_updates()

        # Re-arm the "expire at plan_expires_at" timer for whatever we now know.
        self._arm_plan_expiry_timer()
        # ... and the trial gate + its warning surfaces.
        self._arm_trial_timer()
        self._refresh_trial_banner()
        self._maybe_trial_last_day_modal()

    def _auto_check_updates(self) -> None:
        """One quiet check for a newer release — Pro only, once per run."""
        if getattr(self, "_auto_update_checked", False):
            return
        if not entitlements.auto_update_enabled(self._plan()):
            return
        if not self.config.get("auto_check_updates", True):
            return
        if not getattr(self, "updater", None) or not self.updater.enabled:
            return
        self._auto_update_checked = True
        self.updater.check(silent=True)

    def _prompt_upgrade(self, feature: str, detail: str = "") -> None:
        """Explain a Pro-gated feature and offer to open the pricing page."""
        self.statusBar().showMessage(entitlements.upgrade_hint(feature), 6000)
        box = QMessageBox(self)
        box.setWindowTitle("Pro feature")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>{feature}</b> is part of AgentDeck Pro.")
        if detail:
            box.setInformativeText(detail)
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Help)
        box.button(QMessageBox.Ok).setText("Not now")
        box.button(QMessageBox.Help).setText("See Pro")
        if box.exec() == QMessageBox.Help:
            QDesktopServices.openUrl(QUrl(entitlements.UPGRADE_URL))

    # Benign account-error messages that aren't worth a crash report: the user
    # backing out, or a session that just needs re-authing.
    _QUIET_ACCOUNT_ERRORS = ("cancel", "session expired", "not signed in", "sign in again")

    def _on_account_error(self, msg: str) -> None:
        self.statusBar().showMessage(f"Account: {msg}", 6000)
        low = (msg or "").lower()
        if not any(s in low for s in self._QUIET_ACCOUNT_ERRORS):
            self.account.report_error(
                msg, kind="error", phase="runtime", context={"source": "account"}
            )

    def _on_account_signed_out(self) -> None:
        """A signed-in account is required -- ask for one again, or quit.

        Fires on an explicit Sign out and on a dead refresh token. AgentDeck is
        never usable unauthenticated, so we put the sign-in window back up; if
        the user dismisses it without signing in, the window closes.
        """
        self.statusBar().showMessage("Signed out of AgentDeck", 4000)
        self._require_login()

    def _require_login(self) -> None:
        if getattr(self, "_relogin_active", False):
            return
        self._relogin_active = True
        try:
            from login_window import LoginWindow

            login = LoginWindow(self.account, self.config, icon=self.windowIcon())
            accepted = login.exec() == QDialog.Accepted and self.account.is_signed_in
            login.deleteLater()
            if accepted:
                self._account_chip.refresh()
                return
            # Dismissed without signing in -> the app can't be used.
            self._force_quit = True
            self.close()
        finally:
            self._relogin_active = False

    def _on_account_signed_in(self, _user: dict) -> None:
        self.statusBar().showMessage(
            f"Signed in as {self.account.email or self.account.display_name}", 4000
        )
        # Pull this account's cloud settings and merge the ones that reached us.
        try:
            cloud = self.account.pull_cloud_settings()
        except Exception:  # noqa: BLE001
            cloud = None
        if cloud:
            self.config.update(cloud)
            try:
                save_config(self.config)
            except (OSError, ValueError):
                pass

    def _open_account_dialog(self) -> None:
        AccountDialog(self.account, self.config, self).exec()
        self._account_chip.refresh()

    def _show_shortcuts(self) -> None:
        rows = [
            ("Ctrl+Shift+T", "New terminal pane"),
            ("Ctrl+Shift+W", "Close the active pane"),
            ("Ctrl+Shift+E", "Expand / restore the active pane"),
            ("Ctrl+Shift+R", "Reset a stuck full-screen pane"),
            ("Ctrl+Tab  /  Ctrl+Shift+Tab", "Next / previous pane"),
            ("Alt+1 … Alt+9", "Jump to pane N"),
            ("Ctrl+Shift+N", "New workspace"),
            ("Ctrl+Shift+PgUp / PgDn", "Previous / next workspace"),
            ("Ctrl+B", "Show / hide the workspace sidebar"),
            ("Ctrl+Shift+X", "Start / stop voice input"),
            ("Ctrl+ +  /  Ctrl+ -  /  Ctrl+0", "Font larger / smaller / reset"),
        ]
        body = "\n".join(f"{k:<28}{v}" for k, v in rows)
        box = QMessageBox(self)
        box.setWindowTitle("Keyboard shortcuts")
        box.setText("<pre style='font-family:Cascadia Mono,Consolas'>" + body + "</pre>")
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About AgentDeck",
            f"<b>AgentDeck</b> v{__version__}<br><br>"
            "Every terminal, every agent, one deck.<br><br>"
            "<a href='https://github.com/atik806/AgentDeck'>"
            "github.com/atik806/AgentDeck</a>",
        )

    # -- teardown ----------------------------------------------------------

    def _shutdown_all(self) -> None:
        self._watchdog.stop()
        if getattr(self, "_plan_watch", None) is not None:
            self._plan_watch.stop()
        if getattr(self, "_plan_expiry_timer", None) is not None:
            self._plan_expiry_timer.stop()
        if getattr(self, "_trial_timer", None) is not None:
            self._trial_timer.stop()
        self._set_update_glow(False)
        self._voice_engine.shutdown()
        self.account.shutdown()
        for workspace in self._workspaces:
            workspace.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802
        running = sum(workspace.running_count() for workspace in self._workspaces)
        if running and not getattr(self, "_force_quit", False):
            reply = QMessageBox.question(
                self,
                "Close AgentDeck",
                f"{running} shell(s) are still running. Close them all?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return

        self._shutdown_all()
        event.accept()
