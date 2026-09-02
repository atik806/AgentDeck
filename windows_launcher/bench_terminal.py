#!/usr/bin/env python3
"""Repeatable micro-benchmark for the terminal screen model + renderer.

Runs head-less (offscreen Qt) so it can go in CI or a shell loop. It drives the
same code the panel does -- ``TerminalStream.feed`` into ``TerminalScreen``,
then a simulated paint that builds the runs for every visible row -- against a
set of canned workloads, and prints parse / paint / total timings.

    cd E:\\Workspace\\V4\\windows_launcher
    .venv\\Scripts\\python.exe bench_terminal.py
    .venv\\Scripts\\python.exe bench_terminal.py --rounds 5 --cols 120 --rows 40

Compare two runs to judge an optimisation. The numbers are wall-clock ms on the
GUI thread -- exactly the budget a real frame competes for.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from terminal_view import TerminalCanvas  # noqa: E402
from vt_screen import TerminalScreen, TerminalStream  # noqa: E402


# ---------------------------------------------------------------------------
# Workloads -- each returns a chunk of pty bytes (as str, the way pyte is fed)
# ---------------------------------------------------------------------------

def w_plain_dump(cols: int) -> str:
    """A quiet `type bigfile` / `cat` -- long plain lines, no escapes. This is
    the case _MAX_FEED_CHARS and the CSI pre-check target."""
    line = ("the quick brown fox jumps over the lazy dog " * 4)[:cols - 1]
    return "\r\n".join(line for _ in range(4000)) + "\r\n"


def w_colour_ls(cols: int) -> str:
    """`ls --color` / build output -- every line carries a few SGR runs."""
    palette = ("\x1b[32m", "\x1b[33m", "\x1b[36m", "\x1b[1;34m", "\x1b[31m")
    out = []
    for i in range(3000):
        c = palette[i % len(palette)]
        out.append(f"{c}drwxr-xr-x\x1b[0m  {c}component-{i:04d}.tsx\x1b[0m")
    return "\r\n".join(out) + "\r\n"


def w_spinner(cols: int) -> str:
    """An agent 'thinking' -- one line rewritten in place, cursor never leaves
    the bottom. The dirty-row path should make this nearly free."""
    frames = "|/-\\"
    out = []
    for i in range(4000):
        out.append(f"\r\x1b[K{frames[i % 4]} working... ({i} tok)")
    return "".join(out)


def w_altscreen_redraw(cols: int, rows: int) -> str:
    """A full-screen TUI repainting its viewport (vim scrolling, a pager). Enter
    the alt screen once, then repaint every row many times."""
    out = ["\x1b[?1049h"]
    for frame in range(400):
        out.append("\x1b[H")
        for r in range(rows):
            out.append(f"\x1b[{r + 1};1H\x1b[K{frame:03d}:{r:02d} " + "=" * (cols - 12))
    out.append("\x1b[?1049l")
    return "".join(out)


WORKLOADS = {
    "plain-dump": lambda c, r: w_plain_dump(c),
    "colour-ls": lambda c, r: w_colour_ls(c),
    "spinner": lambda c, r: w_spinner(c),
    "alt-redraw": lambda c, r: w_altscreen_redraw(c, r),
}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _simulate_paint(canvas: TerminalCanvas, screen: TerminalScreen) -> int:
    """Build the runs for every visible row, the way paintEvent does, without a
    real QPainter. Returns the number of rows processed."""
    canvas._run_cache.clear()
    top = max(0, screen.total_rows() - canvas.visible_rows())
    width = canvas.visible_cols()
    _, sel = (None, None)
    count = 0
    for view_row in range(canvas.visible_rows()):
        abs_row = top + view_row
        if abs_row >= screen.total_rows():
            break
        cells = dict(screen.row(abs_row))
        canvas._build_runs(cells, width, abs_row, None, None)
        count += 1
    return count


def run_workload(name: str, chunk: str, cols: int, rows: int, rounds: int,
                 feed_step: int) -> dict:
    parse_times, paint_times = [], []
    for _ in range(rounds):
        screen = TerminalScreen(cols, rows, scrollback=5000)
        stream = TerminalStream(screen)
        canvas = TerminalCanvas(screen, __import__("terminal_view").preferred_font(11))
        canvas.resize(cols * 9, rows * 18)

        t0 = time.perf_counter()
        for i in range(0, len(chunk), feed_step):
            stream.feed(chunk[i:i + feed_step])
        parse_times.append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        for _ in range(30):
            _simulate_paint(canvas, screen)
        paint_times.append((time.perf_counter() - t0) * 1000.0 / 30.0)
        canvas.deleteLater()

    return {
        "name": name,
        "bytes": len(chunk),
        "parse_ms": statistics.median(parse_times),
        "parse_mbps": len(chunk) / 1e6 / (statistics.median(parse_times) / 1000.0),
        "paint_ms": statistics.median(paint_times),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--cols", type=int, default=100)
    ap.add_argument("--rows", type=int, default=30)
    ap.add_argument("--feed-step", type=int, default=8192,
                    help="pty read size to simulate (bytes per feed call)")
    ap.add_argument("--only", default="", help="comma-separated workload names")
    args = ap.parse_args()

    QApplication(sys.argv)

    wanted = [s for s in args.only.split(",") if s] or list(WORKLOADS)
    print(f"terminal benchmark  ·  {args.cols}x{args.rows}  ·  "
          f"{args.rounds} rounds  ·  feed {args.feed_step}B\n")
    print(f"{'workload':<14}{'size':>9}{'parse ms':>11}{'MB/s':>9}"
          f"{'paint ms/frame':>16}")
    print("-" * 59)
    for name in wanted:
        chunk = WORKLOADS[name](args.cols, args.rows)
        r = run_workload(name, chunk, args.cols, args.rows, args.rounds,
                         args.feed_step)
        print(f"{r['name']:<14}{r['bytes'] / 1024:>7.0f}K"
              f"{r['parse_ms']:>11.1f}{r['parse_mbps']:>9.1f}"
              f"{r['paint_ms']:>16.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
