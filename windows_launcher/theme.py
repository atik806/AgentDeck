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
           "apply_palette", "manager", "MODES",
           "scheme", "set_scheme", "scheme_labels", "scheme_is_dark_only",
           "DEFAULT_SCHEME"]

MODES = ("light", "dark")

#: The colour scheme shipped as the default -- the one the toolbar / splash /
#: logo already speak. ``config["color_scheme"]`` selects among :data:`_SCHEMES`.
DEFAULT_SCHEME = "catppuccin"

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
    # voice strip (voice_overlay.py) -- a near-black voice-memo recorder;
    # the wave is monochrome grey, the "on air" cue is the red edge.
    "voice_bg": "#16161e",
    "voice_border": "#33333f",
    "voice_border_rec": "#f38ba8",      # the "recording" ring
    "voice_wave": "#b8bcc8",            # brighter grey while listening
    "voice_wave_idle": "#6c7086",       # a quiet grey at rest
    "voice_partial_text": "#a6adc8",    # dim interim transcript
    "voice_text": "#cdd6f4",            # a finished transcript
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
    # voice strip (voice_overlay.py) -- inverted for Latte: a pale card with a
    # monochrome grey wave, red edge for "on air".
    "voice_bg": "#e6e9ef",
    "voice_border": "#bcc0cc",
    "voice_border_rec": "#d20f39",
    "voice_wave": "#5c5f77",
    "voice_wave_idle": "#9ca0b0",
    "voice_partial_text": "#6c6f85",
    "voice_text": "#4c4f69",
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
# Named colour schemes
# ---------------------------------------------------------------------------
#
# Catppuccin (above) is the default and is authored token-by-token. The other
# schemes are given as a compact ~20-value "spec" -- a handful of surface
# layers, foregrounds, one accent, three semantic colours and a 16-slot ANSI
# ramp -- and :func:`_expand` maps that onto the full token table the same way
# every time, so each scheme reads as one considered set rather than a pile of
# hand-picked values. Any token a scheme doesn't produce falls back to the
# Catppuccin table for the active mode (see :func:`color`).
#
# A scheme entry: ``{"label", "dark": <table>, ["light": <table>],
# "ansi_dark": {...}, ["ansi_light": {...}]}``. A scheme with no ``light`` key
# is dark-only -- Light mode shows its dark palette (the Settings picker says
# so).

def _expand(s: dict) -> dict:
    """A full colour-token table from a compact scheme spec.

    Spec keys: ``crust mantle base layer1 surface surface_hi overlay overlay_hi
    fg fg_dim fg_faint accent accent_hi accent_soft accent2 on_accent danger
    warn ok cursor`` (+ optional ``selection``).
    """
    sel = s.get("selection", s["accent"])
    return {
        "window_bg": s["base"],
        "toolbar_bg": s["layer1"],
        "toolbar_border": s["overlay"],
        "surface": s["surface"],
        "surface_hover": s["surface_hi"],
        "surface_pressed": s["overlay_hi"],
        "border": s["overlay"],
        "border_hover": s["overlay_hi"],
        "separator": s["overlay"],
        "text": s["fg"],
        "text_muted": s["fg_dim"],
        "text_faint": s["fg_faint"],
        "accent": s["accent"],
        "accent_hover": s["accent_hi"],
        "accent_text": s["accent"],
        "accent_soft_bg": s["accent_soft"],
        "on_accent": s["on_accent"],
        "accent_2": s["accent2"],
        "pro": s["warn"],
        "danger": s["danger"],
        "danger_hover": s["danger"],
        "activity": s["ok"],
        "sidebar_bg": s["mantle"],
        "sidebar_hover": s["surface"],
        "sidebar_active": s["accent_soft"],
        "sidebar_text": s["fg_dim"],
        "sidebar_heading": s["fg_faint"],
        "sidebar_badge_bg": s["surface"],
        "sidebar_badge_text": s["fg_faint"],
        "card_bg": s["base"],
        "card_raised": s["surface"],
        "card_border": s["overlay"],
        "dialog_text": s["fg"],
        "status_bg": s["layer1"],
        "status_text": s["fg_dim"],
        "menu_bg": s["layer1"],
        "menu_border": s["overlay_hi"],
        "pane_header_bg": s["layer1"],
        "pane_header_bg_active": s["accent_soft"],
        "pane_border": s["overlay"],
        "pane_border_active": s["accent"],
        "pane_border_dead": s["danger"],
        "pane_title": s["fg_dim"],
        "pane_title_dead": s["danger"],
        "splitter": s["crust"],
        "term_bg": s["mantle"],
        "term_fg": s["fg"],
        "term_cursor": s["cursor"],
        "term_selection": sel,
        "voice_bg": s["crust"],
        "voice_border": s["overlay"],
        "voice_border_rec": s["danger"],
        "voice_wave": s["fg_dim"],
        "voice_wave_idle": s["fg_faint"],
        "voice_partial_text": s["fg_dim"],
        "voice_text": s["fg"],
    }


