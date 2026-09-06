"""Mouse routing: local text-selection vs. clicks forwarded to a TUI.

Regression test for "I can't select or copy text in a pane running Claude
Code". A program that reads the mouse (Claude Code, fzf, a TUI picker) sets a
tracking mode, and every left click was then forwarded to it -- so a
drag-select never started and there was nothing to copy.

The fix defers the decision: a press that stays put is a click for the
program, a press that turns into a drag is a local selection (Shift still
forces local selection outright, as it does for the wheel).

Run:  .venv\\Scripts\\python.exe test_mouse_select.py
"""
import sys

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from terminal_view import TerminalCanvas, preferred_font
from vt_screen import TerminalScreen, TerminalStream

app = QApplication.instance() or QApplication(sys.argv)
fails = []
DRAG = QGuiApplication.styleHints().startDragDistance()


def check(name, got, want=True):
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
    canvas._cell_w, canvas._cell_h = 8.0, 16.0
    sent: list[str] = []
    canvas.input_requested.connect(sent.append)
    return screen, stream, canvas, sent


def press(canvas, x, y, mods=Qt.NoModifier, button=Qt.LeftButton):
    canvas.mousePressEvent(QMouseEvent(
        QEvent.MouseButtonPress, QPointF(x, y), QPointF(x, y),
        button, button, mods))


def move(canvas, x, y, mods=Qt.NoModifier):
    canvas.mouseMoveEvent(QMouseEvent(
        QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
        Qt.NoButton, Qt.LeftButton, mods))


def release(canvas, x, y, mods=Qt.NoModifier, button=Qt.LeftButton):
    canvas.mouseReleaseEvent(QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(x, y), QPointF(x, y),
        button, Qt.NoButton, mods))


print("== 1. no mouse tracking: drag selects text locally, sends nothing ==")
screen, stream, canvas, sent = make()
stream.feed("hello world\r\n")
press(canvas, 0, 0)
move(canvas, 11 * 8, 4)
release(canvas, 11 * 8, 4)
check("selection is active", canvas.has_selection(), True)
check("selected text", canvas.selected_text(), "hello world")
check("nothing forwarded to the pty", sent, [])

print("== 2. mouse tracking: a stationary click is forwarded to the program ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1000h\x1b[?1006h")
press(canvas, 40, 40)
check("press alone forwards nothing (decision deferred)", sent, [])
release(canvas, 40, 40)
check("release forwards the press+release pair (SGR)", sent, ["\x1b[<0;6;3M\x1b[<0;6;3m"])
check("no local selection", canvas.has_selection(), False)

print("== 3. mouse tracking: a drag still selects text locally ==")
screen, stream, canvas, sent = make()
stream.feed("hello world\r\n")
stream.feed("\x1b[?1000h\x1b[?1006h")
press(canvas, 0, 0)
move(canvas, DRAG + 4, 0)
check("drag past the threshold starts a selection", canvas._selecting, True)
move(canvas, 11 * 8, 4)
release(canvas, 11 * 8, 4)
check("selected text", canvas.selected_text(), "hello world")
check("nothing forwarded -- not the press, not a stray release", sent, [])

print("== 4. mouse tracking: a sub-threshold wobble is still a click ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1000h\x1b[?1006h")
press(canvas, 40, 40)
move(canvas, 42, 41)
check("tiny move does not start a selection", canvas._selecting, False)
release(canvas, 42, 41)
check("still forwarded as a click", sent, ["\x1b[<0;6;3M\x1b[<0;6;3m"])

print("== 5. mouse tracking + Shift: immediate local selection, no reports ==")
screen, stream, canvas, sent = make()
stream.feed("hello world\r\n")
stream.feed("\x1b[?1000h\x1b[?1006h")
press(canvas, 0, 0, mods=Qt.ShiftModifier)
check("selecting right away, no deferral", canvas._selecting, True)
move(canvas, 11 * 8, 4, mods=Qt.ShiftModifier)
release(canvas, 11 * 8, 4, mods=Qt.ShiftModifier)
check("selected text", canvas.selected_text(), "hello world")
check("nothing forwarded", sent, [])

print("== 6. mouse tracking: middle/right buttons still forward ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1000h\x1b[?1006h")
press(canvas, 40, 40, button=Qt.RightButton)
release(canvas, 40, 40, button=Qt.RightButton)
check("right-click press+release forwarded", sent, ["\x1b[<2;6;3M", "\x1b[<2;6;3m"])

print("== 7. a release whose press we never forwarded is not sent alone ==")
screen, stream, canvas, sent = make()
stream.feed("\x1b[?1000h\x1b[?1006h")
# Press with Shift (local), release without Shift -- must not leak a report.
press(canvas, 10, 10, mods=Qt.ShiftModifier)
release(canvas, 10, 10)
check("no unpaired release report", sent, [])

print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("all mouse-selection routing tests passed")
