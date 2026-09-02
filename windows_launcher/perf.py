"""Performance instrumentation: per-pane meters plus an on-screen HUD.

You cannot rank a list of optimisations without numbers, so this module is the
numbers. It is deliberately close to free when the HUD is off -- every call site
guards on :func:`enabled` before it reads the clock -- and the meters are fixed
-size ring buffers, so leaving it on does not leak.

Wiring:

* :class:`TerminalView` calls :func:`register` in ``__init__`` and
  :func:`unregister` in ``close_session``; it stamps the returned
  :class:`PaneMetrics` with parse/flush/backlog/bytes as output flows.
* :class:`TerminalCanvas` stamps the same object with frame (paint) time.
* :class:`PerfHUD` (a frameless child of the window) polls every pane's metrics
  on a timer and paints a compact table. ``terminal_panel`` toggles it with
  ``Ctrl+Shift+P``.
* ``bench_terminal.py`` drives the screen model head-less and prints the same
  aggregates for a set of canned workloads -- the repeatable benchmark.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

__all__ = [
    "enabled",
    "set_enabled",
    "toggle",
    "now",
    "Meter",
    "PaneMetrics",
    "register",
    "unregister",
    "all_metrics",
    "PerfHUD",
]

now = time.perf_counter

_enabled = False


def enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = bool(value)


def toggle() -> bool:
    set_enabled(not _enabled)
    return _enabled


class Meter:
    """A fixed-size ring of samples with cheap aggregates.

    Used for the per-frame quantities (paint time, parse time, rows painted) --
    anything where the useful reading is "typical" and "worst" over a short
    trailing window rather than a running total.
    """

    __slots__ = ("_buf",)

    def __init__(self, window: int = 120) -> None:
        self._buf: deque[float] = deque(maxlen=window)

    def add(self, value: float) -> None:
        self._buf.append(float(value))

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)

    def last(self) -> float:
        return self._buf[-1] if self._buf else 0.0

    def avg(self) -> float:
        return sum(self._buf) / len(self._buf) if self._buf else 0.0

    def peak(self) -> float:
        return max(self._buf) if self._buf else 0.0


class PaneMetrics:
    """Every number the HUD and the benchmark show, for one pane."""

    def __init__(self, label: str = "") -> None:
        self.label = label
        # milliseconds
        self.frame_ms = Meter()      # one TerminalCanvas.paintEvent
        self.parse_ms = Meter()      # one TerminalStream.feed on the GUI thread
        self.flush_ms = Meter()      # the whole _flush (feed + scroll + repaint)
        self.rows_painted = Meter()  # rows the last paint actually drew
        # throughput
        self._byte_accum = 0
        self._byte_t0 = now()
        self._bps = 0.0
        self.total_bytes = 0
        # queue depth, sampled live
        self.backlog_bytes = 0
        self.backlog_peak = 0
        self.dropped_bytes = 0       # shed at the high-water mark

    # -- throughput ----------------------------------------------------------

    def note_bytes(self, count: int) -> None:
        """Record ``count`` bytes arriving from the pty."""
        self.total_bytes += count
        self._byte_accum += count
        elapsed = now() - self._byte_t0
        if elapsed >= 0.5:
            self._bps = self._byte_accum / elapsed
            self._byte_accum = 0
            self._byte_t0 = now()

    def bytes_per_sec(self) -> float:
        # Decay to zero when the pipe goes quiet rather than freezing the last
        # burst rate on screen.
        if now() - self._byte_t0 > 1.5:
            self._bps = 0.0
        return self._bps

    def note_backlog(self, byte_count: int) -> None:
        self.backlog_bytes = byte_count
        self.backlog_peak = max(self.backlog_peak, byte_count)

    def note_dropped(self, count: int) -> None:
        self.dropped_bytes += count


_metrics: list[PaneMetrics] = []


def register(label: str = "") -> PaneMetrics:
    m = PaneMetrics(label)
    _metrics.append(m)
    return m


def unregister(metrics: Optional[PaneMetrics]) -> None:
    try:
        _metrics.remove(metrics)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        pass


def all_metrics() -> list[PaneMetrics]:
    return list(_metrics)


# ---------------------------------------------------------------------------
# HUD
# ---------------------------------------------------------------------------


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


class PerfHUD(QWidget):
    """A translucent, click-through metrics panel pinned to a corner.

    Parented to the window, not the terminal stack, so it floats over every
    pane. Repaints on its own 500 ms timer -- reading the meters is cheap and
    the HUD must not itself distort the frame time it is reporting.
    """

    _MARGIN = 10

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._font = QFont("Cascadia Mono, Consolas", 8)
        self._font.setStyleHint(QFont.Monospace)
        self._metrics = QFontMetrics(self._font)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._active = False
        self.hide()

    # -- visibility --------------------------------------------------------

    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        set_enabled(active)
        self.setVisible(active)
        if active:
            self.raise_()
            self._timer.start()
            self.reposition()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        self.reposition()
        self.update()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.resize(self._preferred_size())
        self.move(
            max(0, parent.width() - self.width() - self._MARGIN),
            self._MARGIN + 40,
        )

    def _lines(self) -> list[str]:
        rows = all_metrics()
        lines = [f"PERF  ·  {len(rows)} pane(s)  ·  Ctrl+Shift+P"]
        agg_frame = agg_parse = agg_flush = 0.0
        agg_bps = float(agg_backlog := 0)
        agg_dropped = 0
        for m in rows:
            agg_frame = max(agg_frame, m.frame_ms.peak())
            agg_parse = max(agg_parse, m.parse_ms.peak())
            agg_flush = max(agg_flush, m.flush_ms.peak())
            agg_bps += m.bytes_per_sec()
            agg_backlog += m.backlog_bytes
            agg_dropped += m.dropped_bytes
            lines.append(
                f"{(m.label or '?')[:10]:<10} "
                f"paint {m.frame_ms.avg():4.1f}/{m.frame_ms.peak():4.1f}  "
                f"parse {m.parse_ms.avg():4.1f}/{m.parse_ms.peak():4.1f}  "
                f"rows {m.rows_painted.avg():3.0f}  "
                f"{_fmt_bytes(m.bytes_per_sec()):>7}/s  "
                f"q {_fmt_bytes(m.backlog_bytes):>6}"
                + (f"  drop {_fmt_bytes(m.dropped_bytes)}" if m.dropped_bytes else "")
            )
        lines.append(
            f"{'ALL':<10} paint  ·  /{agg_frame:4.1f}  "
            f"parse  ·  /{agg_parse:4.1f}  flush /{agg_flush:4.1f}  "
            f"{_fmt_bytes(agg_bps):>7}/s  q {_fmt_bytes(agg_backlog):>6}"
            + (f"  drop {_fmt_bytes(agg_dropped)}" if agg_dropped else "")
        )
        return lines

    def _preferred_size(self):
        lines = self._lines()
        w = max((self._metrics.horizontalAdvance(s) for s in lines), default=120)
        h = self._metrics.height() * len(lines)
        return self._padded(w, h)

    @staticmethod
    def _padded(w: int, h: int):
        from PySide6.QtCore import QSize

        return QSize(w + 18, h + 14)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setFont(self._font)
        painter.fillRect(self.rect(), QColor(12, 12, 20, 214))
        painter.setPen(QColor(70, 70, 100, 220))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        line_h = self._metrics.height()
        y = 7 + self._metrics.ascent()
        for i, text in enumerate(self._lines()):
            painter.setPen(QColor("#94e2d5") if i == 0 else QColor("#cdd6f4"))
            painter.drawText(9, int(y), text)
            y += line_h
        painter.end()