_DRACULA = {
    "crust": "#191a21", "mantle": "#21222c", "base": "#282a36", "layer1": "#2b2d3a",
    "surface": "#343746", "surface_hi": "#44475a", "overlay": "#44475a", "overlay_hi": "#565973",
    "fg": "#f8f8f2", "fg_dim": "#c9cad4", "fg_faint": "#6272a4",
    "accent": "#bd93f9", "accent_hi": "#d6acff", "accent_soft": "#343049", "accent2": "#8be9fd",
    "on_accent": "#282a36", "danger": "#ff5555", "warn": "#ffb86c", "ok": "#50fa7b",
    "cursor": "#f8f8f2",
    "ansi": {
        "black": "#21222c", "red": "#ff5555", "green": "#50fa7b", "yellow": "#f1fa8c",
        "blue": "#bd93f9", "magenta": "#ff79c6", "cyan": "#8be9fd", "white": "#f8f8f2",
        "brightblack": "#6272a4", "brightred": "#ff6e6e", "brightgreen": "#69ff94",
        "brightyellow": "#ffffa5", "brightblue": "#d6acff", "brightmagenta": "#ff92df",
        "brightcyan": "#a4ffff", "brightwhite": "#ffffff",
    },
}

_NORD = {
    "crust": "#242933", "mantle": "#2e3440", "base": "#2e3440", "layer1": "#353c4a",
    "surface": "#3b4252", "surface_hi": "#434c5e", "overlay": "#434c5e", "overlay_hi": "#4c566a",
    "fg": "#d8dee9", "fg_dim": "#abb4c6", "fg_faint": "#7b869c",
    "accent": "#88c0d0", "accent_hi": "#8fbcbb", "accent_soft": "#333f48", "accent2": "#81a1c1",
    "on_accent": "#2e3440", "danger": "#bf616a", "warn": "#ebcb8b", "ok": "#a3be8c",
    "cursor": "#d8dee9",
    "ansi": {
        "black": "#3b4252", "red": "#bf616a", "green": "#a3be8c", "yellow": "#ebcb8b",
        "blue": "#81a1c1", "magenta": "#b48ead", "cyan": "#88c0d0", "white": "#e5e9f0",
        "brightblack": "#4c566a", "brightred": "#bf616a", "brightgreen": "#a3be8c",
        "brightyellow": "#ebcb8b", "brightblue": "#81a1c1", "brightmagenta": "#b48ead",
        "brightcyan": "#8fbcbb", "brightwhite": "#eceff4",
    },
}

_TOKYO_NIGHT = {
    "crust": "#16161e", "mantle": "#1a1b26", "base": "#1a1b26", "layer1": "#1f2335",
    "surface": "#24283b", "surface_hi": "#292e42", "overlay": "#3b4261", "overlay_hi": "#545c7e",
    "fg": "#c0caf5", "fg_dim": "#a9b1d6", "fg_faint": "#565f89",
    "accent": "#7aa2f7", "accent_hi": "#9eb8ff", "accent_soft": "#23283f", "accent2": "#7dcfff",
    "on_accent": "#1a1b26", "danger": "#f7768e", "warn": "#e0af68", "ok": "#9ece6a",
    "cursor": "#c0caf5",
    "ansi": {
        "black": "#15161e", "red": "#f7768e", "green": "#9ece6a", "yellow": "#e0af68",
        "blue": "#7aa2f7", "magenta": "#bb9af7", "cyan": "#7dcfff", "white": "#a9b1d6",
        "brightblack": "#414868", "brightred": "#ff899d", "brightgreen": "#9fe044",
        "brightyellow": "#faba4a", "brightblue": "#8db0ff", "brightmagenta": "#c7a9ff",
        "brightcyan": "#a4daff", "brightwhite": "#c0caf5",
    },
}

