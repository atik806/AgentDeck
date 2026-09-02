"""Terminal screen state: a pyte screen with scrollback, plus colour lookup.

pyte does the hard part -- parsing the VT/xterm escape stream into a grid of
styled cells. Two things it does not give us in a form this app can use:

* **Scrollback.** ``pyte.HistoryScreen`` exists but its paging model fights the
  dirty-line tracking we want for repaints. It is cheaper and far more
  predictable to subclass the plain screen and grab each line at the moment it
  scrolls off the top.
* **Colours.** pyte hands back names (``"red"``), 256-palette hex strings
  (``"af005f"``) and ``"default"``. The renderer needs ``QColor``.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Iterable, Optional

import pyte
from PySide6.QtGui import QColor
from pyte.screens import Char, Cursor, Margins, StaticDefaultDict

__all__ = ["TerminalScreen", "TerminalStream", "Palette", "DEFAULT_SCROLLBACK"]


DEFAULT_SCROLLBACK = 5000

#: DEC private modes that swap in the alternate screen buffer. ``47`` is the
#: original xterm code, ``1047`` clears the buffer on the way in and ``1049``
#: -- the one everything modern actually sends -- also saves the cursor.
_ALT_BUFFER_MODES = frozenset({47, 1047, 1049})

#: Modes whose entry starts from a cleared alternate buffer.
_ALT_CLEARING_MODES = frozenset({1047, 1049})

#: DECSET 1048 saves the cursor without touching the buffer.
_SAVE_CURSOR_MODE = 1048


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

class Palette:
    """Maps pyte's colour tokens onto concrete ``QColor`` values.

    The 16 ANSI slots come from :mod:`theme` -- the Campbell scheme (Windows
    Terminal's default) in dark mode, a GitHub-light set in light mode -- so a
    fresh ``Palette()`` reflects whatever theme is active. The class attributes
    stay as the dark defaults for any caller that reads them statically.
    """

    BACKGROUND = QColor("#181825")
    FOREGROUND = QColor("#cdd6f4")
    CURSOR = QColor("#f5e0dc")
    SELECTION = QColor(137, 180, 250, 130)

    #: Base names; ``_ANSI`` fills in per mode. pyte calls SGR 33 "brown", and
    #: 0.8.2 ships a typo "bfightmagenta" for SGR 105 -- both are aliased below.
    def __init__(self, mode: Optional[str] = None) -> None:
        try:
            import theme

            self.mode = mode or theme.mode()
            slots = theme.ansi(self.mode)
            self.BACKGROUND = theme.qcolor("term_bg", self.mode)
            self.FOREGROUND = theme.qcolor("term_fg", self.mode)
            self.CURSOR = theme.qcolor("term_cursor", self.mode)
            sel = theme.qcolor("term_selection", self.mode)
            self.SELECTION = QColor(sel.red(), sel.green(), sel.blue(), 130)
        except Exception:  # noqa: BLE001 - theme import must never break a pane
            self.mode = "dark"
            slots = {
                "black": "#45475a", "red": "#f38ba8", "green": "#a6e3a1",
                "yellow": "#f9e2af", "blue": "#89b4fa", "magenta": "#cba6f7",
                "cyan": "#94e2d5", "white": "#bac2de", "brightblack": "#585b70",
                "brightred": "#f5a0b5", "brightgreen": "#bce0b8",
                "brightyellow": "#fbe9c4", "brightblue": "#a8c7ff",
                "brightmagenta": "#dcc1fb", "brightcyan": "#b2ebe1",
                "brightwhite": "#cdd6f4",
            }

        table = dict(slots)
        table["brown"] = table.get("yellow", "#c19c00")
        table["brightbrown"] = table.get("brightyellow", "#f9f1a5")
        table["bfightmagenta"] = table.get("brightmagenta", "#b4009e")
        self._ANSI = table

        self._cache: dict[str, QColor] = {}
        for name, value in self._ANSI.items():
            self._cache[name] = QColor(value)

    def resolve(self, token: str, *, background: bool, bold: bool = False) -> QColor:
        """Turn one pyte colour token into a ``QColor``.

        ``bold`` brightens the eight base ANSI foregrounds, which is the
        long-standing convention for bold text and what every other terminal on
        Windows does.
        """
        if token == "default":
            return self.BACKGROUND if background else self.FOREGROUND

        if bold and not background:
            bright = self._cache.get("bright" + token)
            if bright is not None:
                return bright

        cached = self._cache.get(token)
        if cached is not None:
            return cached

        # Anything left is a 6-digit hex string from the 256-colour table or a
        # truecolor SGR 38;2;r;g;b sequence.
        colour = QColor("#" + token) if len(token) == 6 else QColor(token)
        if not colour.isValid():
            colour = self.BACKGROUND if background else self.FOREGROUND
        self._cache[token] = colour
        return colour


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

#: One scrollback entry: the cells of a line, trailing blanks trimmed off.
Row = list[Char]


class TerminalScreen(pyte.Screen):
    """A pyte screen with scrollback and an alternate screen buffer.

    ``index()`` is pyte's "move down, scrolling if we're at the bottom margin"
    primitive -- every newline that pushes the screen up goes through it. By
    snapshotting the top line before delegating, we build genuine scrollback
    without touching pyte's parsing at all.

    pyte has no notion of the **alternate screen buffer**, and a terminal
    without one cannot run a full-screen program properly: ``vim``, ``less``,
    ``htop`` and Claude Code's fullscreen renderer all switch to it on startup
    (DECSET 1049) and switch back on exit. With the switch ignored, every frame
    they paint lands in the primary buffer instead -- so the shell history is
    destroyed on exit and each repaint that reaches the bottom row scrolls
    another copy of the program's borders and rules into the scrollback. That
    is what produces a pane full of stacked separator lines. This subclass
    implements the swap, and keeps the alternate screen out of the scrollback
    the way a real terminal does.
    """

    def __init__(
        self,
        columns: int,
        lines: int,
        scrollback: int = DEFAULT_SCROLLBACK,
    ):
        # These must exist before super().__init__, which calls reset().
        self.scrollback: deque[Row] = deque(maxlen=max(0, scrollback))
        self._alt_active = False
        self._primary: Optional[tuple] = None
        # False while the alternate buffer is the visible one: a full-screen
        # program's frames are not history.
        self._history_enabled = True
        # The last printable character, for REP (see repeat_characters).
        self._last_graphic = ""
        super().__init__(columns, lines)

    # -- alternate screen buffer -------------------------------------------

    @property
    def alternate_screen(self) -> bool:
        """True while a full-screen program owns the viewport."""
        return self._alt_active

    def set_mode(self, *modes: int, **kwargs) -> None:
        # Only private (``ESC [ ? ... h``) codes carry these meanings; the
        # same numbers as ANSI modes mean something else entirely.
        if kwargs.get("private"):
            codes = set(modes)
            if codes & _ALT_BUFFER_MODES:
                self._enter_alternate(clear=bool(codes & _ALT_CLEARING_MODES))
            elif _SAVE_CURSOR_MODE in codes:
                self.save_cursor()
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private"):
            codes = set(modes)
            if codes & _ALT_BUFFER_MODES:
                self._leave_alternate()
            elif _SAVE_CURSOR_MODE in codes:
                self.restore_cursor()
        super().reset_mode(*modes, **kwargs)

    def _new_buffer(self) -> defaultdict:
        return defaultdict(lambda: StaticDefaultDict(self.default_char))

    def _enter_alternate(self, *, clear: bool) -> None:
        # A program that sends 1049h twice must not lose the primary buffer it
        # saved the first time, nor have its own frame wiped.
        if self._alt_active:
            return

        self._alt_active = True
        self._history_enabled = False
        self._primary = (self.buffer, self.cursor, self.margins)

        self.buffer = self._new_buffer()
        self.cursor = Cursor(0, 0, self.cursor.attrs)
        self.margins = None
        self.dirty.update(range(self.lines))
        if clear:
            self.cursor_position()

    def _leave_alternate(self) -> None:
        if not self._alt_active or self._primary is None:
            return

        self.buffer, self.cursor, self.margins = self._primary
        self._primary = None
        self._alt_active = False
        self._history_enabled = True
        self.dirty.update(range(self.lines))

    def exit_alternate_screen(self) -> None:
        """Force the primary buffer back after a lost ``1049l``.

        A full-screen program that crashes, is killed, or vanishes with its
        SSH connection never sends the ``ESC [ ? 1049 l`` that would restore
        the primary buffer. The pane is then stuck on the alternate screen
        for good: ``history_length`` stays pinned at 0, so scrollback is
        gone and the scrollbar and mouse wheel do nothing for the rest of
        the session. When the view can tell the program is gone it calls
        this to recover, the way typing ``reset`` into the shell would.
        """
        if not self._alt_active:
            return
        if self._primary is not None:
            self._leave_alternate()
            return
        # No saved primary to restore (should not happen, but never get
        # wedged over it) -- just drop back to history-keeping mode.
        self._alt_active = False
        self._history_enabled = True
        self.dirty.update(range(self.lines))

    def reset(self) -> None:
        # RIS drops the alternate screen along with everything else. reset() is
        # called from pyte's __init__, so this runs before there is anything to
        # restore -- hence the plain assignment rather than _leave_alternate.
        self._alt_active = False
        self._primary = None
        self._history_enabled = True
        self._last_graphic = ""
        super().reset()

    # -- REP ----------------------------------------------------------------

    def draw(self, data: str) -> None:
        super().draw(data)
        if data:
            self._last_graphic = data[-1]

    def repeat_characters(self, count: Optional[int] = None) -> None:
        """``CSI Ps b`` -- repeat the preceding character ``count`` times.

        pyte does not implement REP. Programs use it to compress long runs of
        one character, which in practice means exactly the horizontal rules and
        box borders a TUI draws, so dropping it breaks them into single glyphs.
        """
        if not self._last_graphic:
            return
        # Clamp to the rest of the line; repeating is not supposed to wrap.
        count = min(max(1, count or 1), self.columns - self.cursor.x)
        if count > 0:
            self.draw(self._last_graphic * count)

    # -- scrollback capture -------------------------------------------------

    def index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)

        # Only a scroll of the full screen produces scrollback. When a program
        # has set a smaller scroll region (a pager holding a status line, say)
        # the lines leaving that region are not history, they are overwritten.
        if self.cursor.y == bottom and top == 0 and bottom == self.lines - 1:
            self._push_history(top)

        super().index()

    def _materialise_row(self, y: int) -> Row:
        """The cells of buffer row ``y``, trailing blanks trimmed.

        Buffer rows are sparse dicts keyed by column, defaulting to a blank
        cell. Materialise only up to the last written column so a mostly empty
        200-column line costs a few cells, not 200. A row that is all blank --
        or was never touched -- comes back as ``[]``.
        """
        line = self.buffer.get(y)
        if not line:
            return []
        width = max(line.keys()) + 1
        row: Row = [line[x] for x in range(width)]
        while row and row[-1].data == " " and row[-1].bg == "default":
            row.pop()
        return row

    def _push_history(self, y: int) -> None:
        if not self._history_enabled or self.scrollback.maxlen == 0:
            return
        self.scrollback.append(self._materialise_row(y))

    def clear_scrollback(self) -> None:
        self.scrollback.clear()

    # -- geometry -----------------------------------------------------------

    def resize(self, lines: Optional[int] = None, columns: Optional[int] = None) -> None:
        target_lines = lines or self.lines
        target_columns = columns or self.columns
        if (target_lines, target_columns) == (self.lines, self.columns):
            return

        # Dragging a splitter while vim is open still has to leave a correctly
        # shaped shell behind when vim exits, so the hidden primary buffer is
        # resized by the same rules as the visible one.
        if self._alt_active and self._primary is not None:
            was = (self.lines, self.columns)
            alt_state = (self.buffer, self.cursor, self.margins)
            self.buffer, self.cursor, self.margins = self._primary

            # Lines leaving the primary buffer are real history, even though
            # the alternate screen is the one on display.
            self._history_enabled = True
            self._resize_buffer(target_lines, target_columns)
            self._history_enabled = False

            self._primary = (self.buffer, self.cursor, self.margins)
            self.buffer, self.cursor, self.margins = alt_state
            self.lines, self.columns = was

        self._resize_buffer(target_lines, target_columns)

    def _resize_buffer(self, lines: int, columns: int) -> None:
        # Shrinking clips from the top, which is the right behaviour -- it keeps
        # the prompt the user is looking at. pyte does that part itself, but
        # drops the clipped lines on the floor; capture them as scrollback first
        # and let pyte perform the actual shift.
        #
        # Blank rows are the exception. A pane is created at a 24-row default
        # and its first real geometry is usually smaller (a 2x2 grid pane is
        # ~21 rows), so the very first resize clips the top of a screen the
        # shell has not drawn on yet. Pushing those empty rows wedges blank
        # lines into the scrollback above the prompt for the life of the pane --
        # the view then opens scrolled up, over dead space. A clip over empty
        # space is not history; Windows Terminal keeps none either.
        if lines < self.lines:
            for y in range(self.lines - lines):
                if self._materialise_row(y):
                    self._push_history(y)

        super().resize(lines, columns)

    # -- read access for the renderer ---------------------------------------

    @property
    def history_length(self) -> int:
        # A full-screen program owns the viewport: real terminals show none of
        # the scrollback while it runs, and neither does the renderer, which
        # addresses rows as "history first, then screen".
        return 0 if self._alt_active else len(self.scrollback)

    def row(self, index: int) -> Iterable[tuple[int, Char]]:
        """Cells of one row, addressed across scrollback *and* screen.

        ``index`` runs from ``0`` (oldest scrollback line) through
        ``history_length + lines - 1`` (bottom of the live screen), which lets
        the view treat the whole scrollable region as one continuous document.
        """
        history = self.history_length

        if index < history:
            for x, char in enumerate(self.scrollback[index]):
                yield x, char
            return

        line = self.buffer.get(index - history)
        if line is None:
            return
        # Snapshot the row before yielding: this is a generator the paint loop
        # drains lazily, so a mutation of ``line`` between two yields would raise
        # "dictionary changed size during iteration". Feed and paint are both on
        # the GUI thread today, but the copy is cheap and removes the footgun.
        #
        # ``buffer`` lines are insertion-ordered dicts drawn left to right, so
        # the snapshot is already column-ordered in the common case -- only a
        # line a program has back-filled out of order pays for the sort (this
        # runs per visible row per frame per pane).
        cells = list(line.items())
        prev = -1
        for col, _char in cells:
            if col < prev:
                cells.sort(key=lambda kv: kv[0])
                break
            prev = col
        yield from cells

    def total_rows(self) -> int:
        return self.history_length + self.lines


# ---------------------------------------------------------------------------
# Stream
# ---------------------------------------------------------------------------

#: A complete CSI sequence carrying a private parameter prefix of ``<``, ``=``
#: or ``>``: XTMODKEYS (``CSI > 4 ; 2 m``), XTVERSION (``CSI > 0 q``) and the
#: kitty keyboard protocol (``CSI > 1 u``, ``CSI < u``). Intermediates are the
#: standard 0x20-0x2f range, finals 0x40-0x7e, per ECMA-48.
_PRIVATE_CSI = re.compile(r"\x1b\[[<=>][0-9;:]*[ -/]*[@-~]")

#: The tail of a chunk that could still grow into one of the above once more
#: bytes arrive. pty reads are arbitrary chunks, so a sequence can straddle two
#: of them -- and half of ``CSI > 4 m`` fed to pyte is the bug all over again.
#: Deliberately does not match a ``?`` prefix: pyte handles those correctly and
#: they must reach it (DECSET 1049 among them).
_PRIVATE_CSI_TAIL = re.compile(r"\x1b(?:\[(?:[<=>][0-9;:]*[ -/]*)?)?\Z")

#: Stop holding a fragment back after this many characters. Whatever it is, it
#: is not one of the sequences above, and stalling real output is worse.
_MAX_CARRY = 24


class TerminalStream(pyte.Stream):
    """pyte's parser with two corrections.

    **REP.** ``Stream.csi`` maps a sequence's final byte to a screen method
    name, and pyte ships without an entry for ``b`` -- so ``CSI Ps b`` (repeat
    preceding character) is silently discarded. Programs use it to compress
    long runs of one character, which in practice means exactly the horizontal
    rules and box borders a TUI draws. Extending a *copy* of the table adds it
    without mutating pyte's class attribute for anything else in the process.

    **Private parameter prefixes.** pyte skips a ``>`` prefix byte without
    recording that it saw one (``streams.py``: ``elif char in SP_OR_GT: pass``)
    and then dispatches on the final byte as if the sequence were an ordinary
    CSI. ``CSI > 4 ; 2 m`` is XTMODKEYS, which modern TUIs send at startup to
    ask for disambiguated key encoding; pyte executes it as ``SGR 4`` and turns
    **underline on for the rest of the session**. Nothing ever turns it off,
    because as far as the program is concerned it never asked for underline.
    The renderer then underlines blank cells too -- correctly, underlined
    whitespace is still underlined -- so every row of the pane becomes a
    full-width horizontal rule.

    ``<`` is worse: it is not in pyte's skip set at all, so it falls through to
    the generic branch, dispatches on the *prefix*, and breaks out of the
    sequence -- leaving the real final byte to be drawn as text. ``CSI < u``
    (kitty keyboard pop) paints a stray ``u`` on the screen.

    Neither sequence has any effect on a screen model, so both are dropped
    before pyte sees them.
    """

    csi = {**pyte.Stream.csi, "b": "repeat_characters"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._carry = ""

    def feed(self, data: str) -> None:
        if self._carry:
            data = self._carry + data
            self._carry = ""

        # The private-CSI fix-ups only ever fire on data containing an ESC.
        # ``feed`` is on the hot path for a multi-megabyte file dump, where the
        # payload is plain text -- skip both regex passes entirely for it. (A
        # split sequence still resolves: the ESC lands in whichever chunk it
        # falls in, and ``_carry`` bridges the two.)
        if "\x1b" in data:
            tail = _PRIVATE_CSI_TAIL.search(data)
            if tail is not None and len(data) - tail.start() <= _MAX_CARRY:
                self._carry = data[tail.start():]
                data = data[:tail.start()]

            if "\x1b[" in data:
                data = _PRIVATE_CSI.sub("", data)

        if data:
            super().feed(data)
