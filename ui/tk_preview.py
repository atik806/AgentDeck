"""Tk canvas grid preview.

Mirrors :mod:`ui.preview` (the cairo version) and shares its geometry through
:mod:`gridmath`, so the two renderings of "what will the screen look like" can't
drift apart.
"""

from __future__ import annotations

import math
import tkinter as tk

from gridmath import grid_cells, grid_shape
from ui.theme import PALETTE


def _mix(color_a: tuple[int, int, int], color_b: tuple[int, int, int], t: float) -> str:
    """Hex colour ``t`` of the way from ``color_a`` to ``color_b``."""
    t = min(max(t, 0.0), 1.0)
    return "#%02x%02x%02x" % tuple(
        int(round(a + (b - a) * t)) for a, b in zip(color_a, color_b)
    )


#: Endpoints of the tile gradient, matching the cairo preview's ramp.
_TILE_FROM = (54, 133, 227)
_TILE_TO = (130, 170, 255)

_PANE_FILL = "#4a8ad9"


class GridPreview(tk.Canvas):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("background", PALETTE["preview_bg"])
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("borderwidth", 0)
        kwargs.setdefault("height", 200)
        super().__init__(master, **kwargs)
        self._count = 4
        self._auto_tile = True
        self._single_window = True
        self._single_label = "shared window"
        self.bind("<Configure>", lambda _event: self.redraw())

    # -- state -------------------------------------------------------------

    def set_count(self, count: int) -> None:
        count = max(int(count), 1)
        if self._count != count:
            self._count = count
            self.redraw()

    def set_auto_tile(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._auto_tile != enabled:
            self._auto_tile = enabled
            self.redraw()

    def set_single_window(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._single_window != enabled:
            self._single_window = enabled
            self.redraw()

    def set_single_label(self, label: str) -> None:
        label = label or "shared window"
        if self._single_label != label:
            self._single_label = label
            self.redraw()

    # -- drawing -----------------------------------------------------------

    def redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 2 or height < 2:
            return

        if self._single_window and self._count > 1:
            self._draw_panes(width, height)
        elif self._auto_tile:
            self._draw_grid(width, height)
        else:
            self._draw_untiled(width, height)

    def _draw_panes(self, width: int, height: int) -> None:
        padding = 24
        x0, y0 = padding, padding
        outer_w = width - 2 * padding
        outer_h = height - 2 * padding
        if outer_w < 20 or outer_h < 20:
            return

        self.create_rectangle(
            x0, y0, x0 + outer_w, y0 + outer_h, fill=_PANE_FILL, outline=""
        )

        # Same column/row split the real launcher will use.
        cols, _rows = grid_shape(self._count)
        cols = min(cols, self._count)
        base, extra = divmod(self._count, cols)
        per_column = [base + (1 if i < extra else 0) for i in range(cols)]

        inner_x = x0 + 6
        inner_y = y0 + 30
        inner_w = outer_w - 12
        inner_h = outer_h - 50
        gap = 3
        col_w = (inner_w - (cols - 1) * gap) / cols

        for col, rows_here in enumerate(per_column):
            cx = inner_x + col * (col_w + gap)
            row_h = (inner_h - (rows_here - 1) * gap) / max(rows_here, 1)
            for row in range(rows_here):
                cy = inner_y + row * (row_h + gap)
                self.create_rectangle(
                    cx,
                    cy,
                    cx + col_w,
                    cy + row_h,
                    fill="#3d74b8",
                    outline="#2f5c94",
                )

        self.create_text(
            width / 2,
            y0 + 16,
            text=self._single_label,
            fill="#ffffff",
            font=("Segoe UI", 12, "bold"),
        )
        self.create_text(
            width / 2,
            y0 + outer_h - 12,
            text=f"{self._count} panes",
            fill="#e8eefc",
            font=("Segoe UI", 9),
        )

    def _draw_grid(self, width: int, height: int) -> None:
        cols, rows = grid_shape(self._count)
        cells = grid_cells(self._count, width, height - 18, padding=20, gap=5)

        for index, (x, y, cell_w, cell_h) in enumerate(cells):
            t = index / max(self._count - 1, 1)
            fill = _mix(_TILE_FROM, _TILE_TO, t)
            self.create_rectangle(
                x, y, x + cell_w, y + cell_h, fill=fill, outline=""
            )
            size = max(8, int(min(cell_w, cell_h) * 0.35))
            if cell_w > 24 and cell_h > 20:
                self.create_text(
                    x + cell_w / 2,
                    y + cell_h / 2,
                    text=str(index + 1),
                    fill="#ffffff",
                    font=("Segoe UI", size, "bold"),
                )

        self.create_text(
            width / 2,
            height - 10,
            text=f"{self._count} terminals — {cols}×{rows} grid",
            fill=PALETTE["fg_dim"],
            font=("Segoe UI", 9),
        )

    def _draw_untiled(self, width: int, height: int) -> None:
        padding = 20
        count = min(self._count, 12)
        cols = min(count, 4)
        rows = math.ceil(count / cols) if cols else 1

        card_w, card_h = 34, 24
        span_x = width - 2 * padding - card_w
        span_y = height - 2 * padding - card_h - 18
        step_x = span_x / max(cols - 1, 1) if cols > 1 else 0
        step_y = span_y / max(rows - 1, 1) if rows > 1 else 0

        for index in range(count):
            col = index % cols
            row = index // cols
            x = padding + col * step_x + row * 3
            y = padding + row * step_y + col * 2
            t = index / max(count - 1, 1)
            self.create_rectangle(
                x,
                y,
                x + card_w,
                y + card_h,
                fill=_mix(_TILE_FROM, _TILE_TO, t),
                outline=PALETTE["border"],
            )

        self.create_text(
            width / 2,
            height - 10,
            text=f"{self._count} terminals (no tiling)",
            fill=PALETTE["fg_dim"],
            font=("Segoe UI", 9),
        )