_GRUVBOX_DARK = {
    "crust": "#1d2021", "mantle": "#282828", "base": "#282828", "layer1": "#32302f",
    "surface": "#3c3836", "surface_hi": "#504945", "overlay": "#504945", "overlay_hi": "#665c54",
    "fg": "#ebdbb2", "fg_dim": "#bdae93", "fg_faint": "#a89984",
    "accent": "#fabd2f", "accent_hi": "#ffd75f", "accent_soft": "#3c3626", "accent2": "#8ec07c",
    "on_accent": "#282828", "danger": "#fb4934", "warn": "#fe8019", "ok": "#b8bb26",
    "cursor": "#ebdbb2",
    "ansi": {
        "black": "#282828", "red": "#cc241d", "green": "#98971a", "yellow": "#d79921",
        "blue": "#458588", "magenta": "#b16286", "cyan": "#689d6a", "white": "#a89984",
        "brightblack": "#928374", "brightred": "#fb4934", "brightgreen": "#b8bb26",
        "brightyellow": "#fabd2f", "brightblue": "#83a598", "brightmagenta": "#d3869b",
        "brightcyan": "#8ec07c", "brightwhite": "#ebdbb2",
    },
}

_GRUVBOX_LIGHT = {
    "crust": "#d5c4a1", "mantle": "#f2e5bc", "base": "#fbf1c7", "layer1": "#f2e5bc",
    "surface": "#ffffff", "surface_hi": "#ebdbb2", "overlay": "#d5c4a1", "overlay_hi": "#bdae93",
    "fg": "#3c3836", "fg_dim": "#504945", "fg_faint": "#7c6f64",
    "accent": "#b57614", "accent_hi": "#8f5902", "accent_soft": "#f2e0b0", "accent2": "#427b58",
    "on_accent": "#fbf1c7", "danger": "#9d0006", "warn": "#af3a03", "ok": "#79740e",
    "cursor": "#3c3836",
    "ansi": {
        "black": "#7c6f64", "red": "#9d0006", "green": "#79740e", "yellow": "#b57614",
        "blue": "#076678", "magenta": "#8f3f71", "cyan": "#427b58", "white": "#3c3836",
        "brightblack": "#928374", "brightred": "#cc241d", "brightgreen": "#98971a",
        "brightyellow": "#d79921", "brightblue": "#458588", "brightmagenta": "#b16286",
        "brightcyan": "#689d6a", "brightwhite": "#7c6f64",
    },
}

# -- Rosé Pine -------------------------------------------------------------
# Muted rose, gold and pine over a near-black plum. Reads calm at a glance,
# but the iris accent and love/gold semantics keep it lively.
_ROSE_PINE = {
    "crust": "#16141f", "mantle": "#1f1d2e", "base": "#191724", "layer1": "#1c1b2b",
    "surface": "#1f1d2e", "surface_hi": "#26233a", "overlay": "#26233a", "overlay_hi": "#403d52",
    "fg": "#e0def4", "fg_dim": "#908caa", "fg_faint": "#6e6a86",
    "accent": "#c4a7e7", "accent_hi": "#d7c1f2", "accent_soft": "#2a2740", "accent2": "#9ccfd8",
    "on_accent": "#191724", "danger": "#eb6f92", "warn": "#f6c177", "ok": "#31748f",
    "cursor": "#e0def4", "selection": "#403d52",
    "ansi": {
        "black": "#26233a", "red": "#eb6f92", "green": "#31748f", "yellow": "#f6c177",
        "blue": "#9ccfd8", "magenta": "#c4a7e7", "cyan": "#ebbcba", "white": "#e0def4",
        "brightblack": "#6e6a86", "brightred": "#eb6f92", "brightgreen": "#31748f",
        "brightyellow": "#f6c177", "brightblue": "#9ccfd8", "brightmagenta": "#c4a7e7",
        "brightcyan": "#ebbcba", "brightwhite": "#e0def4",
    },
}

_ROSE_PINE_DAWN = {
    "crust": "#f2e9e1", "mantle": "#fffaf3", "base": "#faf4ed", "layer1": "#fffaf3",
    "surface": "#fffaf3", "surface_hi": "#f2e9e1", "overlay": "#dfdad9", "overlay_hi": "#cecacd",
    "fg": "#575279", "fg_dim": "#797593", "fg_faint": "#9893a5",
    "accent": "#907aa9", "accent_hi": "#6c5c86", "accent_soft": "#ece7f2", "accent2": "#56949f",
    "on_accent": "#faf4ed", "danger": "#b4637a", "warn": "#ea9d34", "ok": "#286983",
    "cursor": "#575279", "selection": "#dfdad9",
    "ansi": {
        "black": "#f2e9e1", "red": "#b4637a", "green": "#286983", "yellow": "#ea9d34",
        "blue": "#56949f", "magenta": "#907aa9", "cyan": "#d7827e", "white": "#575279",
        "brightblack": "#9893a5", "brightred": "#b4637a", "brightgreen": "#286983",
        "brightyellow": "#ea9d34", "brightblue": "#56949f", "brightmagenta": "#907aa9",
        "brightcyan": "#d7827e", "brightwhite": "#575279",
    },
}

