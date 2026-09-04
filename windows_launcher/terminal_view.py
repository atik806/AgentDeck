"""The terminal widget: paints the screen, and turns key presses into VT input.

Two halves, and the split matters:

* :class:`TerminalCanvas` owns the grid. It paints cells, tracks selection, and
  translates keyboard and mouse events into the byte sequences a shell expects.
* :class:`TerminalView` wraps the canvas with a scrollbar and owns the pty
  session, so the panel above only ever deals with one object per pane.

Output is buffered and applied on a timer rather than parsed inline. A command
like ``dir /s`` can emit megabytes in a burst; feeding and repainting per chunk
would spend the whole frame budget on frames nobody sees. Coalescing to ~60 Hz
keeps the UI responsive no matter how loud the shell gets.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QDir, QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QKeyEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QScrollBar,
    QSizePolicy,
    QWidget,
)

import perf
import theme
from pty_backend import DEFAULT_SHELL, PtySession
from vt_screen import DEFAULT_SCROLLBACK, Palette, TerminalScreen, TerminalStream

__all__ = ["TerminalView", "TerminalCanvas", "preferred_font"]


#: Repaint budget. 16 ms is one frame at 60 Hz.
_FRAME_MS = 16

#: How long to wait for the geometry to settle before resizing the pty. Long
#: enough to swallow the intermediate sizes a splitter rebuild walks through,
#: short enough that it is not perceptible when dragging a splitter.
_RESIZE_COALESCE_MS = 40

#: How often to check that a full-screen program which switched to the
#: alternate screen is still running. Only armed while the alternate screen is
#: up; the point is to notice a crash/kill that never sent the ``1049l`` that
#: would have restored the primary buffer and its scrollback.
_ALT_WATCH_MS = 2000

#: The child-process probe (a full Toolhelp snapshot walk) is skipped while the
#: program is visibly alive -- any pty output within this window means it is
#: still running, so there is nothing to recover and no need to pay for the walk.
#: A program sitting idle on the alternate screen (a paused pager) goes quiet,
#: and only then does the watchdog actually probe.
_ALT_QUIET_S = 3.0

#: How long after the last burst of pty output a pane still counts as "busy".
#: An agent that is thinking or running a tool paints continuously (a spinner,
#: streaming text); once it is back at its prompt waiting for the user the pty
#: goes silent. The sidebar's activity dot reads :meth:`TerminalView.is_busy`
#: off this -- long enough to bridge the gap between spinner frames, short
#: enough that the glow dies promptly when the agent stops.
_BUSY_WINDOW_S = 2.5

#: pyte stores private modes shifted left by 5 (see pyte.Screen.set_mode).
_MODE_BRACKETED_PASTE = 2004 << 5

#: Mouse-reporting DEC private modes, shifted the same way. A program that has
#: set any of these is reading the mouse itself: X10 (9), normal button
#: tracking (1000), button-event / drag (1002), any-event / motion (1003).
_MOUSE_TRACKING_MODES = frozenset({9 << 5, 1000 << 5, 1002 << 5, 1003 << 5})

#: SGR extended mouse coordinates (DECSET 1006): report as ``CSI < b;x;y M``
#: instead of the legacy three-bytes-offset-by-32 form that caps at column 223.
_MODE_MOUSE_SGR = 1006 << 5

#: One wheel notch scrolls this many lines -- both for our own scrollback and
#: for the cursor keys we synthesise on the alternate screen.
_WHEEL_LINES = 3

#: DECCKM (DEC private mode 1): the program wants cursor keys as ``ESC O A``
#: rather than ``ESC [ A``. Pagers set it, so the synthesised wheel arrows have
#: to follow suit.
_MODE_APP_CURSOR = 1 << 5

#: Most characters fed to pyte in one frame. A ``dir /s`` or ``cat bigfile``
#: burst can deliver megabytes between two frames; feeding all of it to the
#: parser synchronously freezes the window for the whole parse. Cap it and
#: re-arm the flush timer with the remainder, so a loud command degrades to a
#: fast scroll (the window stays responsive, other panes keep painting) instead
#: of a hang. This is a *responsiveness* cap, not throughput: pure-Python pyte
#: parses on the order of 1 MB/s, so ~96K keeps a single frame's parse near
#: 100 ms worst case while the total dump still streams through in the
#: background. See ``bench_terminal.py``.
_MAX_FEED_CHARS = 96 * 1024

#: Hard ceiling on unparsed pty output held in memory. A producer that outruns
#: the parser past this point is dumping content the user will scroll straight
#: past; the oldest bytes are shed to stay bounded (counted for the perf HUD).
_MAX_BACKLOG_CHARS = 8 * 1024 * 1024

#: The run cache is keyed by absolute row. Cap its size so scrolling through a
#: long scrollback cannot grow it without limit.
_RUN_CACHE_LIMIT = 512

_MONOSPACE_PREFERENCE = (
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "Lucida Console",
    "Courier New",
)


def preferred_font(size: int) -> QFont:
    """The best monospace font installed, at ``size`` points.

    Cell geometry is derived from font metrics, so a proportional font would
    make every column drift. Falling back to the system fixed-pitch font
    guarantees we never end up with one.
    """
    families = set(QFontDatabase.families())
    for name in _MONOSPACE_PREFERENCE:
        if name in families:
            font = QFont(name, size)
            font.setStyleHint(QFont.Monospace)
            font.setFixedPitch(True)
            return font

    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setPointSize(size)
    font.setFixedPitch(True)
    return font


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------

#: Keys whose escape sequence never varies.
_PLAIN_KEYS = {
    Qt.Key_Escape: "\x1b",
    Qt.Key_Tab: "\t",
    Qt.Key_Backtab: "\x1b[Z",
    Qt.Key_Return: "\r",
    Qt.Key_Enter: "\r",
    # xterm sends DEL for backspace; ConPTY maps it back to the console's
    # backspace key event, so cmd and PSReadLine both behave.
    Qt.Key_Backspace: "\x7f",
    Qt.Key_Insert: "\x1b[2~",
    Qt.Key_Delete: "\x1b[3~",
    Qt.Key_PageUp: "\x1b[5~",
    Qt.Key_PageDown: "\x1b[6~",
    Qt.Key_F1: "\x1bOP",
    Qt.Key_F2: "\x1bOQ",
    Qt.Key_F3: "\x1bOR",
    Qt.Key_F4: "\x1bOS",
    Qt.Key_F5: "\x1b[15~",
    Qt.Key_F6: "\x1b[17~",
    Qt.Key_F7: "\x1b[18~",
    Qt.Key_F8: "\x1b[19~",
    Qt.Key_F9: "\x1b[20~",
    Qt.Key_F10: "\x1b[21~",
    Qt.Key_F11: "\x1b[23~",
    Qt.Key_F12: "\x1b[24~",
}

#: Keys that take a modifier parameter: ``ESC [ 1 ; <mod> <final>``.
_CURSOR_KEYS = {
    Qt.Key_Up: "A",
    Qt.Key_Down: "B",
    Qt.Key_Right: "C",
    Qt.Key_Left: "D",
    Qt.Key_End: "F",
    Qt.Key_Home: "H",
}


def _modifier_param(mods: Qt.KeyboardModifiers) -> int:
    """xterm's modifier encoding: 1 + shift(1) + alt(2) + ctrl(4)."""
    param = 1
    if mods & Qt.ShiftModifier:
        param += 1
    if mods & Qt.AltModifier:
        param += 2
    if mods & Qt.ControlModifier:
        param += 4
    return param


