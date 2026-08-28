"""UI selection: the GTK4/Adwaita window where available, Tkinter otherwise."""

from __future__ import annotations

import importlib.util

__all__ = ["gtk_available", "tk_available", "pick_ui", "run_ui"]


def gtk_available() -> bool:
    """Whether PyGObject with GTK4 and libadwaita can actually be imported."""
    if importlib.util.find_spec("gi") is None:
        return False
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
    except (ImportError, ValueError, AttributeError):
        return False
    return True


def tk_available() -> bool:
    if importlib.util.find_spec("tkinter") is None:
        return False
    try:
        import tkinter  # noqa: F401
        from tkinter import ttk  # noqa: F401
    except ImportError:
        return False
    return True


def pick_ui(prefer: str = "auto") -> str:
    """Return ``"gtk"``, ``"tk"``, or raise if neither toolkit is usable.

    ``prefer`` may be ``"gtk"``/``"tk"`` to force one; it still falls back if
    that toolkit isn't importable.
    """
    if prefer == "gtk" and gtk_available():
        return "gtk"
    if prefer == "tk" and tk_available():
        return "tk"
    if gtk_available():
        return "gtk"
    if tk_available():
        return "tk"
    raise RuntimeError(
        "No usable GUI toolkit found.\n"
        "  - Linux: install PyGObject, GTK4 and libadwaita "
        "(pip install -r requirements-linux.txt), or the python3-tk package.\n"
        "  - Windows: tkinter ships with python.org builds; reinstall Python "
        "with the 'tcl/tk and IDLE' option enabled.\n"
        "Or run headless with --cli."
    )


def run_ui(prefer: str = "auto") -> int:
    """Start the chosen UI and block until it closes."""
    toolkit = pick_ui(prefer)
    if toolkit == "gtk":
        from ui.gtk_app import run as run_gtk

        return run_gtk()
    from ui.tk_window import run as run_tk

    return run_tk()
