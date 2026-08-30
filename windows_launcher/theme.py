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

_DARK = {
    "window_bg": "#0c0c0c",
    "toolbar_bg": "#1a1a1a",
    "toolbar_border": "#2b2b2b",
    "surface": "#272727",
    "surface_hover": "#333333",
    "surface_pressed": "#3a3a3a",
    "border": "#3b3b3b",
    "border_hover": "#4f4f4f",
    "separator": "#323232",
    "text": "#e4e4e4",
    "text_muted": "#8f8f8f",
    "text_faint": "#6e6e6e",
    "accent": "#3b78ff",
    "accent_hover": "#5590ff",
    "accent_text": "#d3e2ff",
    "accent_soft_bg": "#223049",
    "on_accent": "#ffffff",
    "pro": "#e3b341",
    "danger": "#e0666b",
    "danger_hover": "#c02020",
    # sidebar
    "sidebar_bg": "#141414",
    "sidebar_hover": "#1f1f1f",
    "sidebar_active": "#23232b",
    "sidebar_text": "#b7b7b7",
    "sidebar_heading": "#8a8a8a",
    "sidebar_badge_bg": "#2b2b2b",
    "sidebar_badge_text": "#9a9a9a",
    # cards / dialogs
    "card_bg": "#1b1b1b",
    "card_raised": "#242424",
    "card_border": "#363636",
    "dialog_text": "#e6e6e6",
    # status bar
    "status_bg": "#1b1b1b",
    "status_text": "#9a9a9a",
    # menus
    "menu_bg": "#232323",
    "menu_border": "#3b3b3b",
    # pane chrome
    "pane_header_bg": "#171717",
    "pane_header_bg_active": "#1f1f1f",
    "pane_border": "#2b2b2b",
    "pane_border_active": "#3b78ff",
    "pane_border_dead": "#a03030",
    "pane_title": "#b0b0b0",
    "pane_title_dead": "#d08080",
    "splitter": "#101010",
    # terminal
    "term_bg": "#0c0c0c",
    "term_fg": "#cccccc",
    "term_cursor": "#cccccc",
    "term_selection": "#3a6ea5",       # blended with alpha at use sites
}

_LIGHT = {
    "window_bg": "#ffffff",
    "toolbar_bg": "#f3f3f5",
    "toolbar_border": "#dcdce1",
    "surface": "#ffffff",
    "surface_hover": "#ececef",
    "surface_pressed": "#e0e0e4",
    "border": "#cfcfd6",
    "border_hover": "#b2b2bd",
    "separator": "#e2e2e6",
    "text": "#1f2023",
    "text_muted": "#68686f",
    "text_faint": "#9a9aa3",
    "accent": "#2f6fed",
    "accent_hover": "#1f5cd6",
    "accent_text": "#1b4bb8",
    "accent_soft_bg": "#e4edfd",
    "on_accent": "#ffffff",
    "pro": "#9a6b00",
    "danger": "#c8342f",
    "danger_hover": "#b02722",
    "sidebar_bg": "#f0f0f2",
    "sidebar_hover": "#e5e5e9",
    "sidebar_active": "#dde6fb",
    "sidebar_text": "#4a4a52",
    "sidebar_heading": "#7a7a83",
    "sidebar_badge_bg": "#dedee3",
    "sidebar_badge_text": "#5c5c64",
    "card_bg": "#f7f7f8",
    "card_raised": "#ffffff",
    "card_border": "#d7d7dd",
    "dialog_text": "#1f2023",
    "status_bg": "#f3f3f5",
    "status_text": "#68686f",
    "menu_bg": "#ffffff",
    "menu_border": "#cfcfd6",
    "pane_header_bg": "#ededf0",
    "pane_header_bg_active": "#e2e9fb",
    "pane_border": "#d7d7dd",
    "pane_border_active": "#2f6fed",
    "pane_border_dead": "#c8342f",
    "pane_title": "#5c5c64",
    "pane_title_dead": "#b02722",
    "splitter": "#d7d7dd",
    "term_bg": "#ffffff",
    "term_fg": "#2b2b2b",
    "term_cursor": "#2b2b2b",
    "term_selection": "#2f6fed",
}

#: 16 ANSI slots per mode. Dark = Windows Terminal "Campbell" (unchanged from the
#: original Palette). Light = GitHub-light, dark enough to read on white.
_ANSI = {
    "dark": {
        "black": "#0c0c0c", "red": "#c50f1f", "green": "#13a10e", "yellow": "#c19c00",
        "blue": "#0037da", "magenta": "#881798", "cyan": "#3a96dd", "white": "#cccccc",
        "brightblack": "#767676", "brightred": "#e74856", "brightgreen": "#16c60c",
        "brightyellow": "#f9f1a5", "brightblue": "#3b78ff", "brightmagenta": "#b4009e",
        "brightcyan": "#61d6d6", "brightwhite": "#f2f2f2",
    },
    "light": {
        "black": "#24292e", "red": "#cf222e", "green": "#116329", "yellow": "#7d4e00",
        "blue": "#0969da", "magenta": "#8250df", "cyan": "#1b7c83", "white": "#6e7781",
        "brightblack": "#57606a", "brightred": "#a40e26", "brightgreen": "#1a7f37",
        "brightyellow": "#633c01", "brightblue": "#218bff", "brightmagenta": "#a475f9",
        "brightcyan": "#3192aa", "brightwhite": "#8c959f",
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
