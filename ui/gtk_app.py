"""The GTK4/libadwaita application wrapper.

Kept separate from ``main.py`` so that importing the entry point doesn't require
PyGObject — on Windows it isn't installed and this module is never imported.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from ui.window import MultiTerminalWindow

APP_ID = "com.multi-terminal.launcher"


class MultiTerminalApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=0)

    def do_activate(self):
        window = self.props.active_window
        if not window:
            window = MultiTerminalWindow(self)
        window.present()


def run(argv: list[str] | None = None) -> int:
    app = MultiTerminalApp()
    # Our own flags are already parsed; don't let GApplication see them.
    return app.run([])
