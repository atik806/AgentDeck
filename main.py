#!/usr/bin/env python3
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from ui.window import MultiTerminalWindow


class MultiTerminalApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.multi-terminal.launcher",
            flags=0,
        )

    def do_activate(self):
        win = MultiTerminalWindow(self)
        win.present()


def main():
    app = MultiTerminalApp()
    exit_code = app.run(sys.argv)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