# -- Synthwave --------------------------------------------------------------
# Deep indigo with neon magenta and cyan -- retro-futurist, high contrast,
# still legible for long sessions.
_SYNTHWAVE = {
    "crust": "#1a1527", "mantle": "#241b2f", "base": "#2a2139", "layer1": "#2f2542",
    "surface": "#37294d", "surface_hi": "#453361", "overlay": "#453361", "overlay_hi": "#5c4685",
    "fg": "#f6f6fb", "fg_dim": "#c7c1e0", "fg_faint": "#8b83b0",
    "accent": "#ff5cc8", "accent_hi": "#ff8ad9", "accent_soft": "#3a2350", "accent2": "#36f9f6",
    "on_accent": "#2a2139", "danger": "#fe4450", "warn": "#ffb000", "ok": "#72f1b8",
    "cursor": "#ff5cc8", "selection": "#463067",
    "ansi": {
        "black": "#241b2f", "red": "#fe4450", "green": "#72f1b8", "yellow": "#fede5d",
        "blue": "#36f9f6", "magenta": "#ff5cc8", "cyan": "#36f9f6", "white": "#f6f6fb",
        "brightblack": "#8b83b0", "brightred": "#fe4450", "brightgreen": "#72f1b8",
        "brightyellow": "#ffb000", "brightblue": "#6ee2ff", "brightmagenta": "#ff8ad9",
        "brightcyan": "#36f9f6", "brightwhite": "#ffffff",
    },
}

# -- Kanagawa -----------------------------------------------------------------
# Sumi-ink darks with a wave-blue accent, after Hokusai. The most restrained
# of the set -- boardroom-safe, still distinctly not grey.
_KANAGAWA = {
    "crust": "#16161d", "mantle": "#1f1f28", "base": "#1f1f28", "layer1": "#16161d",
    "surface": "#2a2a37", "surface_hi": "#363646", "overlay": "#363646", "overlay_hi": "#54546d",
    "fg": "#dcd7ba", "fg_dim": "#c8c093", "fg_faint": "#727169",
    "accent": "#7e9cd8", "accent_hi": "#9db3e6", "accent_soft": "#223249", "accent2": "#7fb4ca",
    "on_accent": "#1f1f28", "danger": "#c34043", "warn": "#ff9e3b", "ok": "#98bb6c",
    "cursor": "#c8c093", "selection": "#2d4f67",
    "ansi": {
        "black": "#090618", "red": "#c34043", "green": "#76946a", "yellow": "#c0a36e",
        "blue": "#7e9cd8", "magenta": "#957fb8", "cyan": "#6a9589", "white": "#c8c093",
        "brightblack": "#727169", "brightred": "#e82424", "brightgreen": "#98bb6c",
        "brightyellow": "#e6c384", "brightblue": "#7fb4ca", "brightmagenta": "#938aa9",
        "brightcyan": "#7aa89f", "brightwhite": "#dcd7ba",
    },
}

# -- One Dark ---------------------------------------------------------------
# The Atom classic every developer recognises -- balanced slate with a clear
# blue accent and warm semantic colours.
_ONE_DARK = {
    "crust": "#1b1f27", "mantle": "#21252b", "base": "#282c34", "layer1": "#2c313a",
    "surface": "#333842", "surface_hi": "#3e4451", "overlay": "#3e4451", "overlay_hi": "#4b5263",
    "fg": "#abb2bf", "fg_dim": "#9098a5", "fg_faint": "#5c6370",
    "accent": "#61afef", "accent_hi": "#8ccbff", "accent_soft": "#22384f", "accent2": "#c678dd",
    "on_accent": "#282c34", "danger": "#e06c75", "warn": "#e5c07b", "ok": "#98c379",
    "cursor": "#528bff", "selection": "#3e4451",
    "ansi": {
        "black": "#282c34", "red": "#e06c75", "green": "#98c379", "yellow": "#e5c07b",
        "blue": "#61afef", "magenta": "#c678dd", "cyan": "#56b6c2", "white": "#abb2bf",
        "brightblack": "#5c6370", "brightred": "#e06c75", "brightgreen": "#98c379",
        "brightyellow": "#e5c07b", "brightblue": "#61afef", "brightmagenta": "#c678dd",
        "brightcyan": "#56b6c2", "brightwhite": "#ffffff",
    },
}

