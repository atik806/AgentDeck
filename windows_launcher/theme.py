"""App-wide light / dark theming.

One place that owns the colour tokens every surface reads, plus a tiny
``QObject`` hub so widgets can repaint when the mode changes.

Usage:

* ``theme.init(config)`` once at startup -- resolves ``config["theme"]``
  (``"system" | "light" | "dark"``) to a concrete mode.
* ``theme.color("toolbar_bg")`` -- a hex string for the current mode.
* ``theme.manager().changed`` -- a signal (carries the new mode string);
  ``terminal_panel`` fans it out to the sidebar / panes / dialogs.
* ``theme.set_mode("light")`` -- flip and notify.

Nothing here imports the widgets it themes; callers pull tokens.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette

__all__ = ["init", "mode", "set_mode", "toggle", "color", "qcolor", "ansi",
           "apply_palette", "manager", "MODES"]

MODES = ("light", "dark")

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

# Catppuccin Mocha. The launch splash and the logo already speak this
# language (layered indigo, a blue->teal accent); these tokens carry it
# through the toolbar, sidebar, panes and dialogs. Surfaces layer
# crust -> mantle -> base -> raised so the terminal reads as content and the
# chrome recedes.
_DARK = {
    "window_bg": "#1e1e2e",
    "toolbar_bg": "#262637",
    "toolbar_border": "#363649",
    "surface": "#2b2b3f",
    "surface_hover": "#363649",
    "surface_pressed": "#45455e",
    "border": "#363649",
    "border_hover": "#45455e",
    "separator": "#363649",
    "text": "#cdd6f4",
    "text_muted": "#a6adc8",
    "text_faint": "#7f849c",
    "accent": "#89b4fa",
    "accent_hover": "#a6c8ff",
    "accent_text": "#b4befe",
    "accent_soft_bg": "#2c3050",
    "on_accent": "#1e1e2e",
    # second accent -- the blue->teal sweep from the logo / splash
    "accent_2": "#94e2d5",
    "pro": "#fab387",
    "danger": "#f38ba8",
    # a filled-red hover; kept a pastel so the dark ``on_accent`` glyph reads
    "danger_hover": "#f38ba8",
    # the sidebar "an agent is working here" glow dot
    "activity": "#a6e3a1",
    # sidebar
    "sidebar_bg": "#181825",
    "sidebar_hover": "#232333",
    "sidebar_active": "#2c3050",
    "sidebar_text": "#a6adc8",
    "sidebar_heading": "#7f849c",
    "sidebar_badge_bg": "#2b2b3f",
    "sidebar_badge_text": "#9399b2",
    # cards / dialogs
    "card_bg": "#1e1e2e",
    "card_raised": "#2b2b3f",
    "card_border": "#363649",
    "dialog_text": "#cdd6f4",
    # status bar
    "status_bg": "#262637",
    "status_text": "#9399b2",
    # menus
    "menu_bg": "#262637",
    "menu_border": "#45455e",
    # pane chrome
    "pane_header_bg": "#262637",
    "pane_header_bg_active": "#2c3050",
    "pane_border": "#363649",
    "pane_border_active": "#89b4fa",
    "pane_border_dead": "#f38ba8",
    "pane_title": "#a6adc8",
    "pane_title_dead": "#f38ba8",
    "splitter": "#11111b",
    # terminal
    "term_bg": "#181825",
    "term_fg": "#cdd6f4",
    "term_cursor": "#f5e0dc",
    "term_selection": "#89b4fa",        # blended with alpha at use sites
}

# Catppuccin Latte -- the same identity mapped for light mode, so it reads as
# a considered set of surfaces rather than a greyscale fallback.
_LIGHT = {
    "window_bg": "#eff1f5",
    "toolbar_bg": "#e6e9ef",
    "toolbar_border": "#ccd0da",
    "surface": "#ffffff",
    "surface_hover": "#e6e9ef",
    "surface_pressed": "#dce0e8",
    "border": "#ccd0da",
    "border_hover": "#bcc0cc",
    "separator": "#dce0e8",
    "text": "#4c4f69",
    "text_muted": "#5c5f77",
    "text_faint": "#8c8fa1",
    "accent": "#1e66f5",
    "accent_hover": "#1552d0",
    "accent_text": "#1e66f5",
    "accent_soft_bg": "#dce7fd",
    "on_accent": "#ffffff",
    "accent_2": "#179299",
    "pro": "#8a6d00",
    "danger": "#d20f39",
    "danger_hover": "#b00c30",
    "activity": "#40a02b",
    "sidebar_bg": "#dce0e8",
    "sidebar_hover": "#ccd0da",
    "sidebar_active": "#dce7fd",
    "sidebar_text": "#5c5f77",
    "sidebar_heading": "#7c7f93",
    "sidebar_badge_bg": "#ccd0da",
    "sidebar_badge_text": "#5c5f77",
    "card_bg": "#e6e9ef",
    "card_raised": "#ffffff",
    "card_border": "#ccd0da",
    "dialog_text": "#4c4f69",
    "status_bg": "#e6e9ef",
    "status_text": "#5c5f77",
    "menu_bg": "#ffffff",
    "menu_border": "#ccd0da",
    "pane_header_bg": "#e6e9ef",
    "pane_header_bg_active": "#dce7fd",
    "pane_border": "#ccd0da",
    "pane_border_active": "#1e66f5",
    "pane_border_dead": "#d20f39",
    "pane_title": "#5c5f77",
    "pane_title_dead": "#d20f39",
    "splitter": "#ccd0da",
    "term_bg": "#ffffff",
    "term_fg": "#4c4f69",
    "term_cursor": "#dc8a78",
    "term_selection": "#1e66f5",
}

#: 16 ANSI slots per mode. Dark = Catppuccin Mocha; Light = Catppuccin Latte
#: (its brights nudged darker so they stay legible on a light ground). Bold
#: text brightens the eight base foregrounds -- see ``Palette.resolve``.
_ANSI = {
    "dark": {
        "black": "#45475a", "red": "#f38ba8", "green": "#a6e3a1", "yellow": "#f9e2af",
        "blue": "#89b4fa", "magenta": "#cba6f7", "cyan": "#94e2d5", "white": "#bac2de",
        "brightblack": "#585b70", "brightred": "#f5a0b5", "brightgreen": "#bce0b8",
        "brightyellow": "#fbe9c4", "brightblue": "#a8c7ff", "brightmagenta": "#dcc1fb",
        "brightcyan": "#b2ebe1", "brightwhite": "#cdd6f4",
    },
    "light": {
        "black": "#5c5f77", "red": "#d20f39", "green": "#40a02b", "yellow": "#c47d19",
        "blue": "#1e66f5", "magenta": "#8839ef", "cyan": "#179299", "white": "#acb0be",
        "brightblack": "#6c6f85", "brightred": "#b60c30", "brightgreen": "#388a25",
        "brightyellow": "#a86e15", "brightblue": "#1a5ad9", "brightmagenta": "#7a33d4",
        "brightcyan": "#147e86", "brightwhite": "#8c8fa1",
    },
}

_TABLE = {"dark": _DARK, "light": _LIGHT}


# ---------------------------------------------------------------------------
# State + hub
# ---------------------------------------------------------------------------

class _Manager(QObject):
    #: Emitted after :func:`set_mode` actually changes the mode. Carries the
    #: new mode string ("light" / "dark").
    changed = Signal(str)


_manager: Optional[_Manager] = None
_mode = "dark"


def manager() -> _Manager:
    global _manager
    if _manager is None:
        _manager = _Manager()
    return _manager


def _detect_system() -> str:
    """Best-effort OS preference; falls back to dark."""
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        hints = app.styleHints() if app is not None else None
        scheme = getattr(hints, "colorScheme", None)
        if callable(scheme):
            from PySide6.QtCore import Qt

            if scheme() == Qt.ColorScheme.Light:
                return "light"
            if scheme() == Qt.ColorScheme.Dark:
                return "dark"
    except Exception:  # noqa: BLE001 - detection is optional
        pass
    return "dark"


def init(config: Optional[dict] = None) -> str:
    """Resolve ``config['theme']`` to a concrete mode and store it. Idempotent."""
    global _mode
    pref = "system"
    if isinstance(config, dict):
        pref = str(config.get("theme", "system") or "system").strip().lower()
    _mode = _detect_system() if pref not in MODES else pref
    return _mode


def mode() -> str:
    return _mode


def set_mode(new_mode: str) -> None:
    global _mode
    new_mode = "light" if str(new_mode).strip().lower() == "light" else "dark"
    if new_mode == _mode:
        return
    _mode = new_mode
    manager().changed.emit(_mode)


def toggle() -> str:
    set_mode("light" if _mode == "dark" else "dark")
    return _mode


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def color(token: str, mode_override: Optional[str] = None) -> str:
    table = _TABLE.get(mode_override or _mode, _DARK)
    return table.get(token) or _DARK.get(token, "#ff00ff")


def qcolor(token: str, mode_override: Optional[str] = None) -> QColor:
    return QColor(color(token, mode_override))


def ansi(mode_override: Optional[str] = None) -> dict:
    return dict(_ANSI.get(mode_override or _mode, _ANSI["dark"]))


def apply_palette(app) -> None:
    """Set a matching ``QPalette`` so native controls (menus, scrollbars,
    tooltips, dialog frames we don't fully style) follow the theme."""
    if app is None:
        return
    pal = QPalette()
    bg = qcolor("window_bg")
    surface = qcolor("surface")
    text = qcolor("text")
    pal.setColor(QPalette.Window, bg)
    pal.setColor(QPalette.WindowText, text)
    pal.setColor(QPalette.Base, surface)
    pal.setColor(QPalette.AlternateBase, qcolor("card_raised"))
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.Button, qcolor("surface"))
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.ToolTipBase, qcolor("menu_bg"))
    pal.setColor(QPalette.ToolTipText, text)
    pal.setColor(QPalette.Highlight, qcolor("accent"))
    pal.setColor(QPalette.HighlightedText, qcolor("on_accent"))
    pal.setColor(QPalette.PlaceholderText, qcolor("text_faint"))
    pal.setColor(QPalette.Link, qcolor("accent"))
    disabled = qcolor("text_faint")
    for grp in (QPalette.Disabled,):
        pal.setColor(grp, QPalette.WindowText, disabled)
        pal.setColor(grp, QPalette.Text, disabled)
        pal.setColor(grp, QPalette.ButtonText, disabled)
    app.setPalette(pal)