def key_to_sequence(event: QKeyEvent) -> Optional[str]:
    """Translate a key press into terminal input, or ``None`` to ignore it.

    Kept free of widget state so it can be tested directly.
    """
    key = event.key()
    mods = event.modifiers()
    text = event.text()

    if key in _CURSOR_KEYS:
        final = _CURSOR_KEYS[key]
        param = _modifier_param(mods)
        if param == 1:
            return f"\x1b[{final}"
        return f"\x1b[1;{param}{final}"

    if key in _PLAIN_KEYS:
        sequence = _PLAIN_KEYS[key]
        # Alt prefixes an ESC, which is how shells detect Meta.
        if mods & Qt.AltModifier and sequence not in ("\x1b",):
            return "\x1b" + sequence
        return sequence

    ctrl = bool(mods & Qt.ControlModifier)
    alt = bool(mods & Qt.AltModifier)

    if ctrl and not (mods & Qt.MetaModifier):
        # Ctrl+A..Z collapse to 0x01..0x1a; the rest of the C0 range follows
        # the usual @ [ \ ] ^ _ mapping.
        if Qt.Key_A <= key <= Qt.Key_Z:
            code = key - Qt.Key_A + 1
            return ("\x1b" if alt else "") + chr(code)
        specials = {
            Qt.Key_Space: "\x00",
            Qt.Key_At: "\x00",
            Qt.Key_BracketLeft: "\x1b",
            Qt.Key_Backslash: "\x1c",
            Qt.Key_BracketRight: "\x1d",
            Qt.Key_AsciiCircum: "\x1e",
            Qt.Key_Underscore: "\x1f",
            Qt.Key_Question: "\x7f",
        }
        if key in specials:
            return ("\x1b" if alt else "") + specials[key]

    if text:
        return ("\x1b" + text) if alt else text

    return None


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------