_SCHEMES: "dict[str, dict]" = {
    "catppuccin": {
        "label": "Catppuccin",
        "dark": _DARK, "light": _LIGHT,
        "ansi_dark": _ANSI["dark"], "ansi_light": _ANSI["light"],
    },
    "dracula": {
        "label": "Dracula",
        "dark": _expand(_DRACULA), "ansi_dark": _DRACULA["ansi"],
    },
    "nord": {
        "label": "Nord",
        "dark": _expand(_NORD), "ansi_dark": _NORD["ansi"],
    },
    "tokyonight": {
        "label": "Tokyo Night",
        "dark": _expand(_TOKYO_NIGHT), "ansi_dark": _TOKYO_NIGHT["ansi"],
    },
    "gruvbox": {
        "label": "Gruvbox",
        "dark": _expand(_GRUVBOX_DARK), "light": _expand(_GRUVBOX_LIGHT),
        "ansi_dark": _GRUVBOX_DARK["ansi"], "ansi_light": _GRUVBOX_LIGHT["ansi"],
    },
    "rosepine": {
        "label": "Rosé Pine",
        "dark": _expand(_ROSE_PINE), "light": _expand(_ROSE_PINE_DAWN),
        "ansi_dark": _ROSE_PINE["ansi"], "ansi_light": _ROSE_PINE_DAWN["ansi"],
    },
    "kanagawa": {
        "label": "Kanagawa",
        "dark": _expand(_KANAGAWA), "ansi_dark": _KANAGAWA["ansi"],
    },
    "onedark": {
        "label": "One Dark",
        "dark": _expand(_ONE_DARK), "ansi_dark": _ONE_DARK["ansi"],
    },
    "synthwave": {
        "label": "Synthwave",
        "dark": _expand(_SYNTHWAVE), "ansi_dark": _SYNTHWAVE["ansi"],
    },
}


def _scheme_entry(key: Optional[str] = None) -> dict:
    return _SCHEMES.get(key or _scheme) or _SCHEMES[DEFAULT_SCHEME]


def _scheme_table(key: Optional[str], m: str) -> dict:
    entry = _scheme_entry(key)
    return entry.get(m) or entry.get("dark") or _DARK


def _scheme_ansi(key: Optional[str], m: str) -> dict:
    entry = _scheme_entry(key)
    return entry.get(f"ansi_{m}") or entry.get("ansi_dark") or _ANSI["dark"]


def scheme() -> str:
    return _scheme


def scheme_labels() -> "list[tuple[str, str]]":
    """``[(key, label)]`` in display order -- the Settings scheme picker."""
    return [(k, v["label"]) for k, v in _SCHEMES.items()]


def scheme_is_dark_only(key: str) -> bool:
    return "light" not in (_SCHEMES.get(key) or {})


def set_scheme(new_scheme: str) -> None:
    """Switch the colour scheme and notify (repaint) if it actually changed."""
    global _scheme
    new_scheme = str(new_scheme or "").strip().lower()
    if new_scheme not in _SCHEMES:
        new_scheme = DEFAULT_SCHEME
    if new_scheme == _scheme:
        return
    _scheme = new_scheme
    manager().changed.emit(_mode)


# ---------------------------------------------------------------------------
# State + hub
# ---------------------------------------------------------------------------

class _Manager(QObject):
    #: Emitted after :func:`set_mode` actually changes the mode. Carries the
    #: new mode string ("light" / "dark").
    changed = Signal(str)


_manager: Optional[_Manager] = None
_mode = "dark"
_scheme = DEFAULT_SCHEME


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
    """Resolve ``config['theme']`` to a concrete mode + read ``color_scheme``.
    Idempotent; returns the resolved mode."""
    global _mode, _scheme
    pref = "system"
    sch = DEFAULT_SCHEME
    if isinstance(config, dict):
        pref = str(config.get("theme", "system") or "system").strip().lower()
        sch = str(config.get("color_scheme", DEFAULT_SCHEME) or DEFAULT_SCHEME).strip().lower()
    _mode = _detect_system() if pref not in MODES else pref
    _scheme = sch if sch in _SCHEMES else DEFAULT_SCHEME
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
    m = mode_override or _mode
    # scheme table -> Catppuccin base for this mode -> Catppuccin dark -> magenta
    table = _scheme_table(None, m)
    base = _TABLE.get(m, _DARK)
    return table.get(token) or base.get(token) or _DARK.get(token, "#ff00ff")


def qcolor(token: str, mode_override: Optional[str] = None) -> QColor:
    return QColor(color(token, mode_override))


def ansi(mode_override: Optional[str] = None) -> dict:
    m = mode_override or _mode
    return {**_ANSI.get(m, _ANSI["dark"]), **_scheme_ansi(None, m)}


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
