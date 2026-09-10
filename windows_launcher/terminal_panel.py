"""The one window: a workspace sidebar, the terminal area, and the toolbar.

Terminals live in :class:`Workspace` widgets (see ``workspace.py``). This window
owns a list of them, shows one at a time in a ``QStackedWidget``, and lets the
sidebar switch between them -- every hidden workspace keeps its shells running.
Toolbar actions and keyboard shortcuts are routed to the active workspace.

The ``_panes`` / ``_relayout`` / ``_zoomed`` names that older callers and the
test suite reach for are kept as thin proxies onto the active workspace.
"""

from __future__ import annotations

import hashlib
import sys
import time
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
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import entitlements
import git_worktree
import pane_state
import perf
import theme
from notifications import AttentionNotifier
from account import AccountController
from github_controller import GitHubController
from vercel_controller import VercelController
from jira_controller import JiraController
from gitlab_controller import GitLabController
from linear_controller import LinearController
from account_dialog import AccountDialog
from agents import agent_label, installed_agent_keys, pretrust_folder, resolve_agent
from navbar import AccountChip, HelpButton, gear_icon, theme_icon
from new_workspace_dialog import NewWorkspaceDialog
from notes_panel import NotesPanel
from plugins_panel import PluginsPanel
from routine_scheduler import RoutineScheduler
from routines_panel import RoutinesPanel
from routines_store import RoutinesStore
from skills_panel import SkillsPanel
from skills_store import SkillsStore
import skills_sync
from settings_dialog import SettingsPanel
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
from workspaces_store import SessionSnapshot, WorkspaceSnapshot, WorkspacesStore
from worktree_panel import WorktreePanel
from worktree_store import (
    WorktreeStore,
    branch_name,
    default_worktrees_root,
    repo_key_for,
    worktree_dir,
)
import voice_commands
import voice_postprocess
from voice_engine import VoiceEngine
from voice_overlay import VoiceOverlay, mic_icon
from update_progress import UpdateProgressDialog
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


