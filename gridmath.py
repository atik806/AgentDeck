"""Pure grid geometry, shared by the launchers and by both preview renderers.

Keeping this dependency-free means the cairo preview, the Tk preview and the
actual window placement can never disagree about where cell *i* belongs.
"""

from __future__ import annotations

import math


def compute_grid(count: int) -> list[tuple[int, int, int, int]]:
    """Return ``(col, row, cols, rows)`` for each of ``count`` cells."""
    if count <= 0:
        return []
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    positions = []
    for i in range(count):
        col = i % cols
        row = i // cols
        positions.append((col, row, cols, rows))
    return positions


def grid_shape(count: int) -> tuple[int, int]:
    """Return ``(cols, rows)`` for a ``count``-cell grid."""
    if count <= 0:
        return (1, 1)
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    return (cols, rows)


def column_counts(count: int, cols: int) -> list[int]:
    """Split ``count`` cells across ``cols`` columns as evenly as possible.

    The first ``count % cols`` columns get one extra cell, so the result is
    always within one of balanced and sums to ``count``.
    """
    if count <= 0 or cols <= 0:
        return []
    cols = min(cols, count)
    base, extra = divmod(count, cols)
    return [base + (1 if i < extra else 0) for i in range(cols)]


def tile_rects(
    count: int,
    area_x: int = 0,
    area_y: int = 0,
    area_w: int = 1920,
    area_h: int = 1080,
) -> list[tuple[int, int, int, int]]:
    """Integer pixel rects tiling ``area`` into ``count`` cells."""
    rects = []
    for col, row, cols, rows in compute_grid(count):
        cell_w = area_w // cols
        cell_h = area_h // rows
        rects.append((area_x + col * cell_w, area_y + row * cell_h, cell_w, cell_h))
    return rects


def grid_cells(
    count: int,
    width: float,
    height: float,
    padding: float = 20.0,
    gap: float = 5.0,
) -> list[tuple[float, float, float, float]]:
    """Float rects for drawing a preview of a ``count``-cell grid.

    Cells are clamped to a 10px minimum so a very high count still renders
    something visible rather than collapsing to zero-area rectangles.
    """
    if count <= 0:
        return []
    cols, rows = grid_shape(count)

    avail_w = width - 2 * padding
    avail_h = height - 2 * padding
    cell_w = max((avail_w - (cols - 1) * gap) / cols, 10.0)
    cell_h = max((avail_h - (rows - 1) * gap) / rows, 10.0)

    cells = []
    for col, row, _, _ in compute_grid(count):
        cells.append(
            (
                padding + col * (cell_w + gap),
                padding + row * (cell_h + gap),
                cell_w,
                cell_h,
            )
        )
    return cells


def even_split_fraction(remaining: int) -> float:
    """Fraction the *new* pane should take to leave equal-sized siblings.

    When carving ``n`` equal slices out of one pane by repeated splitting, the
    first split must hand ``(n-1)/n`` to the new pane so that ``1/n`` is left
    behind, the second ``(n-2)/(n-1)``, and so on. ``remaining`` is how many
    slices still have to come out of the pane being split, including itself.
    """
    if remaining <= 1:
        return 1.0
    return (remaining - 1) / remaining
