"""Colours shared by the GTK stylesheet and the Tk widgets.

The values come from the CSS in :mod:`ui.window`, so the Tk fallback looks like
the Adwaita UI rather than like default grey ``ttk``.
"""

from __future__ import annotations

PALETTE = {
    "bg": "#16161a",
    "bg_alt": "#0d0d0f",
    "panel": "#0a0a0c",
    "preview_bg": "#1e1e28",
    "border": "#2a2a32",
    "border_dim": "#1a1a1e",
    "fg": "#cdd6f4",
    "fg_dim": "#a6adc8",
    "fg_faint": "#585b6e",
    "accent": "#3584e4",
    "accent_hover": "#4a90e8",
    "running": "#a6e3a1",
    "stopped": "#45475a",
    "danger": "#f38ba8",
    "hover": "#2a2a32",
}

MONO_FONTS = (
    "Cascadia Mono",
    "JetBrains Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Courier New",
)

UI_FONTS = ("Segoe UI", "Ubuntu", "DejaVu Sans", "Helvetica")


def first_available_font(candidates, families) -> str:
    """First of ``candidates`` present in ``families``, else the last candidate."""
    installed = {name.lower() for name in families}
    for name in candidates:
        if name.lower() in installed:
            return name
    return candidates[-1]