class TerminalCanvas(QWidget):
    """Draws the screen grid and collects input for one pane."""

    #: Emitted when the visible geometry changes, as ``(rows, cols)``.
    geometry_changed = Signal(int, int)

    #: Emitted when the user scrolls or new output arrives.
    content_changed = Signal()

    #: Emitted with text destined for the pty.
    input_requested = Signal(str)

    #: Emitted when this canvas gains keyboard focus.
    focus_gained = Signal()

    #: Emitted when a bare Return/Enter is sent -- the user submitted a line.
    submitted = Signal()

    def __init__(
        self,
        screen: TerminalScreen,
        font: QFont,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._screen = screen
        self._palette = Palette()
        self._scroll_top = 0

        # Per-row cache of painted runs, keyed by absolute row -> (width, runs,
        # cells). Populated during paint, invalidated per-row from _flush by the
        # rows pyte marked dirty; wiped wholesale on any change that shifts every
        # row (resize, theme, font, a structural scroll into history).
        self._run_cache: dict[int, tuple] = {}
        # Stamped by the owning TerminalView; None in isolation / tests.
        self._perf: Optional[perf.PaneMetrics] = None

        # Selection is stored in absolute (row, col) coordinates spanning
        # scrollback and screen, so it survives new output scrolling the view.
        self._sel_anchor: Optional[tuple[int, int]] = None
        self._sel_head: Optional[tuple[int, int]] = None
        self._selecting = False

        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.IBeamCursor)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Dropping a file onto a terminal types its path, the way it does in
        # Windows Terminal and cmd -- see dropEvent.
        self.setAcceptDrops(True)
        # ConPTY gives us the input; Qt must not swallow Tab for focus changes.
        self.setAttribute(Qt.WA_InputMethodEnabled, True)

        self._cell_w = 8.0
        self._cell_h = 16.0
        self._ascent = 12.0
        self.set_font(font)

    # -- theme -----------------------------------------------------------------

    def apply_theme(self) -> None:
        """Rebuild the colour palette for the current theme and repaint."""
        self._palette = Palette()
        self.invalidate_all()

    # -- repaint invalidation -------------------------------------------------

    def invalidate_all(self) -> None:
        """Drop every cached row and schedule a full repaint.

        For a change that moves every row relative to the viewport or restyles
        them all: resize, font, theme, alternate-screen swap, a scroll that
        pushed lines into history.
        """
        self._run_cache.clear()
        self.update()

    def invalidate_rows(self, abs_rows) -> None:
        """Drop the given absolute rows from the cache and repaint just them.

        The fast path out of :meth:`TerminalView._flush`: only the handful of
        rows pyte marked dirty (a spinner frame, a streamed token, one edited
        line) are rebuilt and blitted.
        """
        if not abs_rows:
            return
        top = self._scroll_top
        visible = self.visible_rows()
        cell_h = self._cell_h
        lo = hi = None
        for row in abs_rows:
            self._run_cache.pop(row, None)
            view_row = row - top
            if 0 <= view_row < visible:
                lo = view_row if lo is None else min(lo, view_row)
                hi = view_row if hi is None else max(hi, view_row)
        if lo is None:
            return
        self.update(
            0,
            int(lo * cell_h),
            self.width(),
            int((hi - lo + 1) * cell_h) + 1,
        )

    # -- font / geometry ---------------------------------------------------

    def set_font(self, font: QFont) -> None:
        self._font = font
        metrics = QFontMetricsF(font)
        # horizontalAdvance of a wide glyph is the cell width for a fixed-pitch
        # face. The row pitch is the font's designed line spacing, but never
        # less than ascent+descent: Cascadia's leading is negative at some sizes
        # and positive at others, so taking either one alone would clip
        # descenders at one size or gap the box-drawing glyphs at another.
        self._cell_w = max(1.0, metrics.horizontalAdvance("W"))
        self._cell_h = max(1.0, metrics.height(), metrics.lineSpacing())
        self._ascent = metrics.ascent()
        self.invalidate_all()
        self._emit_geometry()

    @property
    def cell_size(self) -> tuple[float, float]:
        return self._cell_w, self._cell_h

    def visible_rows(self) -> int:
        return max(1, int(self.height() // self._cell_h))

    def visible_cols(self) -> int:
        return max(1, int(self.width() // self._cell_w))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._emit_geometry()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        # Sizes reported while this canvas was off screen were ignored, so the
        # pty may still be sized for the layout the pane had before it was
        # hidden. There is a real size to publish now.
        self._emit_geometry()

    def _emit_geometry(self) -> None:
        # A hidden or not-yet-laid-out canvas has no meaningful size, and the
        # numbers it does report are actively harmful. Mid-reparent its width is
        # 0, which visible_cols() floors to a single column -- and resizing the
        # screen to one column truncates every line in the pane to one
        # character, wiping the pane's contents. A pane hidden behind an
        # expanded one is worse still: it would be told it is as wide as the
        # expanded pane, so anything running in it repaints at a width it does
        # not have. A real terminal only ever resizes to a size it actually
        # occupies, and so does this one.
        if not self.isVisible() or self.width() <= 0 or self.height() <= 0:
            return
        self.geometry_changed.emit(self.visible_rows(), self.visible_cols())

    # -- scrolling ---------------------------------------------------------

    def scroll_top(self) -> int:
        return self._scroll_top

    def set_scroll_top(self, value: int) -> None:
        value = max(0, min(value, self.max_scroll_top()))
        if value != self._scroll_top:
            self._scroll_top = value
            self.update()

    def max_scroll_top(self) -> int:
        return max(0, self._screen.total_rows() - self.visible_rows())

    def at_bottom(self) -> bool:
        return self._scroll_top >= self.max_scroll_top()

    def scroll_to_bottom(self) -> None:
        self.set_scroll_top(self.max_scroll_top())

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if not delta:
            event.accept()
            return

        notches = max(1, abs(delta) // 120)
        up = delta > 0
        force_local = bool(event.modifiers() & Qt.ShiftModifier)

        # 1. The program is reading the mouse (Claude Code, htop, less -R, any
        #    TUI): forward the wheel so it scrolls *its* content, exactly as
        #    xterm and Windows Terminal do. Shift forces our local scrollback.
        if self._mouse_tracking() and not force_local:
            seq = self._encode_mouse(64 if up else 65, event.position().toPoint())
            if seq:
                self.input_requested.emit(seq * notches)
                event.accept()
                return

        # 2. Alternate screen, no mouse tracking: there is no scrollback to move
        #    through (a full-screen program owns the viewport), so translate the
        #    wheel to cursor keys -- "alternate scroll mode". Without this the
        #    wheel is simply dead over vim, a pager, or Claude Code's renderer.
        #    This *is* the local wheel behaviour here, so Shift doesn't suppress
        #    it -- Shift only opts out of sending the program mouse reports.
        if self._screen.alternate_screen:
            intro = "\x1bO" if _MODE_APP_CURSOR in self._screen.mode else "\x1b["
            arrow = intro + ("A" if up else "B")
            self.input_requested.emit(arrow * (notches * _WHEEL_LINES))
            event.accept()
            return

        # 3. Primary screen: scroll our own scrollback.
        step = notches * _WHEEL_LINES
        self.set_scroll_top(self._scroll_top - (step if up else -step))
        self.content_changed.emit()
        event.accept()

    def _mouse_tracking(self) -> bool:
        """True while the program has asked to receive mouse events."""
        return bool(_MOUSE_TRACKING_MODES & self._screen.mode)

    def _cell_coords(self, pos: QPoint) -> tuple[int, int]:
        """1-based ``(col, row)`` for a pixel position, clamped to the screen."""
        col = int(pos.x() // self._cell_w) + 1
        row = int(pos.y() // self._cell_h) + 1
        col = max(1, min(col, self._screen.columns))
        row = max(1, min(row, self._screen.lines))
        return col, row

    def _encode_mouse(self, button: int, pos: QPoint, *, release: bool = False) -> str:
        """One mouse report in whichever encoding the program selected.

        Returns ``""`` when the position can't be expressed in the legacy
        encoding (past column/row 223 and no SGR mode) -- the caller then
        falls back to plain scrolling.
        """
        col, row = self._cell_coords(pos)
        if _MODE_MOUSE_SGR in self._screen.mode:
            return f"\x1b[<{button};{col};{row}{'m' if release else 'M'}"
        code = 32 + (3 if release else button)
        if col > 223 or row > 223 or code > 255:
            return ""
        return "\x1b[M" + chr(code) + chr(32 + col) + chr(32 + row)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        t0 = perf.now() if perf.enabled() else 0.0
        painter = QPainter(self)
        painter.setFont(self._font)
        painter.fillRect(event.rect(), self._palette.BACKGROUND)

        screen = self._screen
        history = screen.history_length
        rows = self.visible_rows()
        top = self._scroll_top
        cell_w, cell_h = self._cell_w, self._cell_h
        # Invariant across the row loop -- recomputing it per row (per frame per
        # pane) showed up in the profile.
        width = self.visible_cols()

        sel_start, sel_end = self._normalised_selection()
        # A selection tints backgrounds per-row, so its rows can't be served
        # from (or written to) the style cache.
        use_cache = sel_start is None

        # Only the rows the incoming damage rect actually covers. A dirty-row
        # repaint hands us a thin band; without this the loop still walked every
        # visible row each frame.
        first = max(0, int(event.rect().top() // cell_h))
        last = min(rows, int(event.rect().bottom() // cell_h) + 1)

        painted = 0
        for view_row in range(first, last):
            abs_row = top + view_row
            if abs_row >= screen.total_rows():
                break

            y = view_row * cell_h
            entry = self._run_cache.get(abs_row) if use_cache else None
            if entry is not None and entry[0] == width:
                runs, cells = entry[1], entry[2]
            else:
                cells = dict(screen.row(abs_row))
                if not cells and use_cache:
                    self._run_cache[abs_row] = (width, (), {})
                    continue
                runs = self._build_runs(cells, width, abs_row, sel_start, sel_end)
                if use_cache:
                    self._run_cache[abs_row] = (width, runs, cells)

            if not cells and sel_start is None:
                continue
            painted += 1

            for start_x, text, fg, bg in runs:
                if bg != self._palette.BACKGROUND:
                    painter.fillRect(
                        QRect(
                            int(start_x * cell_w),
                            int(y),
                            int(len(text) * cell_w) + 1,
                            int(cell_h) + 1,
                        ),
                        bg,
                    )

            for start_x, text, fg, bg in runs:
                if not text.strip():
                    continue
                painter.setPen(QPen(fg))
                painter.drawText(
                    QPoint(int(start_x * cell_w), int(y + self._ascent)),
                    text,
                )

            self._paint_decorations(painter, cells, abs_row, y)

        if use_cache and len(self._run_cache) > _RUN_CACHE_LIMIT:
            self._run_cache.clear()

        self._paint_cursor(painter, history, top, rows)
        painter.end()

        if t0 and self._perf is not None:
            self._perf.frame_ms.add((perf.now() - t0) * 1000.0)
            self._perf.rows_painted.add(painted)

    def _build_runs(
        self,
        cells: dict,
        width: int,
        abs_row: int,
        sel_start: Optional[tuple[int, int]],
        sel_end: Optional[tuple[int, int]],
    ) -> list[tuple[int, str, QColor, QColor]]:
        """Group a row into runs of identical styling.

        One ``drawText`` per style change instead of per character is the
        difference between a smooth scroll and a stuttering one.
        """
        runs: list[tuple[int, str, QColor, QColor]] = []
        current: Optional[list] = None
        blank = self._screen.default_char

        for x in range(width):
            char = cells.get(x, blank)
            fg, bg = self._cell_colours(char)

            if self._in_selection(abs_row, x, sel_start, sel_end):
                bg = self._palette.SELECTION

            if current is not None and current[2] == fg and current[3] == bg:
                current[1] += char.data
                continue

            if current is not None:
                runs.append((current[0], current[1], current[2], current[3]))
            current = [x, char.data, fg, bg]

        if current is not None:
            runs.append((current[0], current[1], current[2], current[3]))
        return runs

    def _cell_colours(self, char) -> tuple[QColor, QColor]:
        fg = self._palette.resolve(char.fg, background=False, bold=char.bold)
        bg = self._palette.resolve(char.bg, background=True)
        if char.reverse:
            fg, bg = bg, fg
        return fg, bg

    def _paint_decorations(self, painter: QPainter, cells: dict, abs_row: int, y: float) -> None:
        """Underline and strikethrough, which run under/through the glyphs."""
        cell_w, cell_h = self._cell_w, self._cell_h
        for x, char in cells.items():
            if not (char.underscore or char.strikethrough):
                continue
            fg, _ = self._cell_colours(char)
            painter.setPen(QPen(fg))
            if char.underscore:
                line_y = int(y + cell_h - 2)
                painter.drawLine(int(x * cell_w), line_y, int((x + 1) * cell_w), line_y)
            if char.strikethrough:
                line_y = int(y + cell_h / 2)
                painter.drawLine(int(x * cell_w), line_y, int((x + 1) * cell_w), line_y)

    def _paint_cursor(self, painter: QPainter, history: int, top: int, rows: int) -> None:
        cursor = self._screen.cursor
        if cursor.hidden:
            return

        abs_row = history + cursor.y
        view_row = abs_row - top
        if not (0 <= view_row < rows):
            return

        rect = QRect(
            int(cursor.x * self._cell_w),
            int(view_row * self._cell_h),
            max(1, int(self._cell_w)),
            max(1, int(self._cell_h)),
        )

        if self.hasFocus():
            # A filled block with the glyph knocked out, the way every other
            # terminal draws it.
            painter.fillRect(rect, self._palette.CURSOR)
            cells = dict(self._screen.row(abs_row))
            char = cells.get(cursor.x)
            if char is not None and char.data.strip():
                painter.setPen(QPen(self._palette.BACKGROUND))
                painter.drawText(
                    QPoint(rect.x(), int(view_row * self._cell_h + self._ascent)),
                    char.data,
                )
        else:
            painter.setPen(QPen(self._palette.CURSOR))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

    # -- selection ---------------------------------------------------------

    def _cell_at(self, pos: QPoint) -> tuple[int, int]:
        row = self._scroll_top + int(pos.y() // self._cell_h)
        col = int(round(pos.x() / self._cell_w))
        return row, max(0, col)

    def _normalised_selection(
        self,
    ) -> tuple[Optional[tuple[int, int]], Optional[tuple[int, int]]]:
        if self._sel_anchor is None or self._sel_head is None:
            return None, None
        if self._sel_anchor <= self._sel_head:
            return self._sel_anchor, self._sel_head
        return self._sel_head, self._sel_anchor

    @staticmethod
    def _in_selection(
        row: int,
        col: int,
        start: Optional[tuple[int, int]],
        end: Optional[tuple[int, int]],
    ) -> bool:
        if start is None or end is None:
            return False
        if row < start[0] or row > end[0]:
            return False
        if row == start[0] and col < start[1]:
            return False
        if row == end[0] and col >= end[1]:
            return False
        return True

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self._sel_anchor = self._cell_at(event.position().toPoint())
            self._sel_head = self._sel_anchor
            self._selecting = True
            self.update()
        elif event.button() == Qt.MiddleButton:
            # Middle-click paste, as on X11 and in Windows Terminal.
            self.paste()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._selecting:
            self._sel_head = self._cell_at(event.position().toPoint())
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._selecting = False
            if self._sel_anchor == self._sel_head:
                self.clear_selection()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Select the word under the cursor."""
        row, col = self._cell_at(event.position().toPoint())
        cells = dict(self._screen.row(row))
        if not cells:
            return

        def is_word(x: int) -> bool:
            char = cells.get(x)
            return bool(char and char.data.strip() and char.data not in "\"'`()[]{},;")

        if not is_word(col):
            return
        start = col
        while start > 0 and is_word(start - 1):
            start -= 1
        end = col
        while is_word(end + 1):
            end += 1

        # The press that opened this double-click left _selecting armed; clear it
        # so a tiny drag afterwards doesn't collapse the word selection back to a
        # single cell in mouseMoveEvent.
        self._selecting = False
        self._sel_anchor = (row, start)
        self._sel_head = (row, end + 1)
        self.update()
        event.accept()

    def clear_selection(self) -> None:
        if self._sel_anchor is not None or self._sel_head is not None:
            self._sel_anchor = None
            self._sel_head = None
            self.update()

    def has_selection(self) -> bool:
        start, end = self._normalised_selection()
        return start is not None and start != end

    def selected_text(self) -> str:
        start, end = self._normalised_selection()
        if start is None or end is None:
            return ""

        lines: list[str] = []
        for row in range(start[0], end[0] + 1):
            cells = dict(self._screen.row(row))
            if not cells:
                lines.append("")
                continue
            first = start[1] if row == start[0] else 0
            last = end[1] if row == end[0] else max(cells.keys()) + 1
            text = "".join(cells.get(x, self._screen.default_char).data for x in range(first, last))
            lines.append(text.rstrip())
        return "\n".join(lines)

    # -- clipboard ---------------------------------------------------------

    def copy(self) -> None:
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def paste(self) -> None:
        payload = self._as_paste(QGuiApplication.clipboard().text())
        if payload:
            self.input_requested.emit(payload)

    def _as_paste(self, text: str) -> str:
        """Normalise clipboard/drop text the way a paste into a shell expects."""
        if not text:
            return ""
        # Shells expect CR for "the user pressed Enter"; a literal LF makes
        # PSReadLine insert a continuation instead of running the line.
        text = text.replace("\r\n", "\r").replace("\n", "\r")

        if _MODE_BRACKETED_PASTE in self._screen.mode:
            # The program asked to be told this is a paste, which lets editors
            # skip auto-indent and shells refuse to run multi-line text blindly.
            text = f"\x1b[200~{text}\x1b[201~"

        return text

    # -- drag and drop ---------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._drop_text(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._drop_text(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        text = self._drop_text(event.mimeData())
        if not text:
            event.ignore()
            return
        # A drop is a click plus a paste: focus the pane, snap back to the
        # prompt, and hand the shell the text -- but never a trailing Enter, so
        # the user still gets to look at what landed before running it. This is
        # what dropping a file onto Windows Terminal or cmd does.
        self.setFocus(Qt.MouseFocusReason)
        self.scroll_to_bottom()
        self.clear_selection()
        self.input_requested.emit(text)
        self.content_changed.emit()
        event.acceptProposedAction()

    def _drop_text(self, mime) -> str:
        """What a drop carrying ``mime`` should type into the shell.

        Files and folders -- from Explorer, or any app that exposes URLs --
        arrive as their filesystem paths: space-separated, and double-quoted
        when a path holds a space or a shell metacharacter. Anything else is
        treated as dropped text and pasted.
        """
        if mime.hasUrls():
            parts = [
                self._quote_path(url.toLocalFile())
                if url.isLocalFile()
                else url.toString()
                for url in mime.urls()
            ]
            return " ".join(part for part in parts if part)
        if mime.hasText():
            return self._as_paste(mime.text())
        return ""

    @staticmethod
    def _quote_path(path: str) -> str:
        native = QDir.toNativeSeparators(path)
        if native and any(ch.isspace() or ch in "()&^;,!`'" for ch in native):
            return f'"{native}"'
        return native

    # -- keyboard ----------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        mods = event.modifiers()

        # Ctrl+Shift is the terminal convention for app shortcuts, because bare
        # Ctrl+C must stay available as SIGINT.
        if mods & Qt.ControlModifier and mods & Qt.ShiftModifier:
            if event.key() == Qt.Key_C:
                self.copy()
                event.accept()
                return
            if event.key() in (Qt.Key_V, Qt.Key_Insert):
                self.paste()
                event.accept()
                return

        # Bare Ctrl+C copies when text is selected and interrupts otherwise --
        # the same compromise Windows Terminal makes.
        if (
            mods & Qt.ControlModifier
            and not (mods & Qt.ShiftModifier)
            and event.key() == Qt.Key_C
            and self.has_selection()
        ):
            self.copy()
            self.clear_selection()
            event.accept()
            return

        if mods & Qt.ShiftModifier and event.key() == Qt.Key_Insert:
            self.paste()
            event.accept()
            return

        # Page keys scroll the buffer when shifted, like a pager. Ctrl+Shift+Page
        # is the panel's switch-workspace shortcut, so leave that combination for
        # the QAction rather than also scrolling here.
        if (
            mods & Qt.ShiftModifier
            and not (mods & Qt.ControlModifier)
            and event.key() in (Qt.Key_PageUp, Qt.Key_PageDown)
        ):
            delta = self.visible_rows() - 1
            if event.key() == Qt.Key_PageUp:
                delta = -delta
            self.set_scroll_top(self._scroll_top + delta)
            self.content_changed.emit()
            event.accept()
            return

        sequence = key_to_sequence(event)
        if sequence is None:
            event.ignore()
            return

        # Typing means "take me back to the prompt".
        self.scroll_to_bottom()
        self.clear_selection()
        self.input_requested.emit(sequence)
        # A bare Enter runs the current line -- voice input listens for the next
        # command, so a submit is its natural stopping point.
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier))
        ):
            self.submitted.emit()
        self.content_changed.emit()
        event.accept()

    def inputMethodEvent(self, event) -> None:  # noqa: N802
        """Commit IME text (CJK input, dead keys) straight to the pty."""
        text = event.commitString()
        if text:
            self.input_requested.emit(text)
        event.accept()

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.focus_gained.emit()
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def event(self, event) -> bool:
        # Qt would otherwise steal Tab and Backtab to move focus between panes.
        # Ctrl+Tab is left alone -- that is the panel's pane-cycling shortcut.
        if (
            event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Tab, Qt.Key_Backtab)
            and not event.modifiers() & Qt.ControlModifier
        ):
            self.keyPressEvent(event)
            return True
        return super().event(event)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

class TerminalView(QWidget):
    """A canvas, a scrollbar, and the pty session feeding them."""

    #: The shell exited, with its code.
    exited = Signal(int)

    #: The program set the window title (OSC 0/2).
    title_changed = Signal(str)

    #: This pane took keyboard focus.
    focus_gained = Signal()

    #: A bare Return/Enter was sent to the shell -- the user ran a line.
    submitted = Signal()

    def __init__(
        self,
        shell: str = DEFAULT_SHELL,
        font_size: int = 11,
        scrollback: int = DEFAULT_SCROLLBACK,
        cwd: Optional[str] = None,
        startup_command: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)

        self._font_size = font_size
        self._screen = TerminalScreen(80, 24, scrollback=scrollback)
        self._stream = TerminalStream(self._screen)
        self._pending: list[str] = []
        self._last_title = ""
        # A command to run once, as soon as the shell is up (the setup wizard's
        # chosen agent). Fired on the first real output -- that is the shell
        # printing its banner / first prompt, i.e. it is ready for input.
        self._startup_command = (startup_command or "").strip()
        self._startup_sent = not self._startup_command
        # Wall-clock (epoch) moment the startup agent command was typed at the
        # shell. The conversation-handoff feature uses it to tell which on-disk
        # agent session belongs to *this* pane (the newest one created after the
        # pane started its agent), rather than blindly the newest overall.
        self._agent_started_at = 0.0
        # When the pty last produced output; the alt-screen watchdog uses it to
        # skip its process-table walk while a program is visibly still painting.
        self._last_output_at = 0.0

        self.canvas = TerminalCanvas(self._screen, preferred_font(font_size), self)
        self.scrollbar = QScrollBar(Qt.Vertical, self)
        self.scrollbar.setRange(0, 0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.scrollbar)
        self.scrollbar.hide()  # shown by _sync_scrollbar once there's scrollback
        self._style_scrollbar()

        self.session = PtySession(shell=shell, rows=24, cols=80, cwd=cwd, parent=self)
        self.shell_label = self.session.label

        # Perf instrumentation (near-free unless the HUD is on). The canvas
        # shares the same metrics object so paint time lands beside parse time.
        self._perf = perf.register(self.shell_label)
        self.canvas._perf = self._perf
        #: Running size of _pending, so the backlog check in _on_output is O(1).
        self._pending_chars = 0
        #: Absolute row the cursor sat on at the end of the last flush -- so a
        #: cursor that moved without redrawing its old cell still gets that cell
        #: repainted.
        self._last_cursor_row = 0

        # Batch pty output and apply it on a frame timer; see module docstring.
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(_FRAME_MS)
        self._flush_timer.timeout.connect(self._flush)

        # Resizes are coalesced rather than applied as they arrive; see
        # _on_geometry_changed.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(_RESIZE_COALESCE_MS)
        self._resize_timer.timeout.connect(self._apply_geometry)

        # Armed only while the alternate screen is up; see _check_alt_screen.
        self._alt_watchdog = QTimer(self)
        self._alt_watchdog.setInterval(_ALT_WATCH_MS)
        self._alt_watchdog.timeout.connect(self._check_alt_screen)

        self.session.output.connect(self._on_output)
        self.session.exited.connect(self._on_exited)
        self.canvas.input_requested.connect(self.session.write)
        self.canvas.geometry_changed.connect(self._on_geometry_changed)
        self.canvas.content_changed.connect(self._sync_scrollbar)
        # focus lands on the canvas, not on this wrapper, so relay from there.
        self.canvas.focus_gained.connect(self.focus_gained)
        self.canvas.submitted.connect(self.submitted)
        self.scrollbar.valueChanged.connect(self.canvas.set_scroll_top)

        if self.session.error:
            self._show_local(
                f"\r\n\x1b[31mFailed to start {self.shell_label}:\x1b[0m "
                f"{self.session.error}\r\n"
            )

        self.setFocusProxy(self.canvas)

    # -- pty plumbing ------------------------------------------------------

    def _on_output(self, data: str) -> None:
        self._pending.append(data)
        self._pending_chars += len(data)

        # High-water mark: a producer this far ahead of the parser is dumping
        # output that will only ever be scrolled past. Shed the oldest held
        # chunks back down to three-quarters of the ceiling so we stay bounded
        # without stalling; the drop is counted for the HUD. (This is a last
        # resort -- _MAX_FEED_CHARS below normally keeps the backlog draining.)
        if self._pending_chars > _MAX_BACKLOG_CHARS:
            target = _MAX_BACKLOG_CHARS * 3 // 4
            dropped = 0
            while self._pending_chars > target and len(self._pending) > 1:
                dropped += len(self._pending[0])
                self._pending_chars -= len(self._pending.pop(0))
            if dropped:
                # The gap almost certainly cut an escape sequence, and whatever
                # is now at the front of the queue may itself start mid-CSI. A
                # newline can't fall inside a CSI/OSC, so resume just after the
                # first one and prepend an SGR reset -- the parser then starts
                # from a known-clean line instead of rendering a half-sequence
                # as stray text ("48;5;21m…").
                head = self._pending[0]
                nl = head.find("\n")
                if nl != -1:
                    dropped += nl + 1
                    self._pending[0] = "\x1b[m" + head[nl + 1:]
                    self._pending_chars += len("\x1b[m") - (nl + 1)
                if self._perf is not None:
                    self._perf.note_dropped(dropped)

        if self._perf is not None:
            self._perf.note_bytes(len(data))
            self._perf.note_backlog(self._pending_chars)

        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush(self) -> None:
        if not self._pending:
            self._flush_timer.stop()
            return

        t_flush = perf.now() if perf.enabled() else 0.0

        data = "".join(self._pending)
        self._pending.clear()
        self._pending_chars = 0

        # Bound the work this frame. The remainder goes back on the queue and
        # the still-running timer drains it next tick -- a loud command scrolls
        # fast instead of freezing the window for one giant parse.
        if len(data) > _MAX_FEED_CHARS:
            rest = data[_MAX_FEED_CHARS:]
            data = data[:_MAX_FEED_CHARS]
            self._pending.append(rest)
            self._pending_chars = len(rest)

        chunk = data
        self._last_output_at = time.monotonic()

        if not self._startup_sent:
            # The shell has produced output, so it is alive and about to
            # prompt. Give PSReadLine / bash's line editor a beat to finish
            # initialising, then type the command.
            self._startup_sent = True
            QTimer.singleShot(300, self._send_startup_command)

        follow = self.canvas.at_bottom()
        scroll_before = self.canvas.scroll_top()
        history_before = self._screen.history_length
        alt_before = self._screen.alternate_screen

        t_parse = perf.now() if perf.enabled() else 0.0
        try:
            self._stream.feed(chunk)
        except Exception:  # noqa: BLE001 - a malformed sequence must not crash the pane
            pass
        if t_parse and self._perf is not None:
            self._perf.parse_ms.add((perf.now() - t_parse) * 1000.0)

        if follow:
            self.canvas.scroll_to_bottom()
        self._sync_scrollbar()
        self._sync_alt_watchdog()
        self._repaint_after_feed(scroll_before, history_before, alt_before)

        if self._perf is not None:
            self._perf.note_backlog(self._pending_chars)
        if t_flush and self._perf is not None:
            self._perf.flush_ms.add((perf.now() - t_flush) * 1000.0)

        if self._screen.title and self._screen.title != self._last_title:
            self._last_title = self._screen.title
            self.title_changed.emit(self._screen.title)

    def _repaint_after_feed(
        self, scroll_before: int, history_before: int, alt_before: bool
    ) -> None:
        """Repaint only what the feed changed.

        pyte tracks dirty lines; the renderer just never consumed them before
        (so every frame was a full canvas repaint). A structural change -- lines
        scrolled into history, the alternate buffer swapped in/out, the view
        scrolled -- moves every row relative to the viewport, so those still
        force a full repaint. Everything else (a spinner, streamed text landing
        mid-screen, one edited line) touches a handful of rows.

        A held text selection also forces the full path: invalidate_rows()
        repaints only the bounding box of the rows pyte marked dirty, so a
        row inside the selected band that isn't itself dirty this frame keeps
        whatever pixels were already there instead of being re-tinted -- a
        gap in the highlight while the shell keeps streaming under a drag.
        """
        screen = self._screen
        dirty = screen.dirty
        structural = (
            screen.history_length != history_before
            or screen.alternate_screen != alt_before
            or self.canvas.scroll_top() != scroll_before
        )
        cursor_row = screen.history_length + screen.cursor.y
        if structural or len(dirty) >= screen.lines or self.canvas.has_selection():
            self.canvas.invalidate_all()
        else:
            offset = screen.history_length
            abs_rows = {offset + line for line in dirty}
            abs_rows.add(cursor_row)
            abs_rows.add(self._last_cursor_row)
            self.canvas.invalidate_rows(abs_rows)
        dirty.clear()
        self._last_cursor_row = cursor_row

    def _send_startup_command(self) -> None:
        """Type the wizard's agent command at the fresh shell, once."""
        if self._startup_command and self.session.is_alive():
            self.session.write(self._startup_command + "\r")
            self._agent_started_at = time.time()
        self._startup_command = ""

    @property
    def agent_started_at(self) -> float:
        """Epoch seconds when this pane typed its startup agent command (0 if
        it never had one)."""
        return self._agent_started_at

    def _show_local(self, text: str) -> None:
        """Write text into the screen without involving the shell."""
        self._stream.feed(text)
        self._screen.dirty.clear()
        self.canvas.scroll_to_bottom()
        self._sync_scrollbar()
        self.canvas.invalidate_all()

    def _on_exited(self, code: int) -> None:
        self._flush()
        self._show_local(
            f"\r\n\x1b[90m[{self.shell_label} exited with code {code}]\x1b[0m\r\n"
        )
        self.exited.emit(code)

    # -- geometry ----------------------------------------------------------

    def _on_geometry_changed(self, rows: int, cols: int) -> None:
        # Do not touch the pty yet. Rebuilding the splitter tree walks through
        # intermediate states -- a pane is detached, so its neighbour briefly
        # owns the whole width; a pane is reparented, so it is momentarily
        # 0 columns wide -- and every one of them arrives here as a resize.
        # Passing those on is destructive: pyte truncates each line to the new
        # width, so a transient narrow size permanently shortens the pane's
        # contents, and any full-screen program in the pane repaints itself at a
        # size it never actually had. Coalescing on a short timer means only the
        # geometry the pane settles at reaches the shell. The reported size is
        # deliberately unused: by the time the timer fires the canvas itself is
        # telling the truth, so _apply_geometry asks it again.
        self._resize_timer.start()

    def _apply_geometry(self) -> None:
        if not self.canvas.isVisible():
            return

        rows, cols = self.canvas.visible_rows(), self.canvas.visible_cols()
        if (rows, cols) == (self._screen.lines, self._screen.columns):
            self._sync_scrollbar()
            return

        follow = self.canvas.at_bottom()
        self._screen.resize(lines=rows, columns=cols)
        self.session.resize(rows, cols)
        self._screen.dirty.clear()

        if follow:
            self.canvas.scroll_to_bottom()
        self._sync_scrollbar()
        # Width changed -> every cached row's run layout is stale.
        self.canvas.invalidate_all()

    def _sync_scrollbar(self) -> None:
        maximum = self.canvas.max_scroll_top()
        # Keep the right edge clean (and the rounded pane corner intact) while
        # there is nothing to scroll.
        self.scrollbar.setVisible(maximum > 0)
        self.scrollbar.setRange(0, maximum)
        self.scrollbar.setPageStep(self.canvas.visible_rows())
        self.scrollbar.setSingleStep(1)
        # setValue would re-enter set_scroll_top; blocking keeps it one-way.
        self.scrollbar.blockSignals(True)
        self.scrollbar.setValue(self.canvas.scroll_top())
        self.scrollbar.blockSignals(False)

    # -- stuck alternate screen ------------------------------------------------

    def _sync_alt_watchdog(self) -> None:
        """Run the watchdog only while the alternate screen is actually up."""
        if self._screen.alternate_screen:
            if not self._alt_watchdog.isActive():
                self._alt_watchdog.start()
        elif self._alt_watchdog.isActive():
            self._alt_watchdog.stop()

    def _check_alt_screen(self) -> None:
        """Restore the primary buffer if the program that owned it is gone.

        A full-screen program (``vim``, ``less``, a TUI) switches to the
        alternate screen on the way in and is meant to switch back on the way
        out. When it crashes or is killed it never does, and the pane is left
        with no scrollback and a dead scrollbar. The program is always a child
        of the shell, so once the shell has no children again we know it is
        safe to drop back to the primary buffer ourselves.
        """
        if not self._screen.alternate_screen:
            self._alt_watchdog.stop()
            return
        # Still painting = still alive. Skip the expensive Toolhelp walk until the
        # alternate screen has been quiet for a beat.
        if time.monotonic() - self._last_output_at < _ALT_QUIET_S:
            return
        if self.session.is_alive() and self.session.has_child_process():
            return

        self._screen.exit_alternate_screen()
        self._screen.dirty.clear()
        self._alt_watchdog.stop()
        self.canvas.scroll_to_bottom()
        self._sync_scrollbar()
        self.canvas.invalidate_all()

    def reset_screen(self) -> None:
        """Recover a pane wedged on the alternate screen, on demand."""
        self._screen.exit_alternate_screen()
        self._screen.dirty.clear()
        self._sync_alt_watchdog()
        self.canvas.scroll_to_bottom()
        self._sync_scrollbar()
        self.canvas.invalidate_all()

    # -- programmatic input --------------------------------------------------

    def insert_text(self, text: str) -> None:
        """Type ``text`` at the prompt without ever pressing Enter.

        Used by the voice overlay (and anything else that types for the user).
        Same contract as dropping text onto the pane: normalised for a shell
        paste -- CR line endings, and the bracketed-paste wrapper when the
        running program asked for it -- snapped back to the prompt, but the
        line is left for the user to run.
        """
        payload = self.canvas._as_paste(text)
        if not payload:
            return
        self.canvas.scroll_to_bottom()
        self.canvas.clear_selection()
        self.session.write(payload)
        self.canvas.content_changed.emit()

    def erase_text(self, count: int) -> None:
        """Best-effort: delete ``count`` chars left of the cursor (DEL bytes).

        ``\\x7f`` is what a real Backspace sends to PSReadLine / readline. The
        caller clamps ``count`` to what it typed and never lets it cross a
        newline, so an over- or under-erase after the user also typed is on
        them -- this is a "scratch that" convenience, not an editor.
        """
        count = int(count)
        if count <= 0:
            return
        self.canvas.scroll_to_bottom()
        self.session.write("\x7f" * count)
        self.canvas.content_changed.emit()

    def submit(self) -> None:
        """Press Enter at the prompt (and let the panel know, like a real key)."""
        self.canvas.scroll_to_bottom()
        self.session.write("\r")
        self.submitted.emit()

    # -- appearance --------------------------------------------------------

    def set_font_size(self, size: int) -> None:
        self._font_size = max(6, min(48, size))
        self.canvas.set_font(preferred_font(self._font_size))

    def apply_theme(self) -> None:
        """Repaint the terminal in the current light/dark theme."""
        self.canvas.apply_theme()
        self._style_scrollbar()

    def _style_scrollbar(self) -> None:
        """A thin, quiet scrollbar that doesn't fight the rounded pane edge."""
        t = theme.color
        self.scrollbar.setStyleSheet(
            f"""
            QScrollBar:vertical {{
                background: transparent; width: 10px; margin: 2px 2px 6px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {t('border_hover')}; border-radius: 4px; min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {t('text_faint')}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            """
        )

    @property
    def font_size(self) -> int:
        return self._font_size

    # -- lifecycle ---------------------------------------------------------

    def is_alive(self) -> bool:
        return self.session.is_alive()

    def is_busy(self) -> bool:
        """True while the pane is actively producing output.

        Used by the workspace sidebar's glow dot as "an agent is working
        here". A shell idle at its prompt sends nothing, so this is False;
        an agent streaming a reply or running a command keeps it True.
        """
        if not self._last_output_at:
            return False
        return (time.monotonic() - self._last_output_at) < _BUSY_WINDOW_S

    @property
    def error(self) -> Optional[str]:
        """Why the shell failed to spawn, or ``None`` if it started."""
        return self.session.error

    def close_session(self) -> None:
        self._flush_timer.stop()
        self._alt_watchdog.stop()
        self._pending.clear()
        self._pending_chars = 0
        perf.unregister(self._perf)
        self._perf = None
        self.canvas._perf = None
        self.session.close()
