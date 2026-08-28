"""Mouse-wheel routing: local scrollback vs. alternate-scroll vs. mouse reports.

Regression test for "the wheel does nothing while a full-screen program (a
pager, vim, Claude Code's renderer) is running": on the alternate screen there
is no scrollback to move through, so the wheel has to be forwarded to the
program instead -- as mouse events if it asked for them, otherwise as cursor
keys ("alternate scroll mode"), which is what xterm and Windows Terminal do.

Run:  .venv\\Scripts\\python.exe test_wheel.py
"""
import sys

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from terminal_view import TerminalCanvas, preferred_font
from vt_screen import TerminalScreen, TerminalStream

app = QApplication.instance() or QApplication(sys.argv)
fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got  = {got!r}")
        print(f"        want = {want!r}")
        fails.append(name)


def make():
    screen = TerminalScreen(80, 24, scrollback=1000)
    stream = TerminalStream(screen)
    canvas = TerminalCanvas(screen, preferred_font(11))
    # Pin cell geometry so coordinate maths is predictable (no real paint here).
    canvas._cell_w, canvas._cell_h = 8.0, 16.0
    sent: list[str] = []
    canvas.input_requested.connect(sent.append)
    return screen, stream, canvas, sent


def wheel(canvas, dy, mods=Qt.NoModifier, x=40, y=40):
    pos = QPointF(x, y)
    ev = QWheelEvent(
        pos, pos, QPoint(0, 0), QPoint(0, dy),
        Qt.NoButton, mods, Qt.NoScrollPhase, False,
    )
    canvas.wheelEvent(ev)


print("== 1. primary screen: wheel moves our scrollback, sends nothing ==")
screen, stream, canvas, sent = make()
stream.feed("line\r\n" * 40)          # ~17 lines of scrollback at 24 rows
canvas.scroll_to_bottom()
bottom = canvas.scroll_top()
wheel(canvas, 120)                     # wheel up
check("scrolled up locally", canvas.scroll_top() < bottom, True)
check("nothing sent to the pty", sent, [])
wheel(canvas, -120)                    # wheel down
check("back to the bottom", canvas.scroll_top(), bottom)

print("== 2. alternate screen: wheel becomes cursor keys ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1049h")
check("alt screen active", screen.alternate_screen, True)
wheel(canvas, 120)
check("wheel up -> 3x Up", sent, ["\x1b[A" * 3])
sent.clear()
wheel(canvas, -240)                    # two notches down
check("wheel down x2 -> 6x Down", sent, ["\x1b[B" * 6])

print("== 3. alternate screen + DECCKM: SS3 cursor keys ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1049h\x1b[?1h")
wheel(canvas, 120)
check("wheel up -> 3x SS3 Up", sent, ["\x1bOA" * 3])

print("== 4. mouse tracking + SGR: wheel reported as button 64/65 ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1000h\x1b[?1006h")
wheel(canvas, 120, x=40, y=40)         # col 40//8+1=6, row 40//16+1=3
check("SGR wheel-up report", sent, ["\x1b[<64;6;3M"])
sent.clear()
wheel(canvas, -120, x=40, y=40)
check("SGR wheel-down report", sent, ["\x1b[<65;6;3M"])

print("== 5. mouse tracking wins even on the alternate screen ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1049h\x1b[?1000h\x1b[?1006h")
wheel(canvas, 120)
check("reports mouse, not arrows", sent, ["\x1b[<64;6;3M"])

print("== 6. legacy mouse encoding (no 1006) ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1000h")
wheel(canvas, 120, x=40, y=40)
check("legacy wheel-up report", sent,
      ["\x1b[M" + chr(32 + 64) + chr(32 + 6) + chr(32 + 3)])

print("== 7. Shift opts out of mouse reports ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1049h\x1b[?1000h\x1b[?1006h")
wheel(canvas, 120, mods=Qt.ShiftModifier)
check("Shift+wheel on alt screen -> arrows, not a mouse report", sent, ["\x1b[A" * 3])

screen, stream, canvas, sent = make()
stream.feed("line\r\n" * 40)
stream.feed("\x1b[?1000h\x1b[?1006h")   # tracking on, primary screen
canvas.scroll_to_bottom()
bottom = canvas.scroll_top()
wheel(canvas, 120, mods=Qt.ShiftModifier)
check("Shift+wheel on primary -> local scroll, no report", (sent, canvas.scroll_top() < bottom), ([], True))

print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("all wheel routing tests passed")