def _sha_of_triple(triple) -> str:
    """SHA-256 of a ``(name, description, body)`` skill tuple -- used to tell
    when an "Improve with agent" run has actually changed the file on disk."""
    name, description, body = triple
    payload = f"{(name or '').strip()}\x00{(description or '').strip()}\x00{(body or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        # The GitHub plugin surface (connect state, token vault, MCP wiring for
        # the agent in a pane). Inert until the user connects on the Plugins page.
        self.github = GitHubController(self.account, self.config, self)
        # The Vercel plugin surface -- thin: it just drops a tokenless MCP server
        # entry into Claude Code's config; the user authorises with /mcp in a pane.
        self.vercel = VercelController(self.account, self.config, self)
        # The Jira plugin surface -- thin, same shape (Atlassian Rovo MCP).
        self.jira = JiraController(self.account, self.config, self)
        # GitLab + Linear plugin surfaces -- thin, same shape (hosted OAuth MCP).
        self.gitlab = GitLabController(self.account, self.config, self)
        self.linear = LinearController(self.account, self.config, self)
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
        self._startup = startup
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
        # True while a nav view (PLUGINS / NOTES / ROUTINES / SETTINGS) is
        # showing instead of a workspace.
        self._plugins_active = False
        self._notes_active = False
        self._routines_active = False
        self._skills_active = False
        self._settings_active = False
        self._worktrees_active = False
        self._routine_scheduler = RoutineScheduler()
        # Files an "Improve with agent" run is watching for the agent's edits:
        # {abs path -> (skill_id, last_sha, agent_key)}. Polled by _refresh_status.
        self._skill_watch: dict[str, tuple] = {}
        self._skills_materialized_once = False
        # Worktree ids created for the workspace currently being built, then
        # bound to their panes once the panes exist (see _add_workspace).
        self._pending_worktree_ids: list[str] = []
        # detect_repo() result cached per folder -- called on every new-workspace.
        self._repo_info_cache: dict[str, object] = {}
        self._last_worktree_poll = 0.0
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

        # Resolve light/dark + colour scheme once, then follow further changes.
        theme.init(self.config)
        theme.manager().changed.connect(self._on_theme_changed)
        # App-wide terminal font family, before the first pane is built.
        import terminal_view
        terminal_view.set_font_family(self.config.get("font_family", ""))
        self._apply_window_chrome()

        # Built before the toolbar so the toolbar can gate the Update button on
        # updater.enabled (False unless this is a Velopack-installed build).
        self.updater = UpdateController(
            self, channel=str(self.config.get("update_channel", "stable") or "stable")
        )
        self._update_dialog: Optional[UpdateProgressDialog] = None

        # Session-restore bookkeeping -- initialised before _wire_account()
        # because a synchronously-resolved account fires _apply_entitlements
        # (hence _resume_pending_restore) straight from _wire_account, before
        # the restore block below has run. Empty here == nothing to resume.
        self._pending_restore: list = []
        self._restore_active_index = 0

        self._build_toolbar()
        self._build_body()
        self._build_shortcuts()
        self._build_voice()
        self._wire_updater()
        self._wire_account()
        self._build_skills_cloud()

        # Perf HUD -- off by default, toggled with Ctrl+Shift+P. Parented to
        # the window so it floats over every pane; near-free while hidden.
        self._perf_hud = perf.PerfHUD(self)

        # Persist the workspace list so a restart reopens it. Session-only until
        # this feature; see workspaces_store.py.
        self._workspaces_store = WorkspacesStore()
        self._session_save_timer = QTimer(self)
        self._session_save_timer.setSingleShot(True)
        self._session_save_timer.setInterval(500)
        self._session_save_timer.timeout.connect(self._do_persist_session)
        # _pending_restore / _restore_active_index initialised above, before
        # _wire_account(). _restore_session() fills _pending_restore with the
        # 2nd..Nth workspace; _apply_entitlements opens them once the plan is
        # known (async in the real app).

        if not self._restore_session():
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

        # Bring the isolated-worktree store back in step with what's on disk
        # (a previous session may have crashed mid-cleanup).
        self._reconcile_worktrees_on_startup()

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
        hud = getattr(self, "_perf_hud", None)
        if hud is not None and hud.is_active():
            hud.reposition()

    def _toggle_perf_hud(self) -> None:
        """Ctrl+Shift+P -- show/hide the performance HUD (frame/parse/backlog)."""
        hud = getattr(self, "_perf_hud", None)
        if hud is None:
            return
        active = not hud.is_active()
        hud.set_active(active)
        self.statusBar().showMessage(
            "Perf HUD on" if active else "Perf HUD off", 2000
        )

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
        self._voice_btn.setIcon(mic_icon(16))
        self._voice_btn.setCheckable(True)
        self._voice_btn.setFixedSize(30, 27)
        self._voice_btn.setToolTip(
            "Show/hide the voice input widget  ·  Ctrl+Shift+X starts/stops listening"
        )
        self._voice_btn.clicked.connect(lambda: self._toggle_overlay_visible())
        bar.addWidget(self._voice_btn)

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

        # Font size lives in Settings now (gear button) plus the Ctrl+± / Ctrl+0
        # shortcuts wired in _build_shortcuts.

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
        self._settings_btn.clicked.connect(self._show_settings)
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
        new_mode = theme.toggle()  # fires theme.manager().changed -> _on_theme_changed
        # The toolbar toggle is an explicit choice -- pin it (overrides "system").
        self.config["theme"] = new_mode
        self._save_settings()

    def _on_theme_changed(self, mode: str) -> None:
        """Re-skin every surface the panel owns for the new light/dark mode
        (also fired for a colour-scheme change -- same repaint)."""
        app = QApplication.instance()
        if app is not None:
            theme.apply_palette(app)

        self._apply_window_chrome()
        self._toolbar.setStyleSheet(self._toolbar_qss())
        self._style_brand()
        self._style_status_bar()
        self._refresh_theme_button()
        self._refresh_settings_icon()
        self._voice_btn.setIcon(mic_icon(16))
        self._help_btn.apply_theme()
        self._account_chip.refresh()

        self._sidebar.apply_theme()
        self._plugins_panel.apply_theme()
        self._notes_panel.apply_theme()
        self._routines_panel.apply_theme()
        self._skills_panel.apply_theme()
        self._worktree_panel.apply_theme()
        self._settings_panel.apply_theme()
        if getattr(self, "_trial_banner", None) is not None:
            self._trial_banner.apply_theme()
        for workspace in self._workspaces:
            workspace.apply_theme()
        self._refresh_sidebar()

        # Only sync an already-concrete choice. "system" is left alone (its
        # resolved mode isn't the user's stored preference), and a colour-scheme
        # change persists its own key via the Settings panel, not here.
        if str(self.config.get("theme", "")).strip().lower() in ("light", "dark"):
            self.config["theme"] = mode
            self._save_settings()

    def _show_settings(self) -> None:
        """Gear button -- swap the terminal area for the SETTINGS panel.

        A page of the app (like Plugins / Notes), not a separate popup window:
        the panel is built once in ``_build_body`` and writes straight into
        ``self.config`` as the user changes anything, live -- there is no
        "Done" step to apply on. Theme and font size take effect immediately
        via signals; voice settings are debounced onto the already-built
        engine by :meth:`_on_settings_voice_changed`.
        """
        self._notes_panel.flush()
        self._routines_panel.flush()
        self._skills_panel.flush()
        self._notes_active = False
        self._plugins_active = False
        self._routines_active = False
        self._skills_active = False
        self._worktrees_active = False
        self._settings_active = True
        self._settings_panel.reset_to_first_page()
        self._main_stack.setCurrentWidget(self._settings_panel)
        self._hide_voice_overlay()
        self._refresh_sidebar()

    def _leave_settings(self) -> None:
        """Back to the workspaces view. No-op when already there."""
        if not self._settings_active:
            return
        self._settings_active = False
        self._main_stack.setCurrentWidget(self._ws_stack)
        self._restore_voice_overlay()

    def _on_settings_theme_changed(self, _mode: str) -> None:
        theme.set_mode(theme.init(self.config))

    def _on_settings_scheme_changed(self, key: str) -> None:
        """Colour-scheme dropdown -> repaint every surface (theme.set_scheme
        fires theme.manager().changed, which _on_theme_changed handles)."""
        theme.set_scheme(key)

    def _on_settings_font_family_changed(self, family: str) -> None:
        """Terminal-font dropdown -> re-resolve the font in every open pane."""
        import terminal_view
        terminal_view.set_font_family(family)
        for workspace in self._workspaces:
            workspace.reapply_font()

    def _on_settings_voice_changed(self) -> None:
        """A voice_* setting changed in the (always-live) Settings panel.

        Debounced: rebuilding the pipeline / rebinding the hotkey on every
        single keystroke or checkbox click (rather than once, the way the old
        modal dialog applied everything together when "Done" was clicked)
        would mean a rapid run of toggles each briefly interrupts a live
        listen. 500 ms of quiet before it actually applies.
        """
        timer = getattr(self, "_voice_settings_debounce", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._apply_voice_settings)
            self._voice_settings_debounce = timer
        timer.start(500)

    def _apply_voice_settings(self) -> None:
        """Push the current voice_* config into the (already-built) engine,
        global hotkey, and overlay."""
        engine = getattr(self, "_voice_engine", None)
        if engine is None:
            return
        engine.apply_config(self.config)
        self._rebind_global_hotkey()
        enabled = bool(self.config.get("voice_input_enabled", True))
        if self._voice_btn.isEnabled() != enabled:
            self._voice_btn.setEnabled(enabled)
            if not enabled:
                if engine.is_listening:
                    engine.stop_listening()
                self._voice_overlay.setVisible(False)
                self._voice_btn.setChecked(False)
            elif self.config.get("voice_overlay_visible", True):
                self._set_overlay_visible(True)

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
        self._sidebar.notes_selected.connect(self._show_notes)
        self._sidebar.routines_selected.connect(self._show_routines)
        self._sidebar.skills_selected.connect(self._show_skills)
        self._sidebar.worktrees_selected.connect(self._show_worktrees)
        self._sidebar.created.connect(self._new_workspace_interactive)
        self._sidebar.closed.connect(self._close_workspace)
        self._sidebar.renamed.connect(self._rename_workspace)
        self._sidebar.reordered.connect(self._reorder_workspaces)

        # The workspace pages live in _ws_stack; _main_stack flips the whole
        # terminal area over to the PLUGINS panel and back. Keeping _ws_stack a
        # workspace-only stack means its .count() still equals the workspace
        # count (older callers + the test suite rely on that).
        self._ws_stack = QStackedWidget(central)
        self._plugins_panel = PluginsPanel(
            central, github=self.github, vercel=self.vercel, jira=self.jira,
            gitlab=self.gitlab, linear=self.linear,
            account=self.account, config=self.config,
            agents_provider=lambda: self.github._target_agent_keys(self._startup_command),
        )
        self._plugins_panel.review_ready.connect(self._start_github_review)
        self._notes_panel = NotesPanel(central, config=self.config)
        self._notes_panel.send_to_terminal.connect(self._send_note_to_terminal)
        self._routines_store = RoutinesStore()
        self._routines_panel = RoutinesPanel(
            central, store=self._routines_store, config=self.config,
            workspaces_provider=lambda: [w.name for w in self._workspaces],
        )
        self._routines_panel.run_now.connect(self._run_routine_now)
        self._skills_store = SkillsStore()
        self._skills_panel = SkillsPanel(
            central, store=self._skills_store, config=self.config,
        )
        self._skills_panel.improve_requested.connect(self._improve_skill_with_agent)
        self._skills_panel.changed.connect(self._on_skills_changed)
        self._worktree_store = WorktreeStore()
        self._worktree_panel = WorktreePanel(
            central,
            store=self._worktree_store,
            config=self.config,
            repo_provider=self._active_repo_info,
            github_connected=lambda: bool(
                getattr(self, "github", None) and self.github.is_connected
            ),
            merge_enabled=lambda: entitlements.worktrees_enabled(self._plan()),
        )
        self._worktree_panel.merge_requested.connect(self._merge_worktree)
        self._worktree_panel.open_pr_requested.connect(self._open_pr_for_worktree)
        self._worktree_panel.discard_requested.connect(self._discard_worktree)
        self._worktree_panel.open_in_pane_requested.connect(self._open_worktree_in_pane)
        self._settings_panel = SettingsPanel(
            self.config, central,
            updater=getattr(self, "updater", None),
            current_version=__version__,
            voice_enabled=entitlements.voice_enabled(self._plan()),
        )
        self._settings_panel.theme_changed.connect(self._on_settings_theme_changed)
        self._settings_panel.scheme_changed.connect(self._on_settings_scheme_changed)
        self._settings_panel.font_family_changed.connect(self._on_settings_font_family_changed)
        self._settings_panel.font_size_changed.connect(self._set_font)
        self._settings_panel.voice_settings_changed.connect(self._on_settings_voice_changed)
        self._settings_panel.notifications_changed.connect(self._configure_notifications)
        self._main_stack = QStackedWidget(central)
        self._main_stack.addWidget(self._ws_stack)
        self._main_stack.addWidget(self._plugins_panel)
        self._main_stack.addWidget(self._notes_panel)
        self._main_stack.addWidget(self._routines_panel)
        self._main_stack.addWidget(self._skills_panel)
        self._main_stack.addWidget(self._worktree_panel)
        self._main_stack.addWidget(self._settings_panel)

        row.addWidget(self._sidebar)
        row.addWidget(self._main_stack, 1)
        outer.addWidget(body, 1)
        self.setCentralWidget(central)

        # "A terminal needs you" desktop notifications. Owned here, driven from
        # _refresh_status; configure() is (re)called from _apply_entitlements
        # and the settings-changed path. Silent until then.
        self._notifier = AttentionNotifier(self, icon=self.windowIcon())
        self._notifier.set_main_window(self)
        self._notifier.activated.connect(self._focus_pane_by_id)
        # {pane_id -> last attention state we notified about} -- so a pane that
        # sits in "awaiting_input" for a minute doesn't re-toast every second.
        self._pane_attn_notified: dict[str, str] = {}

        status = self.statusBar()
        self._status_label = QLabel("", status)
        status.addPermanentWidget(self._status_label)
        self._style_status_bar()

    def _build_shortcuts(self) -> None:
        def add(sequence: str, slot) -> QAction:
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.setShortcutContext(Qt.ApplicationShortcut)
            action.triggered.connect(slot)
            self.addAction(action)
            return action

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
        # Kept as the focused-window fallback; disabled by _rebind_global_hotkey
        # while the OS-wide hotkey is registered, so one keypress never reaches
        # both handlers and cancels itself out.
        self._voice_action = add("Ctrl+Shift+X", self._toggle_voice)
        add("Ctrl+Shift+P", self._toggle_perf_hud)
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

        folder = self._working_folder or ""
        pro = entitlements.worktrees_enabled(self._plan())
        repo_ok = bool(folder) and git_worktree.git_available() and git_worktree.is_git_repo(folder)
        allow_isolation = pro and repo_ok
        if allow_isolation:
            isolation_reason = ""
        elif not pro:
            isolation_reason = "Isolated worktrees are a Pro feature."
        elif not git_worktree.git_available():
            isolation_reason = "git isn't installed, so worktrees are unavailable."
        else:
            isolation_reason = "This workspace's folder isn't a git repository."

        dialog = NewWorkspaceDialog(
            default_name=self._peek_ws_name(),
            default_agent=self._last_ws_agent,
            default_custom=self._last_ws_agent_custom,
            default_count=self._default_count,
            allow_isolation=allow_isolation,
            isolation_reason=isolation_reason,
            default_isolate=bool(self.config.get("worktree_isolate_default", False)),
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

        isolate = bool(picked.get("isolate_panes"))
        if isolate:
            self.config["worktree_isolate_default"] = True
        name = (picked.get("name") or "").strip() or None
        self._add_workspace(
            name=name,
            pane_count=int(picked.get("count", self._default_count)),
            startup_command=command or None,
            isolate_panes=isolate,
        )

    def _add_workspace(
        self,
        name: Optional[str] = None,
        pane_count: Optional[int] = None,
        *,
        startup_command: Optional[str] = None,
        isolate_panes: bool = False,
    ) -> Workspace:
        accent = _WS_ACCENTS[len(self._workspaces) % len(_WS_ACCENTS)]
        # Wire the connected plugins' MCP servers into the agents' configs *before*
        # this workspace's panes fire their startup command, so an agent comes up
        # with the tools ready. The servers are user-scope, so once per session is
        # enough -- the controllers also (re)wire on __init__ and on connect.
        if not getattr(self, "_plugins_wired_this_session", False):
            self._plugins_wired_this_session = True
            self._wire_github_for(self._working_folder, startup_command)
            self._wire_vercel_for(self._working_folder, startup_command)
            self._wire_jira_for(self._working_folder, startup_command)
            self._wire_gitlab_for(self._working_folder, startup_command)
            self._wire_linear_for(self._working_folder, startup_command)
        # Advance the counter for every workspace so a later default name never
        # collides with an earlier one, even when some were named by hand.
        auto = self._next_ws_name()
        count = pane_count if pane_count is not None else self._default_count

        # Isolated workspace: give each pane its own git worktree so several
        # agents can work the same repo without colliding. Falls back to a plain
        # shared-folder workspace on any failure.
        pane_cwds: Optional[list[str]] = None
        self._pending_worktree_ids = []
        if isolate_panes and entitlements.worktrees_enabled(self._plan()):
            pane_cwds = self._create_worktrees_for_workspace(
                name or auto, count, startup_command
            )

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
        workspace.initialize(count, pane_cwds=pane_cwds)

        if pane_cwds and self._pending_worktree_ids:
            for pane, wid in zip(workspace.panes, self._pending_worktree_ids):
                self._worktree_store.update(wid, pane_id=pane.pane_id)
            workspace._intercept_pane_close = True
            workspace.pane_close_requested.connect(self._prompt_pane_worktree_close)
            self._worktree_panel.reload()
        self._pending_worktree_ids = []

        workspace.changed.connect(self._on_workspace_changed)
        workspace.active_pane_changed.connect(lambda _pane: self._refresh_status())
        workspace.empty.connect(self._on_workspace_empty)
        workspace.notice.connect(
            lambda message: self.statusBar().showMessage(message, 3000)
        )
        workspace.pane_submitted.connect(self._on_pane_submitted)
        workspace.pane_handoff_requested.connect(self._start_handoff)

        self._workspaces.append(workspace)
        self._ws_stack.addWidget(workspace)
        self._select_workspace(workspace)
        self._persist_session()
        return workspace

    # -- session persistence -----------------------------------------------

    def _session_snapshot(self) -> SessionSnapshot:
        rows = [
            WorkspaceSnapshot(
                name=ws.name,
                panes=ws.pane_count,
                agent_key=ws.panes[0].detect_agent_key() if ws.panes else "",
                agent_command=(ws.panes[0].startup_command if ws.panes else "")
                or getattr(ws, "_startup_command", "") or "",
            )
            for ws in self._workspaces
        ]
        active = 0
        if self._active_ws in self._workspaces:
            active = self._workspaces.index(self._active_ws)
        return SessionSnapshot(
            folder=self._working_folder or "",
            layout=self._layout_setting,
            active=active,
            workspaces=rows,
        )

    def _persist_session(self) -> None:
        """Debounced save -- many small changes in a burst write once."""
        if not self._persist_settings or not self.config.get("restore_session", True):
            return
        self._session_save_timer.start()

    def _do_persist_session(self) -> None:
        if not self._persist_settings or not self.config.get("restore_session", True):
            return
        try:
            self._workspaces_store.save(self._session_snapshot())
        except Exception:  # noqa: BLE001 - a save must never crash the app
            pass

    def _restore_session(self) -> bool:
        """Rebuild the workspace list from the last session. Returns whether it
        opened anything (``False`` -> caller seeds a fresh workspace).

        Only the *first* workspace opens now; the rest wait in
        ``_pending_restore`` until ``_apply_entitlements`` confirms the plan
        allows them (the account plan is still resolving at construction).
        """
        if not self._persist_settings or not self.config.get("restore_session", True):
            return False
        if getattr(self, "_startup", None) and self._startup.get("fresh"):
            return False
        try:
            session = self._workspaces_store.load()
        except Exception:  # noqa: BLE001
            return False
        if not session.is_usable():
            return False

        if session.layout in (LAYOUT_GRID, LAYOUT_COLUMNS, LAYOUT_ROWS):
            self._layout_setting = session.layout

        rows = list(session.workspaces)
        self._restore_active_index = session.active
        first, rest = rows[0], rows[1:]
        self._pending_restore = rest
        self._open_snapshot(first)
        if not self._workspaces:
            self._pending_restore = []
            return False
        return True

    def _open_snapshot(self, snap: "WorkspaceSnapshot") -> None:
        command = (snap.agent_command or "").strip()
        self._add_workspace(
            name=snap.name or None,
            pane_count=snap.panes,
            startup_command=command or None,
        )

    def _resume_pending_restore(self) -> None:
        """Open the workspaces a Free-while-resolving restore deferred."""
        if not self._pending_restore:
            return
        cap = entitlements.max_workspaces(self.account.plan)
        while self._pending_restore and len(self._workspaces) < cap:
            self._open_snapshot(self._pending_restore.pop(0))
        self._pending_restore = []
        idx = self._restore_active_index
        if 0 <= idx < len(self._workspaces):
            self._select_workspace(self._workspaces[idx])

    # -- worktrees ------------------------------------------------------------

    def _active_repo_info(self):
        """``git_worktree.RepoInfo`` for the working folder, or ``None``.

        Cached per folder -- the Worktrees panel calls this on every redraw.
        """
        folder = self._working_folder or ""
        if not folder:
            return None
        if folder in self._repo_info_cache:
            return self._repo_info_cache[folder]
        try:
            info = git_worktree.detect_repo(folder)
        except git_worktree.GitError:
            info = None
        self._repo_info_cache[folder] = info
        return info

    def _create_worktrees_for_workspace(
        self, ws_name: str, count: int, startup_command: Optional[str]
    ) -> Optional[list[str]]:
        """Create one git worktree per pane. Returns the pane cwds, or ``None``
        (and opens the workspace shared-folder) on any failure."""
        info = self._active_repo_info()
        if info is None:
            self.statusBar().showMessage(
                "Folder isn't a git repository — opening without isolation", 5000
            )
            return None

        base = info.default_base or info.current_branch
        if not base:
            self.statusBar().showMessage(
                "Repo has no mainline branch yet — opening without isolation", 5000
            )
            return None

        agent_key = ""
        try:
            import agents

            agent_key = agents.agent_key_for_command(startup_command or "") or ""
        except Exception:  # noqa: BLE001
            agent_key = ""

        progress = QProgressDialog(
            f"Creating {count} isolated worktree(s)…", None, 0, count, self
        )
        progress.setWindowTitle("New workspace")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)

        cwds: list[str] = []
        created_ids: list[str] = []
        root = default_worktrees_root()
        repo_key = repo_key_for(info.toplevel)
        try:
            for i in range(count):
                progress.setValue(i)
                QApplication.processEvents()
                branch = branch_name(ws_name, i)
                dest = str(worktree_dir(root, repo_key, branch))
                st = git_worktree.add_worktree(
                    info, worktree_path=dest, branch=branch, base=base
                )
                rec = self._worktree_store.create(
                    repo_root=info.toplevel,
                    repo_key=repo_key,
                    branch=st.branch,
                    path=dest,
                    base_branch=base,
                    base_sha_at_create=st.base_sha,
                    workspace_name=ws_name,
                    agent_key=agent_key,
                    status="active",
                )
                created_ids.append(rec.id)
                cwds.append(dest)
            progress.setValue(count)
        except git_worktree.GitError as exc:
            progress.close()
            # Roll back whatever we made this call.
            for wid in created_ids:
                rec = self._worktree_store.get(wid)
                if rec is not None:
                    try:
                        git_worktree.remove_worktree(info, rec.path, force=True)
                        git_worktree.delete_branch(info, rec.branch)
                    except git_worktree.GitError:
                        pass
                    self._worktree_store.delete(wid)
            self.statusBar().showMessage(
                f"Couldn't create worktrees ({exc}) — opening without isolation", 7000
            )
            return None

        self._pending_worktree_ids = created_ids
        return cwds

    def _prompt_pane_worktree_close(self, pane: TerminalPane) -> None:
        """A pane owning an isolated worktree is being closed -- ask what to do
        with its branch first, then close the pane."""
        ws = next((w for w in self._workspaces if pane in w.panes), None)
        rec = self._worktree_store.by_pane(pane.pane_id)
        if ws is None:
            return
        if rec is None:
            ws.close_pane(pane, force=True)
            return

        info = self._active_repo_info()
        box = QMessageBox(self)
        box.setWindowTitle("Close terminal")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"This terminal has its own worktree on branch\n“{rec.short_branch}”."
        )
        box.setInformativeText("What should happen to it?")
        merge_btn = None
        if entitlements.worktrees_enabled(self._plan()) and info is not None:
            merge_btn = box.addButton(
                f"Merge to {rec.base_branch}", QMessageBox.AcceptRole
            )
        keep_btn = box.addButton("Keep branch", QMessageBox.ActionRole)
        discard_btn = box.addButton("Discard", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(keep_btn)
        box.exec()
        clicked = box.clickedButton()

        if clicked is cancel_btn:
            return
        if clicked is merge_btn:
            if not self._run_merge(rec.id):
                return  # merge failed / conflicted -- keep the pane open to fix it
            ws.close_pane(pane, force=True)
            self._worktree_store.update(rec.id, pane_id="")
            self._worktree_panel.reload()
            return
        if clicked is discard_btn:
            ws.close_pane(pane, force=True)  # kill the shell (releases the cwd) first
            self._remove_worktree_dir(rec.id)
            return

        # Keep: detach the record from the pane, leave branch + dir on disk.
        ws.close_pane(pane, force=True)
        self._worktree_store.update(rec.id, pane_id="", status="detached")
        self._worktree_panel.reload()

    def _remove_worktree_dir(self, wid: str) -> None:
        rec = self._worktree_store.get(wid)
        if rec is None:
            return
        info = self._active_repo_info()
        try:
            if info is not None:
                git_worktree.remove_worktree(info, rec.path, force=True)
                git_worktree.delete_branch(info, rec.branch)
            self._worktree_store.set_status(wid, "discarded")
        except OSError:
            self._worktree_store.set_status(wid, "pending_delete")
            self.statusBar().showMessage(
                "Worktree folder is locked — it'll be cleaned up on next launch", 6000
            )
        except git_worktree.GitError as exc:
            self.statusBar().showMessage(f"Couldn't remove worktree: {exc}", 6000)
        self._worktree_panel.reload()

    def _discard_worktree(self, wid: str) -> None:
        rec = self._worktree_store.get(wid)
        if rec is None:
            return
        if QMessageBox.question(
            self, "Discard worktree",
            f"Delete the worktree and branch “{rec.short_branch}”?\n"
            "Any uncommitted work in it is lost.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        # A pane may still be sitting in it.
        for ws in self._workspaces:
            for pane in list(ws.panes):
                if pane.pane_id == rec.pane_id:
                    ws.close_pane(pane, force=True)
        self._remove_worktree_dir(wid)

    def _open_worktree_in_pane(self, wid: str) -> None:
        rec = self._worktree_store.get(wid)
        if rec is None or not rec.dir_exists:
            self.statusBar().showMessage("That worktree folder is gone", 4000)
            return
        ws = self._active_ws or (self._workspaces[0] if self._workspaces else None)
        if ws is None:
            return
        self._leave_worktrees()
        command = self._startup_command or None
        if command:
            pane = ws.add_pane_with_command(command, cwd=rec.path)
        else:
            pane = ws.add_pane(cwd=rec.path)
        if pane is not None:
            self._worktree_store.update(rec.id, pane_id=pane.pane_id, status="active")
            ws._intercept_pane_close = True
            try:
                ws.pane_close_requested.disconnect(self._prompt_pane_worktree_close)
            except (RuntimeError, TypeError):
                pass
            ws.pane_close_requested.connect(self._prompt_pane_worktree_close)

    def _run_merge(self, wid: str) -> bool:
        """Merge a worktree's branch to its base. Returns True on success."""
        rec = self._worktree_store.get(wid)
        info = self._active_repo_info()
        if rec is None or info is None:
            return False
        if not entitlements.worktrees_enabled(self._plan()):
            self._prompt_upgrade(
                "Merging worktrees",
                "Reviewing an isolated worktree's diff is free; merging it back "
                "or opening a PR is a Pro feature.",
            )
            return False
        dirty, files = git_worktree.is_dirty(rec.path)
        if dirty:
            if QMessageBox.question(
                self, "Uncommitted changes",
                f"“{rec.short_branch}” has {len(files)} uncommitted change(s). "
                "Commit them all and merge?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            ) != QMessageBox.Yes:
                return False
            try:
                git_worktree.commit_all(rec.path, f"WIP on {rec.short_branch} (AgentDeck)")
            except git_worktree.GitError as exc:
                QMessageBox.warning(self, "Commit failed", str(exc))
                return False
        try:
            result = git_worktree.merge_to_base(
                info, branch=rec.branch, base=rec.base_branch,
                scratch_root=str(default_worktrees_root() / repo_key_for(info.toplevel)),
            )
        except git_worktree.WorktreeConflict as exc:
            QMessageBox.warning(
                self, "Merge conflict",
                "The merge hit conflicts in:\n\n"
                + "\n".join(f"  • {p}" for p in exc.conflicts[:12])
                + "\n\nNothing was merged. Open the worktree in a pane to resolve it.",
            )
            self._worktree_store.update(rec.id, last_merge_status="conflict")
            self._worktree_panel.reload()
            return False
        except git_worktree.GitError as exc:
            QMessageBox.warning(self, "Merge failed", str(exc))
            return False

        self._worktree_store.update(
            rec.id, status="merged",
            last_merge_status="fast-forward" if result.fast_forward else "merge commit",
        )
        self._repo_info_cache.pop(self._working_folder or "", None)
        self._worktree_panel.reload()
        self.statusBar().showMessage(
            f"Merged {rec.short_branch} into {rec.base_branch} "
            f"({'fast-forward' if result.fast_forward else 'merge commit'})", 6000
        )
        if getattr(self, "github", None) is not None:
            try:
                self.github.log_run("worktree.merged", rec.branch)
            except Exception:  # noqa: BLE001
                pass
        return True

    def _merge_worktree(self, wid: str) -> None:
        if self._run_merge(wid):
            rec = self._worktree_store.get(wid)
            if rec is not None and QMessageBox.question(
                self, "Remove worktree",
                f"Merged. Remove the worktree folder and branch “{rec.short_branch}” now?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            ) == QMessageBox.Yes:
                self._remove_worktree_dir(wid)

    def _open_pr_for_worktree(self, wid: str) -> None:
        rec = self._worktree_store.get(wid)
        info = self._active_repo_info()
        if rec is None or info is None:
            return
        if rec.pr_url:
            QDesktopServices.openUrl(QUrl(rec.pr_url))
            return
        gh = getattr(self, "github", None)
        if gh is None or not gh.is_connected or not info.remote_slug:
            self.statusBar().showMessage(
                "Connect GitHub in Plugins and add an origin remote first", 5000
            )
            return
        if not entitlements.worktrees_enabled(self._plan()):
            self._prompt_upgrade("Opening a PR from a worktree",
                                 "Opening a PR from an isolated worktree is a Pro feature.")
            return
        dirty, _ = git_worktree.is_dirty(rec.path)
        if dirty:
            try:
                git_worktree.commit_all(rec.path, f"WIP on {rec.short_branch} (AgentDeck)")
            except git_worktree.GitError as exc:
                QMessageBox.warning(self, "Commit failed", str(exc))
                return
        try:
            git_worktree.push_branch(rec.path, branch=rec.branch)
        except git_worktree.GitError as exc:
            QMessageBox.warning(self, "Push failed", str(exc))
            return
        title = f"{rec.workspace_name or rec.short_branch}"
        try:
            pr = gh.create_pull_request(
                info.remote_slug, head=rec.branch, base=rec.base_branch,
                title=title, body="Opened from an AgentDeck isolated worktree.",
            )
        except Exception as exc:  # noqa: BLE001 - surface any API error
            QMessageBox.warning(self, "Couldn't open PR", str(exc))
            return
        url = (pr or {}).get("html_url", "")
        if url:
            self._worktree_store.update(rec.id, pr_url=url)
            self._worktree_panel.reload()
            QDesktopServices.openUrl(QUrl(url))
            self.statusBar().showMessage(f"PR opened: {url}", 8000)

    def _show_worktrees(self) -> None:
        """Swap the terminal area for the WORKTREES review panel."""
        self._notes_panel.flush()
        self._routines_panel.flush()
        self._skills_panel.flush()
        self._notes_active = False
        self._plugins_active = False
        self._routines_active = False
        self._skills_active = False
        self._settings_active = False
        self._worktrees_active = True
        self._worktree_panel.reload()
        self._main_stack.setCurrentWidget(self._worktree_panel)
        self._hide_voice_overlay()
        self._refresh_sidebar()

    def _leave_worktrees(self) -> None:
        """Back to the workspaces view. No-op when already there."""
        if not self._worktrees_active:
            return
        self._worktrees_active = False
        self._main_stack.setCurrentWidget(self._ws_stack)
        self._restore_voice_overlay()

    def _reconcile_worktrees_on_startup(self) -> None:
        """Bring the worktree store back in step with what's on disk.

        Marks vanished worktrees orphaned, adopts strays, sweeps rows whose
        deletion failed last time. Skips entirely without git.
        """
        store = getattr(self, "_worktree_store", None)
        if store is None or not git_worktree.git_available():
            return
        live: dict[str, list] = {}
        for root in store.repo_roots():
            try:
                live[root] = git_worktree.list_worktrees(root)
            except git_worktree.GitError:
                live[root] = []
        try:
            changed = store.reconcile(live)
        except Exception:  # noqa: BLE001 - reconcile is best-effort
            changed = []

        for rec in store.with_status("pending_delete"):
            info = None
            try:
                info = git_worktree.detect_repo(rec.repo_root)
            except git_worktree.GitError:
                info = None
            try:
                if info is not None:
                    git_worktree.remove_worktree(info, rec.path, force=True)
                    git_worktree.delete_branch(info, rec.branch)
                store.delete(rec.id)
            except (OSError, git_worktree.GitError):
                pass

        if changed:
            n = len(changed)
            self.statusBar().showMessage(
                f"{n} isolated worktree(s) need attention — see Worktrees", 8000
            )

    def _wire_github_for(self, folder: Optional[str], agent_command: Optional[str]) -> bool:
        """Best-effort: add the GitHub MCP server to every installed agent's config.

        A no-op unless GitHub is connected. Writes the workspace's agent plus, by
        default, every other coding agent on PATH (``plugins_wire_all_agents``).
        Returns True if any config was actually changed.
        """
        gh = getattr(self, "github", None)
        if gh is None:
            return False
        try:
            return bool(gh.is_connected and gh.ensure_wired(folder or None, agent_command))
        except Exception:  # noqa: BLE001 - wiring is a convenience, never fatal
            return False

    def _wire_vercel_for(self, folder: Optional[str], agent_command: Optional[str]) -> bool:
        """Best-effort: add the Vercel MCP server to every OAuth-capable agent's config.

        A no-op unless Vercel is enabled. Only agents that can run the MCP OAuth
        handshake themselves are written (see ``mcp_targets.OAUTH_ALLOWLIST``).
        The folder is irrelevant (user scope) but kept for call-site symmetry.
        Returns True if any config changed.
        """
        v = getattr(self, "vercel", None)
        if v is None:
            return False
        try:
            return bool(v.is_connected and v.ensure_wired(folder or None, agent_command))
        except Exception:  # noqa: BLE001 - wiring is a convenience, never fatal
            return False

    def _wire_jira_for(self, folder: Optional[str], agent_command: Optional[str]) -> bool:
        """Best-effort: add the Atlassian (Jira) MCP server to every OAuth-capable
        agent's config. Mirrors :meth:`_wire_vercel_for`. Returns True if any
        config changed.
        """
        j = getattr(self, "jira", None)
        if j is None:
            return False
        try:
            return bool(j.is_connected and j.ensure_wired(folder or None, agent_command))
        except Exception:  # noqa: BLE001 - wiring is a convenience, never fatal
            return False

    def _wire_gitlab_for(self, folder: Optional[str], agent_command: Optional[str]) -> bool:
        """Best-effort: add the GitLab MCP server to every OAuth-capable agent's
        config. Mirrors :meth:`_wire_vercel_for`. Returns True if any config changed.
        """
        g = getattr(self, "gitlab", None)
        if g is None:
            return False
        try:
            return bool(g.is_connected and g.ensure_wired(folder or None, agent_command))
        except Exception:  # noqa: BLE001 - wiring is a convenience, never fatal
            return False

    def _wire_linear_for(self, folder: Optional[str], agent_command: Optional[str]) -> bool:
        """Best-effort: add the Linear MCP server to every OAuth-capable agent's
        config. Mirrors :meth:`_wire_vercel_for`. Returns True if any config changed.
        """
        ln = getattr(self, "linear", None)
        if ln is None:
            return False
        try:
            return bool(ln.is_connected and ln.ensure_wired(folder or None, agent_command))
        except Exception:  # noqa: BLE001 - wiring is a convenience, never fatal
            return False

    def _start_github_review(self, payload: dict) -> None:
        """Open a workspace that runs a GitHub PR review (Plugins → Review a PR)."""
        import github_mcp

        repo = str(payload.get("repo") or "").strip()
        try:
            pr_number = int(payload.get("pr_number") or 0)
        except (TypeError, ValueError):
            pr_number = 0
        if not repo or pr_number <= 0:
            return
        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}

        folder = self._working_folder or str(Path.home())
        # The review runs in whatever agent this workspace uses. A few agents
        # can't take a one-shot task on the command line the way the brief needs
        # -- fall back to Claude for those.
        import agents

        agent_cmd = self._startup_command or agents.resolve_agent(
            self._last_ws_agent, self._last_ws_agent_custom
        )
        agent_key = agents.agent_key_for_command(agent_cmd) or "claude"
        if not github_mcp.review_supported(agent_key):
            agent_cmd = "claude"
            self.statusBar().showMessage(
                "PR review runs best in Claude or Codex — starting a Claude pane", 5000
            )
        try:
            brief = github_mcp.write_review_brief(folder, repo, pr_number, options)
            command = github_mcp.review_startup_command(agent_cmd, repo, pr_number, brief)
        except OSError as exc:
            self.statusBar().showMessage(f"Couldn't start the review: {exc}", 5000)
            return

        self._leave_plugins()
        self._add_workspace(
            name=f"Review {repo.split('/')[-1]}#{pr_number}",
            pane_count=1,
            startup_command=command,
        )
        try:
            self.github.log_run("review.started", f"{repo}#{pr_number}")
        except Exception:  # noqa: BLE001
            pass

    # -- conversation handoff -------------------------------------------------

    def _hlog(self, msg: str) -> None:
        """Append a line to ~/.agentdeck-handoff.log -- a breadcrumb trail for
        diagnosing a handoff that 'did nothing'."""
        try:
            from datetime import datetime as _dt

            line = f"{_dt.now().isoformat(timespec='seconds')}  {msg}\n"
            (Path.home() / ".agentdeck-handoff.log").open("a", encoding="utf-8").write(line)
        except Exception:  # noqa: BLE001
            pass
        print(f"[handoff] {msg}")

    def _start_handoff(self, pane) -> None:
        """Pane ⤳ button: pick a target agent, then hand the conversation over."""
        if not entitlements.handoff_enabled(self._plan()):
            self._prompt_upgrade(
                "Conversation handoff",
                "Move a running agent's conversation into a fresh pane — resume "
                "the same agent, or hand the whole transcript to a different "
                "one — on AgentDeck Pro.",
            )
            return
        if pane is None or self._active_ws is None or pane not in self._active_ws.panes:
            self._hlog("start: aborted (no pane / no active workspace)")
            return

        try:
            import agents
            from handoff_dialog import HandoffDialog

            # Best guess at the source agent: the pane's own startup command,
            # then its shell title, then the workspace's configured agent.
            src = pane.detect_agent_key() or agents.agent_key_for_command(
                self._startup_command or ""
            ) or (self._last_ws_agent if self._last_ws_agent not in ("none", "custom") else "")
            after = getattr(pane, "agent_started_at", 0.0) or None
            self._hlog(
                f"start: detect={pane.detect_agent_key()!r} -> src={src!r} "
                f"dir={pane.source_dir!r} started_at={after}"
            )

            dlg = HandoffDialog(
                source_key=src,
                source_dir=pane.source_dir or self._working_folder or "",
                fork_default=bool(self.config.get("handoff_fork_session", True)),
                thinking_default=bool(self.config.get("handoff_include_thinking", False)),
                parent=self,
            )
            if dlg.exec() != QDialog.Accepted:
                self._hlog("start: dialog cancelled")
                return
            choice = dlg.result_choice()
            if choice:
                choice["_after"] = after
                self._hlog(f"start: choice={choice}")
                self._do_handoff(pane, choice)
        except Exception as exc:  # noqa: BLE001 - surface, never vanish
            self._hlog(f"start: EXCEPTION {exc!r}")
            self.statusBar().showMessage(f"Handoff failed: {exc}", 6000)
            import traceback
            traceback.print_exc()

    def _do_handoff(self, pane, choice: dict) -> None:
        """Build the target agent's startup command from ``choice`` and open a
        new pane in the current workspace running it."""
        import agents
        import agent_sessions

        ws = self._active_ws
        if ws is None:
            return

        source_key = choice.get("source_key") or ""
        target_key = choice.get("target_key") or ""
        base_command = choice.get("target_command") or agents.resolve_agent(target_key)
        if not base_command:
            self._hlog(f"do: target {target_key!r} not on PATH -> abort")
            QMessageBox.information(
                self, "Handoff",
                f"{agents.agent_label(target_key)} isn't installed, so there's "
                f"nothing to hand the conversation to.",
            )
            return

        folder = choice.get("source_dir") or self._working_folder or str(Path.home())
        any_cwd = bool(choice.get("any_cwd"))
        after = choice.get("_after") if "_after" in choice else (
            getattr(pane, "agent_started_at", 0.0) or None
        )
        same_agent = bool(source_key) and source_key == target_key

        session = None
        if source_key:
            session = agent_sessions.locate_latest(
                source_key, folder, any_cwd=any_cwd, after=after
            )
            if session is None and not any_cwd:
                session = agent_sessions.locate_latest(
                    source_key, folder, any_cwd=True, after=after
                )
        self._hlog(
            f"do: src={source_key!r} tgt={target_key!r} folder={folder!r} "
            f"any_cwd={any_cwd} after={after} -> session="
            + (f"{session.session_id!r} path={session.path}" if session else "None")
        )

        command = base_command
        seed_prompt = ""
        src_label = agents.agent_label(source_key) if source_key else "the source"
        tgt_label = agents.agent_label(target_key)

        resumed = None
        if session is not None and same_agent and agent_sessions.supports_resume(source_key):
            resumed = agent_sessions.resume_command(
                source_key, base_command, session, fork=bool(choice.get("fork", True))
            )

        if resumed:
            command = resumed
            self._hlog(f"do: resume command = {command!r}")
            self.statusBar().showMessage(f"Resuming {src_label} in a new pane.", 5000)
        elif session is not None:
            md = agent_sessions.transcript_markdown(
                source_key,
                session,
                include_thinking=bool(choice.get("include_thinking")),
                max_chars=int(self.config.get("handoff_max_transcript_chars", 60_000)),
            )
            self._hlog(f"do: transcript len = {len(md) if md else 'None'}")
            if md is None:
                if QMessageBox.question(
                    self, "Handoff",
                    f"The {src_label} session I found has no conversation to hand "
                    f"over yet.\n\nStart {tgt_label} anyway (with a clean session)?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                ) != QMessageBox.Yes:
                    return
            else:
                try:
                    doc = agent_sessions.write_handoff_doc(
                        folder, md, source_key=source_key, target_key=target_key
                    )
                    abs_path = str(doc)
                    self._hlog(f"do: wrote transcript -> {abs_path}")
                except OSError as exc:
                    self._hlog(f"do: write_handoff_doc failed: {exc!r}")
                    self.statusBar().showMessage(f"Couldn't write the handoff: {exc}", 6000)
                    abs_path = ""
                if abs_path:
                    seed_prompt = (
                        f'Read the file "{abs_path}" — it is a transcript of an '
                        f"earlier session with {src_label}. Pick the work up from "
                        f"where it left off."
                    )
                    command = (
                        agent_sessions.initial_prompt_command(
                            target_key, base_command, seed_prompt
                        )
                        or base_command
                    )
                    self._hlog(f"do: transcript command = {command!r}")
                    self.statusBar().showMessage(
                        f"Handed {src_label}'s conversation to {tgt_label}.", 5000
                    )
        else:
            self._hlog("do: no session found")
            if QMessageBox.question(
                self, "Handoff",
                f"I couldn't find a {src_label} conversation for\n{folder}\n\n"
                f"Start {tgt_label} anyway (with a clean session)?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return

        # Pre-trust + plugin-wire the target the same way _add_workspace does.
        if self.config.get("pretrust_agent_folder", False):
            try:
                agents.pretrust_folder(base_command, folder)
            except Exception:  # noqa: BLE001
                pass
        self._wire_github_for(folder, base_command)
        self._wire_vercel_for(folder, base_command)
        self._wire_jira_for(folder, base_command)
        self._wire_gitlab_for(folder, base_command)
        self._wire_linear_for(folder, base_command)

        self._hlog(f"do: add_pane_with_command({command!r})")
        new_pane = ws.add_pane_with_command(command)
        if new_pane is None:
            self._hlog("do: add_pane_with_command returned None (pane cap)")
            self._prompt_upgrade(
                "More panes",
                "The handoff needs a new pane, but this workspace is at its pane "
                "limit — close a pane and try again.",
            ) if ws.max_panes < 16 else self.statusBar().showMessage(
                "This workspace is at the 16-pane limit — close a pane first.", 6000
            )
            return

        # When the target can't take the instruction as a CLI arg, type it in
        # after it's up — without Enter, so the user reviews it first. Some TUIs
        # take several seconds to grab the terminal, so try twice.
        if seed_prompt and command == base_command:
            done = []

            def _seed(p=new_pane, t=seed_prompt):
                if done or p not in ws.panes:
                    return
                # Only once the pane has produced output (the TUI has painted).
                if getattr(p.view, "_last_output_at", 0.0):
                    p.view.insert_text(t + " ")
                    done.append(True)

            QTimer.singleShot(3500, _seed)
            QTimer.singleShot(7000, _seed)
            QTimer.singleShot(12000, _seed)
            self.statusBar().showMessage(
                "Seeded the handoff prompt in the new pane — review it and press Enter.",
                8000,
            )

    # -- routines --------------------------------------------------------

    def _refresh_routines_panel(self) -> None:
        panel = getattr(self, "_routines_panel", None)
        if panel is not None:
            panel.refresh_list()

    def _run_routine_now(self, routine_id: str) -> None:
        """The Routines panel's "Run now" button -- fire a routine immediately,
        outside its schedule, and drop into the workspaces view to watch it."""
        routine = self._routines_store.get(routine_id)
        if routine is None:
            return
        self._leave_routines()
        self._run_routine(routine)

    def _run_routine(self, routine) -> None:
        """A Routine's scheduled time arrived (``RoutineScheduler.due``):
        open (or reuse) a pane running its agent and send its prompt.

        Free/Pro-gated the same as clicking into the Routines view -- a
        routine created while Pro simply stops firing, quietly, if the plan
        later lapses (no popup from a background timer).
        """
        if not entitlements.routines_enabled(self._plan()):
            return

        import agents
        import agent_sessions

        name = routine.display_name
        command = agents.resolve_agent(routine.agent_key, routine.agent_custom)
        if routine.agent_key != agents.PLAIN_KEY and not command:
            self._routines_store.mark_run(
                routine.id, f"skipped: {agents.agent_label(routine.agent_key)} not installed"
            )
            self.statusBar().showMessage(
                f"Routine “{name}” skipped — "
                f"{agents.agent_label(routine.agent_key)} isn't installed", 6000,
            )
            self._refresh_routines_panel()
            return

        # Pre-trust + plugin-wire this agent the same way _add_workspace /
        # _do_handoff do -- the once-per-session flag inside _add_workspace
        # only covers the launch agent, not a routine's own pick.
        if command and self.config.get("pretrust_agent_folder", False):
            pretrust_folder(command, self._working_folder)
        self._wire_github_for(self._working_folder, command)
        self._wire_vercel_for(self._working_folder, command)
        self._wire_jira_for(self._working_folder, command)
        self._wire_gitlab_for(self._working_folder, command)
        self._wire_linear_for(self._working_folder, command)

        prompt = routine.prompt.strip()

        # Prefer handing the prompt to the agent on its own command line: the
        # agent boots straight into the task, with nothing to type into a TUI
        # that's still grabbing the terminal. Typing it in after the fact is
        # what produced the "prompt sits in the composer, never sent" bug --
        # a bracketed paste and the Enter that follows it race the agent's
        # render loop. Agents with no initial-prompt arg (aider, goose,
        # copilot, …) still fall back to the type-in path, now with a paced
        # Enter (see TerminalView.insert_and_submit). Skip the CLI route for a
        # prompt too long to sit safely on a Windows command line.
        launch = command
        prompt_baked = False
        if prompt and command:
            baked = agent_sessions.initial_prompt_command(
                routine.agent_key, command, prompt
            )
            if baked and len(baked) <= 6000:
                launch = baked
                prompt_baked = True

        ws = None
        if routine.workspace_target != "new":
            ws = next((w for w in self._workspaces if w.name == routine.workspace_target), None)
            if ws is None:
                self.statusBar().showMessage(
                    f"Routine “{name}”: workspace “{routine.workspace_target}” "
                    f"is gone — opening a new one", 6000,
                )

        if ws is not None:
            new_pane = ws.add_pane_with_command(launch) if launch else ws.add_pane()
        elif len(self._workspaces) < entitlements.max_workspaces(self.account.plan):
            ws = self._add_workspace(
                name=(routine.new_workspace_name or "").strip() or None,
                pane_count=1,
                startup_command=launch or None,
            )
            new_pane = ws.panes[-1] if ws.panes else None
        else:
            # Free plan, already at the workspace cap -- use the current one
            # rather than silently doing nothing.
            ws = self._active_ws or (self._workspaces[0] if self._workspaces else None)
            new_pane = (ws.add_pane_with_command(launch) if launch else ws.add_pane()) if ws else None

        if ws is None or new_pane is None:
            self._routines_store.mark_run(routine.id, "skipped: no pane available")
            self.statusBar().showMessage(f"Routine “{name}” skipped — no pane available", 6000)
            self._refresh_routines_panel()
            return

        if not prompt:
            self._routines_store.mark_run(routine.id, "ok")
            self._refresh_routines_panel()
            self.statusBar().showMessage(f"Routine “{name}” started in {ws.name}.", 5000)
            return

        done: list = []

        def _seed(p=new_pane, ws=ws, t=prompt, rid=routine.id, name=name, baked=prompt_baked):
            if done or p not in ws.panes:
                return
            # Wait until the pane has produced output (shell / agent is up).
            if not getattr(p.view, "_last_output_at", 0.0):
                return
            if not baked:
                # Type the prompt, then Enter a beat later -- a lone \r sent
                # right behind a bracketed paste gets swallowed by the agent's
                # paste handling. See TerminalView.insert_and_submit.
                p.view.insert_and_submit(t)
            done.append(True)
            self._routines_store.mark_run(rid, "ok")
            self._refresh_routines_panel()
            self.statusBar().showMessage(f"Routine “{name}” started in {ws.name}.", 5000)

        def _give_up(rid=routine.id):
            if not done:
                self._routines_store.mark_run(rid, "skipped: pane produced no output")
                self._refresh_routines_panel()

        QTimer.singleShot(3500, _seed)
        QTimer.singleShot(7000, _seed)
        QTimer.singleShot(12000, _seed)
        QTimer.singleShot(20000, _give_up)

    def _show_plugins(self) -> None:
        """Swap the terminal area for the PLUGINS panel (sidebar nav strip)."""
        self._notes_panel.flush()
        self._routines_panel.flush()
        self._skills_panel.flush()
        self._notes_active = False
        self._routines_active = False
        self._skills_active = False
        self._settings_active = False
        self._worktrees_active = False
        self._plugins_active = True
        self._main_stack.setCurrentWidget(self._plugins_panel)
        self._hide_voice_overlay()
        self._refresh_sidebar()

    def _leave_plugins(self) -> None:
        """Back to the workspaces view. No-op when already there."""
        if not self._plugins_active:
            return
        self._plugins_active = False
        self._main_stack.setCurrentWidget(self._ws_stack)
        self._restore_voice_overlay()

    def _show_notes(self) -> None:
        """Swap the terminal area for the NOTES panel (sidebar nav strip)."""
        self._routines_panel.flush()
        self._skills_panel.flush()
        self._plugins_active = False
        self._routines_active = False
        self._skills_active = False
        self._settings_active = False
        self._worktrees_active = False
        self._notes_active = True
        self._notes_panel.reload()
        self._main_stack.setCurrentWidget(self._notes_panel)
        self._hide_voice_overlay()
        self._refresh_sidebar()

    def _leave_notes(self) -> None:
        """Back to the workspaces view. No-op when already there."""
        if not self._notes_active:
            return
        self._notes_active = False
        self._notes_panel.flush()
        self._main_stack.setCurrentWidget(self._ws_stack)
        self._restore_voice_overlay()

    def _show_routines(self) -> None:
        """Swap the terminal area for the ROUTINES panel (sidebar nav strip).

        Pro-gated (same tier as conversation handoff) -- the nav button stays
        visible for discoverability, the click is gated. See
        :func:`entitlements.routines_enabled`.
        """
        if not entitlements.routines_enabled(self._plan()):
            self._prompt_upgrade(
                "Routines",
                "Schedule an agent prompt to fire at a set time — daily or on "
                "chosen weekdays — on AgentDeck Pro.",
            )
            return
        self._notes_panel.flush()
        self._skills_panel.flush()
        self._plugins_active = False
        self._notes_active = False
        self._skills_active = False
        self._settings_active = False
        self._worktrees_active = False
        self._routines_active = True
        self._routines_panel.reload()
        self._main_stack.setCurrentWidget(self._routines_panel)
        self._hide_voice_overlay()
        self._refresh_sidebar()

    def _leave_routines(self) -> None:
        """Back to the workspaces view. No-op when already there."""
        if not self._routines_active:
            return
        self._routines_active = False
        self._routines_panel.flush()
        self._main_stack.setCurrentWidget(self._ws_stack)
        self._restore_voice_overlay()

    # -- skills ---------------------------------------------------------

    def _show_skills(self) -> None:
        """Swap the terminal area for the SKILLS panel (sidebar nav strip).

        Pro-gated (same tier as Routines / Plugins / Handoff) -- the nav button
        stays visible for discoverability, the click is gated. See
        :func:`entitlements.skills_enabled`.
        """
        if not entitlements.skills_enabled(self._plan()):
            self._prompt_upgrade(
                "Skills",
                "Upload reusable SKILL.md instructions, wire them into every "
                "agent, and let an agent review and improve them — on AgentDeck "
                "Pro. Your library syncs across your devices.",
            )
            return
        self._notes_panel.flush()
        self._routines_panel.flush()
        self._plugins_active = False
        self._notes_active = False
        self._routines_active = False
        self._settings_active = False
        self._worktrees_active = False
        self._skills_active = True
        self._skills_panel.reload()
        self._main_stack.setCurrentWidget(self._skills_panel)
        self._hide_voice_overlay()
        self._refresh_sidebar()

    def _leave_skills(self) -> None:
        """Back to the workspaces view. No-op when already there."""
        if not self._skills_active:
            return
        self._skills_active = False
        self._skills_panel.flush()
        self._main_stack.setCurrentWidget(self._ws_stack)
        self._restore_voice_overlay()

    def _build_skills_cloud(self) -> None:
        """The cross-device mirror for the skill library (Pro; best-effort)."""
        try:
            from skills_cloud import SkillsCloud

            self._skills_cloud = SkillsCloud(
                self.account, self.config, self._skills_store, self
            )
            self._skills_cloud.pulled.connect(self._on_skills_pulled)
        except Exception:  # noqa: BLE001 - the mirror is optional
            self._skills_cloud = None

    def _on_skills_pulled(self) -> None:
        """A cloud pull changed the local library -- reflect it everywhere."""
        if getattr(self, "_skills_panel", None) is not None:
            self._skills_panel.reload()
        self._materialize_skills()

    def _skill_folders(self) -> "list[str]":
        """Workspace folders to wire the AGENTS.md skill fallback into. Every
        workspace opens in the working folder, so that's the one that matters."""
        src = (self._working_folder or "").strip()
        return [src] if src and Path(src).is_dir() else []

    def _materialize_skills(self) -> None:
        """Put enabled skills where agents will find them (or, if the plan is
        not Pro, take them all back out). Best-effort, never raises."""
        try:
            if not entitlements.skills_enabled(self._plan()):
                if self._skills_materialized_once:
                    skills_sync.remove_all()
                    self._skills_materialized_once = False
                return
            skills = self._skills_store.all()
            if not skills and not self._skills_materialized_once:
                return  # nothing to write yet -- don't create empty dirs
            self._skills_materialized_once = True
            skills_sync.materialize(
                skills,
                folders=self._skill_folders(),
                write_agents_md=bool(self.config.get("skills_materialize_agents_md", True)),
            )
        except Exception:  # noqa: BLE001 - materialization is a convenience
            pass

    def _on_skills_changed(self) -> None:
        self._materialize_skills()
        cloud = getattr(self, "_skills_cloud", None)
        if cloud is not None:
            cloud.push_soon()

    def _improve_skill_with_agent(self, skill_id: str) -> None:
        """"Improve with agent": open a pane where an agent reviews the skill's
        file and rewrites it in place; watch that file and re-import its edits."""
        if not entitlements.skills_enabled(self._plan()):
            return
        import agent_sessions

        skill = self._skills_store.get(skill_id)
        if skill is None:
            return
        agent_key = str(self.config.get("skills_improve_agent") or "").strip()
        keys = installed_agent_keys()
        if agent_key not in keys:
            if not keys:
                self.statusBar().showMessage(
                    "Improve with agent needs a coding agent installed", 5000
                )
                return
            agent_key = keys[0]
        command = resolve_agent(agent_key, "")
        if not command:
            self.statusBar().showMessage(
                f"{agent_label(agent_key)} isn't installed", 5000
            )
            return

        folder = self._working_folder or None
        if command and self.config.get("pretrust_agent_folder", False):
            pretrust_folder(command, self._working_folder)
        self._wire_github_for(folder, command)
        self._wire_vercel_for(folder, command)
        self._wire_jira_for(folder, command)
        self._wire_gitlab_for(folder, command)
        self._wire_linear_for(folder, command)

        target = skills_sync.agent_review_target(skill, folder, agent_key)
        prompt = (
            f"Review the skill file at {target}. Check it against Claude Code "
            "SKILL.md guidance: a precise `description` that says exactly when to "
            "use the skill, tight imperative instructions, no redundancy, and "
            "valid `name`/`description` frontmatter. Edit the file in place with "
            "your improvements, then give me a 2-3 line summary of what you "
            "changed."
        )
        launch = command
        baked = agent_sessions.initial_prompt_command(agent_key, command, prompt)
        if baked and len(baked) <= 6000:
            launch = baked

        self._leave_skills()
        ws = self._active_ws or (self._workspaces[0] if self._workspaces else None)
        if ws is None:
            self.statusBar().showMessage("No workspace to run the review in", 5000)
            return
        pane = ws.add_pane_with_command(launch)
        if pane is None:
            self.statusBar().showMessage("No pane available for the review", 5000)
            return
        parsed = skills_sync.read_markdown_skill(target)
        cur_sha = _sha_of_triple(
            parsed if parsed else (skill.name, skill.description, skill.body)
        )
        self._skill_watch[str(target)] = (skill_id, cur_sha, agent_key)
        if not baked:
            QTimer.singleShot(
                3000, lambda p=pane, ws=ws, t=prompt: (
                    p.view.insert_and_submit(t) if p in ws.panes else None
                )
            )
        self.statusBar().showMessage(
            f"{agent_label(agent_key)} is reviewing “{skill.display_name}”", 5000
        )

    def _check_skill_watches(self) -> None:
        """Poll every "Improve with agent" target file; re-import an agent's
        edits into the skill library. Called from the 1 s watchdog."""
        if not self._skill_watch:
            return
        for path, (skill_id, last_sha, agent_key) in list(self._skill_watch.items()):
            parsed = skills_sync.read_markdown_skill(path)
            if parsed is None:
                continue
            new_sha = _sha_of_triple(parsed)
            if new_sha == last_sha:
                continue
            name, description, body = parsed
            skill = self._skills_store.get(skill_id)
            if skill is None:
                self._skill_watch.pop(path, None)
                continue
            self._skills_store.update(
                skill_id,
                name=name or skill.name,
                description=description or skill.description,
                body=body,
                source="agent",
                last_reviewed_at=time.time(),
                last_reviewed_by=agent_label(agent_key),
            )
            self._skill_watch[path] = (skill_id, new_sha, agent_key)
            self._materialize_skills()
            cloud = getattr(self, "_skills_cloud", None)
            if cloud is not None:
                cloud.push_soon()
            if self._skills_active:
                self._skills_panel.reload_current_from_store()
            self.statusBar().showMessage(
                f"Skill “{name or skill.display_name}” updated by "
                f"{agent_label(agent_key)}", 6000
            )

    def _send_note_to_terminal(self, text: str) -> None:
        """Leave the Notes view and drop a note's body at the active pane's
        prompt -- no Enter, same as a file drop / voice insert."""
        self._leave_notes()
        pane = self._active
        if pane is None:
            self.statusBar().showMessage("No terminal pane to send the note to", 4000)
            return
        pane.view.insert_text(text)
        try:
            pane.view.setFocus(Qt.OtherFocusReason)
        except Exception:  # noqa: BLE001 - focus is best-effort
            pass
        self.statusBar().showMessage("Note sent to the active terminal", 3000)

    def _hide_voice_overlay(self) -> None:
        overlay = getattr(self, "_voice_overlay", None)
        if overlay is not None:
            overlay.setVisible(False)

    def _restore_voice_overlay(self) -> None:
        overlay = getattr(self, "_voice_overlay", None)
        if overlay is not None and self.config.get("voice_overlay_visible", True):
            overlay.setVisible(True)
            self._position_overlay()

    def _select_workspace(self, workspace: Workspace) -> None:
        if workspace not in self._workspaces:
            return
        self._leave_plugins()
        self._leave_notes()
        self._leave_routines()
        self._leave_skills()
        self._leave_worktrees()
        self._leave_settings()
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
        for pane in workspace.panes:
            self._pane_attn_notified.pop(pane.pane_id, None)
            self._notifier.forget(pane.pane_id)
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
        self._persist_session()

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
        self._persist_session()

    def _reorder_workspaces(self, src: int, dst: int) -> None:
        """A sidebar row was dragged from position ``src`` to ``dst``.

        Reorders the in-memory list and repaints; Ctrl+Tab cycling and the
        sidebar both read this list, so they follow along. The new order is
        saved to the session snapshot too.
        """
        n = len(self._workspaces)
        if not (0 <= src < n) or not (0 <= dst < n) or src == dst:
            return
        workspace = self._workspaces.pop(src)
        self._workspaces.insert(dst, workspace)
        self._refresh_sidebar()
        self._refresh_status()
        self._persist_session()
        self.statusBar().showMessage(
            f"Moved “{workspace.name}” to position {dst + 1}", 2000
        )

    def _on_workspace_changed(self) -> None:
        self._refresh_sidebar()
        self._refresh_status()
        self._persist_session()

    def _refresh_sidebar(self) -> None:
        on_nav_view = (
            self._plugins_active or self._notes_active
            or self._routines_active or self._skills_active
            or self._settings_active or self._worktrees_active
        )
        active = None if on_nav_view else self._active_ws
        self._sidebar.refresh(self._workspaces, active)
        self._sidebar.set_plugins_active(self._plugins_active)
        self._sidebar.set_notes_active(self._notes_active)
        self._sidebar.set_routines_active(self._routines_active)
        self._sidebar.set_skills_active(self._skills_active)
        self._sidebar.set_worktrees_active(self._worktrees_active)

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

        self._voice_overlay.toggle_requested.connect(self._on_overlay_toggle)
        self._voice_overlay.dismiss_requested.connect(
            lambda: self._set_overlay_visible(False))
        self._voice_overlay.submit_requested.connect(self._on_overlay_submit)
        self._voice_overlay.moved.connect(self._on_voice_moved)
        self._voice_engine.state.connect(self._on_voice_state)
        self._voice_engine.level.connect(self._voice_overlay.set_level)
        self._voice_engine.transcription.connect(self._on_voice_text)
        self._voice_engine.partial.connect(self._voice_overlay.set_partial)
        self._voice_engine.error.connect(self._on_voice_error)
        self._voice_engine.model_progress.connect(self._on_voice_progress)

        # System-wide hotkey (fires even when AgentDeck isn't focused). Falls
        # back silently to the focused QAction on non-Windows / a taken combo.
        from global_hotkey import GlobalHotkey

        self._global_hotkey = GlobalHotkey(self)
        self._global_hotkey.activated.connect(self._on_global_voice_hotkey)
        self._global_hotkey.failed.connect(
            lambda msg: self.statusBar().showMessage(f"Voice hotkey: {msg}", 6000)
        )
        self._global_hotkey.install(QApplication.instance())
        self._fg_hwnd = None
        self._last_hotkey_ms = 0.0
        self._rebind_global_hotkey()

        if not self._voice_engine.available:
            self._voice_overlay.set_available(False, self._voice_engine.import_error)

        # The master switch hides the overlay entirely; the engine/overlay are
        # still built (cheap) so flipping it back on needs no restart.
        enabled = bool(self.config.get("voice_input_enabled", True))
        visible = enabled and bool(self.config.get("voice_overlay_visible", True))
        self._voice_overlay.setVisible(visible)
        self._voice_btn.setChecked(visible)
        self._voice_btn.setEnabled(enabled)
        self._position_overlay()
        self._voice_overlay.raise_()

        # Warm the speech model in the background so the first Ctrl+Shift+X is
        # instant instead of a multi-second "loading model…". Pro-only (the
        # feature is), and only when voice is switched on.
        if (
            enabled
            and self._voice_engine.available
            and entitlements.voice_enabled(self._plan())
        ):
            QTimer.singleShot(2500, self._voice_engine.prewarm)

        # First-run nudge: shown once, only while the feature is on.
        if enabled and not self.config.get("voice_hint_seen", False):
            self.config["voice_hint_seen"] = True
            QTimer.singleShot(
                4000,
                lambda: self.statusBar().showMessage(
                    "Tip: press Ctrl+Shift+X to dictate into the focused pane.", 7000
                ),
            )

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

    def _voice_hotkey_dedupe(self) -> bool:
        """True when a voice-hotkey activation already fired in the last 250 ms.

        The OS ``RegisterHotKey`` posts ``WM_HOTKEY`` even while an AgentDeck
        window is focused, and the in-app ``Ctrl+Shift+X`` ``QAction`` fires in
        that case too -- so a single keypress reaches *both*
        ``_on_global_voice_hotkey`` and ``_toggle_voice``. Left alone they call
        ``engine.toggle()`` twice and cancel out, which is the "the hotkey
        stops working after a while" report (it breaks the moment AgentDeck has
        focus). Both handlers funnel through here so the second one is dropped.
        """
        import time

        now = time.monotonic() * 1000.0
        if now - getattr(self, "_last_hotkey_ms", 0.0) < 250:
            return True
        self._last_hotkey_ms = now
        return False

    def _toggle_voice(self) -> None:
        """Ctrl+Shift+X -- reveal the widget if hidden, then start/stop listening."""
        if self._voice_hotkey_dedupe():
            return
        if self._voice_gated():
            return
        if not self.config.get("voice_input_enabled", True):
            self.statusBar().showMessage(
                "Voice input is switched off in Settings ▸ Voice input.", 4000
            )
            return
        # Focused toggle -> dictate into the active pane, not into whatever
        # window a previous "foreground"-target global hotkey remembered.
        self._fg_hwnd = None
        if not self._voice_overlay.isVisible():
            self._set_overlay_visible(True)
        self._toggle_voice_engine()

    def _on_overlay_toggle(self) -> None:
        """The capsule's mic button / its Ctrl+X -- same gating as the shortcut."""
        if self._voice_gated():
            return
        if not self.config.get("voice_input_enabled", True):
            self.statusBar().showMessage(
                "Voice input is switched off in Settings ▸ Voice input.", 4000
            )
            return
        self._fg_hwnd = None
        self._toggle_voice_engine()

    def _toggle_voice_engine(self) -> None:
        """Flip the engine, and reflect a stop on the overlay at once rather than
        waiting for the (now quick) teardown to emit ``idle``."""
        was_listening = self._voice_engine.is_listening
        self._voice_engine.toggle()
        if was_listening:
            self._voice_overlay.set_state("idle")

    def _on_overlay_submit(self) -> None:
        """A bare Enter while the floating capsule held keyboard focus (it grabs
        focus on a click/drag).

        This is *not* the terminal -- pressing Enter on a control chip should
        never run a shell command. So here Enter just ends the dictation
        session and hands focus back to the pane; the user still presses Enter
        *in the terminal* to actually run the line.
        """
        self._fg_hwnd = None
        if self._voice_engine.is_listening:
            self._toggle_voice_engine()
        else:
            self._voice_overlay.set_state("idle")
        pane = self._active
        if pane is not None:
            pane.view.setFocus()

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

    # -- global hotkey -----------------------------------------------------

    def _rebind_global_hotkey(self) -> None:
        """(Re)register the OS hotkey to match config + the current plan.

        When the OS-wide hotkey is live, the focused-window ``QAction`` copy is
        disabled: ``RegisterHotKey`` still posts ``WM_HOTKEY`` while AgentDeck is
        focused, so leaving both enabled meant one keypress fired
        ``_on_global_voice_hotkey`` *and* ``_toggle_voice`` -> ``engine.toggle()``
        twice -> net no-op ("the hotkey stopped working").
        """
        gk = getattr(self, "_global_hotkey", None)
        if gk is None:
            return
        want = (
            self.config.get("voice_global_hotkey_enabled", True)
            and self.config.get("voice_input_enabled", True)
            and entitlements.voice_enabled(self._plan())
        )
        bound = False
        if want:
            bound = gk.bind(
                self.config.get("voice_hotkey", "Ctrl+Shift+X") or "Ctrl+Shift+X"
            )
        else:
            gk.unbind()
        action = getattr(self, "_voice_action", None)
        if action is not None:
            # Only the OS hotkey when it took; the QAction otherwise.
            action.setEnabled(not bound)

    def _on_global_voice_hotkey(self) -> None:
        if self._voice_hotkey_dedupe():           # de-dupe vs the focused QAction
            return
        if self._voice_gated():
            return
        if not self.config.get("voice_input_enabled", True):
            return

        target = self.config.get("voice_global_target", "agentdeck")
        if target == "foreground" and sys.platform == "win32":
            try:
                import ctypes
                self._fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
            except Exception:  # noqa: BLE001
                self._fg_hwnd = None
        else:
            self._fg_hwnd = None
            win = self.window()
            if win.isMinimized():
                win.showNormal()
            win.show()
            win.raise_()
            win.activateWindow()

        if not self._voice_overlay.isVisible():
            self._set_overlay_visible(True)
        self._toggle_voice_engine()

    def _paste_to_foreground(self, text: str) -> bool:
        """Type ``text`` into the window that was in front when the hotkey fired."""
        if not self._fg_hwnd or sys.platform != "win32":
            return False
        try:
            import ctypes

            clip = QApplication.clipboard()
            saved = clip.text()
            clip.setText(text)
            u32 = ctypes.windll.user32
            u32.SetForegroundWindow(self._fg_hwnd)
            # Ctrl+V via keybd_event (VK_CONTROL 0x11, 'V' 0x56; 0x0002 = keyup).
            u32.keybd_event(0x11, 0, 0, 0)
            u32.keybd_event(0x56, 0, 0, 0)
            u32.keybd_event(0x56, 0, 2, 0)
            u32.keybd_event(0x11, 0, 2, 0)
            QTimer.singleShot(400, lambda: clip.setText(saved))
            return True
        except Exception:  # noqa: BLE001
            return False

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
        self._voice_overlay.set_partial("")     # drop the interim view

        # A decode that lands *after* the user already stopped -- Ctrl+Shift+X,
        # a bare Enter, or "stop listening" -- must not touch the prompt, and
        # above all must never auto-run a command. VoiceEngine._emit_transcription
        # guards this on the worker thread, but a transcription can pass that
        # check microseconds before stop() flips the flag and still arrive here
        # on the GUI thread. Re-check on this side so the stop is always clean.
        engine = getattr(self, "_voice_engine", None)
        if engine is not None and not engine.is_listening:
            return

        action, rest = voice_commands.parse(text, self.config)
        pane = self._active

        if action == "stop":
            self._voice_overlay.flash_text("⏹ stopped")
            self._voice_engine.stop_listening()
            return
        if action and pane is not None:
            if action == "submit":
                self._voice_overlay.flash_text("⏎ sent")
                pane.view.submit()
            elif action == "newline":
                pane.view.insert_text("\n")
            elif action == "scratch":
                self._voice_overlay.flash_text("⌫ scratched")
                pane.view.erase_text(getattr(self, "_last_voice_len", 0))
            self._last_voice_len = 0
            return

        clean = voice_postprocess.apply(rest or text, self.config).strip()
        self._voice_overlay.flash_text(clean or text)
        if not clean:
            return
        payload = clean + " "
        if getattr(self, "_fg_hwnd", None) and self._paste_to_foreground(payload):
            self._last_voice_len = 0
            return
        if pane is not None:
            pane.view.insert_text(payload)
            self._last_voice_len = len(payload)
            if (self.config.get("voice_auto_send", False)
                    and not clean.rstrip().endswith(("\\", "|", "&&", "&"))):
                pane.view.submit()
                self._last_voice_len = 0

    def _on_voice_progress(self, pct: int) -> None:
        self._voice_overlay.set_progress(pct)
        self.statusBar().showMessage(f"Voice: downloading speech model… {int(pct)}%", 4000)

    def _on_voice_error(self, message: str) -> None:
        serious = any(k in message.lower() for k in ("windows settings", "microphone"))
        self.statusBar().showMessage(f"Voice: {message}", 12000 if serious else 6000)
        if serious:
            self._voice_overlay.flash_text(message)

    def _on_pane_submitted(self, _pane=None) -> None:
        """Running a command ends a dictation session -- stop listening.

        Covers a still-loading session too (``_busy`` set, ``_listening`` not yet
        cleared/loaded), and gives the overlay instant feedback rather than
        waiting for the engine's teardown to emit ``idle``.
        """
        engine = getattr(self, "_voice_engine", None)
        if engine is None:
            return
        if engine.is_listening or getattr(engine, "_busy", False) \
                or engine.current_state in ("listening", "loading"):
            engine.stop()
            overlay = getattr(self, "_voice_overlay", None)
            if overlay is not None:
                overlay.set_state("idle")   # stop the animation now, don't wait
            self.statusBar().showMessage("Voice: stopped", 2000)

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
        self._persist_session()

    # -- status ------------------------------------------------------------

    def _refresh_status(self) -> None:
        for routine in self._routine_scheduler.due(self._routines_store.all()):
            self._run_routine(routine)

        self._check_skill_watches()

        for workspace in self._workspaces:
            workspace.poll()

        # Re-classify every pane and raise a desktop notification for the ones
        # that just started needing attention (waiting on input / finished /
        # crashed) -- unless it's the pane the user is already looking at.
        self._poll_pane_attention()

        # Light the sidebar's glow dot on any workspace with an agent working.
        self._sidebar.refresh_activity()

        # Keep the open Worktrees panel roughly current -- but throttled, since a
        # refresh shells out to git for every row.
        if self._worktrees_active:
            now = time.monotonic()
            if now - self._last_worktree_poll >= 4.0:
                self._last_worktree_poll = now
                self._worktree_panel.refresh_status()

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

    # -- pane attention + notifications --------------------------------------

    def _focused_pane(self) -> Optional[TerminalPane]:
        """The pane the user is actually looking at right now (or ``None``)."""
        ws = self._active_ws
        if ws is None or not self.isActiveWindow():
            return None
        if ws.is_zoomed:
            return ws._zoomed
        return ws.active_pane

    def _poll_pane_attention(self) -> None:
        """Drive the sidebar badges + notifications from each pane's state.

        Called every second from ``_refresh_status``. Refreshing is always
        done (the badge needs it even with notifications off); the toast is
        gated on the feature switch and skips the focused pane.
        """
        focused = self._focused_pane()
        notify_on = self._notifier.enabled
        for ws in self._workspaces:
            for pane, _old, new in ws.refresh_attention():
                if new not in pane_state.ATTENTION_STATES:
                    # Left an attention state -- clear its notify latch so the
                    # next entry toasts again.
                    self._pane_attn_notified.pop(pane.pane_id, None)
                    continue
                if not notify_on or pane is focused:
                    continue
                if self._pane_attn_notified.get(pane.pane_id) == new:
                    continue
                self._pane_attn_notified[pane.pane_id] = new
                self._toast_pane(ws, pane, new)

    def _toast_pane(self, ws: Workspace, pane: TerminalPane, state: str) -> None:
        where = f"{ws.name} · terminal {pane.index + 1}"
        titles = {
            pane_state.AWAITING_INPUT: "A terminal is waiting for you",
            pane_state.DONE: "An agent finished",
            pane_state.ERROR: "A terminal exited",
        }
        self._notifier.notify(pane.pane_id, titles.get(state, "AgentDeck"), where)

    def _focus_pane_by_id(self, pane_id: str) -> None:
        """Raise AgentDeck and put the cursor in the pane a toast pointed at."""
        if not pane_id:
            self._raise_self()
            return
        for ws in self._workspaces:
            for pane in ws.panes:
                if pane.pane_id != pane_id:
                    continue
                self._raise_self()
                if ws is not self._active_ws:
                    self._select_workspace(ws)
                ws.set_active(pane)
                QTimer.singleShot(0, pane.focus_terminal)
                return
        self._raise_self()

    def _raise_self(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

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
            self._active_ws._on_pane_close_requested(pane)

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
        """Connect the UpdateController to the status bar and the "update is
        waiting" cue on the settings button.

        Updating is driven from the Settings dialog now (Updates section), but
        these panel-level hooks stay live so a launch check still surfaces:
        ``available`` shows the download prompt, ``progress`` feeds the animated
        dialog, ``ready`` offers the restart. Dormant when the app is not a
        Velopack install (``updater.enabled`` False) -- no signal ever fires.
        """
        self._install_update_glow()
        u = self.updater
        u.available.connect(self._on_update_available)
        u.up_to_date.connect(
            lambda: self.statusBar().showMessage("AgentDeck is up to date", 4000)
        )
        u.progress.connect(self._on_update_progress)
        u.ready.connect(self._on_update_ready)
        u.error.connect(self._on_update_error)

    # -- "an update is waiting" glow ---------------------------------------

    def _install_update_glow(self) -> None:
        """A pulsing halo on the gear/settings button once a release is waiting.

        A ``QGraphicsDropShadowEffect`` used as a halo (offset 0) whose blur
        radius pulses on a loop -- inert until :meth:`_set_update_glow` turns it
        on, which happens when ``updater.available`` fires. It points the user at
        Settings, where the update controls now live. (QSS has no box-shadow, so
        a graphics effect is the only way -- same trick as the pane focus glow.)
        """
        self._update_glow = QGraphicsDropShadowEffect(self)
        self._update_glow.setColor(QColor("#ff3b30"))
        self._update_glow.setOffset(0, 0)
        self._update_glow.setBlurRadius(0)
        self._update_glow.setEnabled(False)
        self._settings_btn.setGraphicsEffect(self._update_glow)

        self._update_pulse = QPropertyAnimation(self._update_glow, b"blurRadius", self)
        self._update_pulse.setDuration(1500)
        self._update_pulse.setKeyValueAt(0.0, 5)
        self._update_pulse.setKeyValueAt(0.5, 22)   # swell
        self._update_pulse.setKeyValueAt(1.0, 5)    # and back, seamlessly
        self._update_pulse.setEasingCurve(QEasingCurve.InOutSine)
        self._update_pulse.setLoopCount(-1)

    def _refresh_settings_icon(self) -> None:
        """Redraw the gear for the current state.

        While an update waits (``_update_glow`` enabled) it gets an accent tint
        and a red notification dot -- a cue that survives even if the toolbar
        clips the drop-shadow halo. Called on theme changes and whenever the
        glow toggles.
        """
        waiting = bool(
            getattr(self, "_update_glow", None)
        ) and self._update_glow.isEnabled()
        color = theme.color("accent") if waiting else None
        self._settings_btn.setIcon(gear_icon(16, color=color, badge=waiting))

    def _set_update_glow(self, on: bool) -> None:
        glow = getattr(self, "_update_glow", None)
        if glow is None:
            return
        if on:
            self._settings_btn.setToolTip(
                "A new AgentDeck version is available — open Settings to install it"
            )
            glow.setEnabled(True)
            if self._update_pulse.state() != QPropertyAnimation.Running:
                self._update_pulse.start()
        else:
            self._update_pulse.stop()
            glow.setEnabled(False)
            glow.setBlurRadius(0)
            self._settings_btn.setToolTip("Settings")
        self._refresh_settings_icon()

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
            self._show_update_dialog(version)
            self.updater.download()

    # -- download / install progress ---------------------------------------

    def _show_update_dialog(self, version: str) -> None:
        """Put up (or reuse) the animated download/install progress dialog."""
        if self._update_dialog is None:
            self._update_dialog = UpdateProgressDialog(version, self)
            self._update_dialog.finished.connect(self._forget_update_dialog)
        self._update_dialog.show()
        self._update_dialog.raise_()

    def _forget_update_dialog(self, *_args) -> None:
        self._update_dialog = None

    def _close_update_dialog(self) -> None:
        if self._update_dialog is not None:
            self._update_dialog.finish()
            self._update_dialog = None

    def _on_update_progress(self, pct: int) -> None:
        if self._update_dialog is not None:
            self._update_dialog.set_progress(pct)
        self.statusBar().showMessage(f"Downloading update… {pct}%", 2000)

    def _on_update_error(self, msg: str) -> None:
        self._close_update_dialog()
        self.statusBar().showMessage(f"Update: {msg}", 6000)

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
            self._close_update_dialog()
            self.statusBar().showMessage(
                "Update will apply next time you restart AgentDeck", 6000
            )
            return
        # Show the "installing, restarting now" state and let it paint before the
        # blocking apply call hands the process over to Velopack.
        self._show_update_dialog(version)
        if self._update_dialog is not None:
            self._update_dialog.start_installing()
        QApplication.processEvents()
        # The user already consented; tear the shells down explicitly and skip
        # the closeEvent "shells still running" prompt.
        self._shutdown_all()
        self.updater.apply_and_restart()
        # apply_and_restart only returns if it failed -- clean the dialog up.
        self._close_update_dialog()

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

        # When GitHub is (dis)connected on the Plugins page, (un)wire the agent
        # config for the folder the workspaces are running in, so a *restarted*
        # agent picks up (or loses) the GitHub tools without reopening AgentDeck.
        gh = getattr(self, "github", None)
        if gh is not None:
            gh.connected.connect(lambda _i: self._on_github_connected())
            gh.disconnected.connect(self._on_github_disconnected)
        v = getattr(self, "vercel", None)
        if v is not None:
            v.connected.connect(lambda _i: self._on_vercel_connected())
            v.disconnected.connect(self._on_vercel_disconnected)
        j = getattr(self, "jira", None)
        if j is not None:
            j.connected.connect(lambda _i: self._on_jira_connected())
            j.disconnected.connect(self._on_jira_disconnected)
        g = getattr(self, "gitlab", None)
        if g is not None:
            g.connected.connect(lambda _i: self._on_gitlab_connected())
            g.disconnected.connect(self._on_gitlab_disconnected)
        ln = getattr(self, "linear", None)
        if ln is not None:
            ln.connected.connect(lambda _i: self._on_linear_connected())
            ln.disconnected.connect(self._on_linear_disconnected)

    def _on_github_connected(self) -> None:
        if self._wire_github_for(self._working_folder, self._startup_command):
            self.statusBar().showMessage(
                "GitHub connected — restart the agent (↻) in a pane to load the GitHub tools",
                8000,
            )

    def _on_github_disconnected(self) -> None:
        self.statusBar().showMessage(
            "GitHub disconnected — restart the agent (↻) to drop the GitHub tools", 6000
        )

    def _on_vercel_connected(self) -> None:
        self._wire_vercel_for(self._working_folder, self._startup_command)
        self.statusBar().showMessage(
            f"Vercel enabled — restart the agent (↻) in a pane, then {self._oauth_hint('vercel')}",
            8000,
        )

    def _oauth_hint(self, server: str) -> str:
        """How the current workspace's agent authorises a hosted OAuth MCP server."""
        import agents
        import mcp_targets

        key = agents.agent_key_for_command(self._startup_command) or "claude"
        return mcp_targets.oauth_hint(key, server)

    def _on_vercel_disconnected(self) -> None:
        self.statusBar().showMessage(
            "Vercel disabled — restart the agent (↻) to drop the Vercel tools", 6000
        )

    def _on_jira_connected(self) -> None:
        self._wire_jira_for(self._working_folder, self._startup_command)
        self.statusBar().showMessage(
            f"Jira enabled — restart the agent (↻) in a pane, then {self._oauth_hint('atlassian')}",
            8000,
        )

    def _on_jira_disconnected(self) -> None:
        self.statusBar().showMessage(
            "Jira disabled — restart the agent (↻) to drop the Jira tools", 6000
        )

    def _on_gitlab_connected(self) -> None:
        self._wire_gitlab_for(self._working_folder, self._startup_command)
        self.statusBar().showMessage(
            f"GitLab enabled — restart the agent (↻) in a pane, then {self._oauth_hint('gitlab')}",
            8000,
        )

    def _on_gitlab_disconnected(self) -> None:
        self.statusBar().showMessage(
            "GitLab disabled — restart the agent (↻) to drop the GitLab tools", 6000
        )

    def _on_linear_connected(self) -> None:
        self._wire_linear_for(self._working_folder, self._startup_command)
        self.statusBar().showMessage(
            f"Linear enabled — restart the agent (↻) in a pane, then {self._oauth_hint('linear')}",
            8000,
        )

    def _on_linear_disconnected(self) -> None:
        self.statusBar().showMessage(
            "Linear disabled — restart the agent (↻) to drop the Linear tools", 6000
        )

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
        settings_panel = getattr(self, "_settings_panel", None)
        if settings_panel is not None:
            settings_panel.set_voice_pro(entitlements.voice_enabled(plan))
        self._rebind_global_hotkey()

        # Background update check on launch is Pro; Free keeps the manual button.
        if pro:
            self._auto_check_updates()
            # Plan resolved to Pro after the window came up: warm the speech
            # model now (the launch-time prewarm in _build_voice was skipped
            # because the plan wasn't known yet).
            engine = getattr(self, "_voice_engine", None)
            if (
                engine is not None
                and engine.available
                and self.config.get("voice_input_enabled", True)
            ):
                engine.prewarm()

        # Skills: materialize the library into the agents' configs now that we
        # know the plan (or, on a lapse to Free, take them all back out).
        self._materialize_skills()
        cloud = getattr(self, "_skills_cloud", None)
        if cloud is not None and pro:
            cloud.pull_soon()

        # Desktop notifications (not plan-gated -- a courtesy like the theme).
        self._configure_notifications()

        # Open any workspaces the session restore deferred while the plan was
        # still resolving (a Pro user's 2nd..Nth workspace).
        self._resume_pending_restore()

        # Re-arm the "expire at plan_expires_at" timer for whatever we now know.
        self._arm_plan_expiry_timer()
        # ... and the trial gate + its warning surfaces.
        self._arm_trial_timer()
        self._refresh_trial_banner()
        self._maybe_trial_last_day_modal()

    def _configure_notifications(self) -> None:
        notifier = getattr(self, "_notifier", None)
        if notifier is None:
            return
        notifier.configure(
            enabled=bool(self.config.get("notify_on_attention", True)),
            sound=bool(self.config.get("notify_sound", False)),
        )

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
        # Flush the session snapshot now, synchronously -- the debounce timer
        # won't get another tick.
        if getattr(self, "_session_save_timer", None) is not None:
            self._session_save_timer.stop()
        self._do_persist_session()
        if getattr(self, "_notes_panel", None) is not None:
            self._notes_panel.flush()
        if getattr(self, "_routines_panel", None) is not None:
            self._routines_panel.flush()
        if getattr(self, "_skills_panel", None) is not None:
            self._skills_panel.flush()
        if getattr(self, "_skills_cloud", None) is not None:
            self._skills_cloud.shutdown()
        if getattr(self, "_plan_watch", None) is not None:
            self._plan_watch.stop()
        if getattr(self, "_plan_expiry_timer", None) is not None:
            self._plan_expiry_timer.stop()
        if getattr(self, "_trial_timer", None) is not None:
            self._trial_timer.stop()
        self._set_update_glow(False)
        if getattr(self, "_notifier", None) is not None:
            self._notifier.shutdown()
        if getattr(self, "_global_hotkey", None) is not None:
            self._global_hotkey.dispose(QApplication.instance())
        self._voice_engine.shutdown()
        self.account.shutdown()
        if getattr(self, "github", None) is not None:
            self.github.unwire_all()
            self.github.shutdown()
        if getattr(self, "vercel", None) is not None:
            self.vercel.unwire_all()
            self.vercel.shutdown()
        if getattr(self, "jira", None) is not None:
            self.jira.unwire_all()
            self.jira.shutdown()
        if getattr(self, "gitlab", None) is not None:
            self.gitlab.unwire_all()
            self.gitlab.shutdown()
        if getattr(self, "linear", None) is not None:
            self.linear.unwire_all()
            self.linear.shutdown()
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
