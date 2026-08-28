"""Scratch probe: does the panel actually work with no console attached?

Run under pythonw.exe. Writes findings to _probe.txt and a screenshot to
_probe.png, because there is no stdout to print to -- which is the whole point.
"""
import ctypes
import os
import sys
import traceback

for _name, _mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
    if getattr(sys, _name, None) is None:
        setattr(sys, _name, open(os.devnull, _mode))

HERE = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(HERE, "_probe.txt"), "w", encoding="utf-8")


def say(*a):
    print(*a, file=out)
    out.flush()


try:
    say("raw sys.stdout was None :", "__original__" not in dir(sys))
    say("GetConsoleWindow()      :", ctypes.windll.kernel32.GetConsoleWindow())

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from terminal_panel import TerminalPanel

    app = QApplication(sys.argv)
    panel = TerminalPanel(
        {"default_count": 3, "default_shell": "auto", "font_size": 11, "layout": "grid"}
    )
    panel.resize(1200, 700)
    panel.show()

    def send():
        panel._panes[1].view.session.write("echo PTY_ROUNDTRIP_OK\r\n")

    def finish():
        say("panes                   :", len(panel._panes))
        say("visible                 :", [p.isVisible() for p in panel._panes])
        say("alive                   :", [p.is_alive() for p in panel._panes])
        say("pty sizes               :",
            [(p.view.session.rows, p.view.session.cols) for p in panel._panes])
        for i, p in enumerate(panel._panes):
            body = "\n".join(l.rstrip() for l in p.view._screen.display).strip()
            say(f"pane {i} ({len(body)} chars)      : {body[:200]!r}")
        say("pty round-trip          :",
            "PTY_ROUNDTRIP_OK" in "\n".join(panel._panes[1].view._screen.display))
        panel.grab().save(os.path.join(HERE, "_probe.png"))
        say("screenshot              : saved")
        os._exit(0)

    QTimer.singleShot(4000, send)
    QTimer.singleShot(9000, finish)
    app.exec()
except Exception:
    say(traceback.format_exc())
    os._exit(1)
