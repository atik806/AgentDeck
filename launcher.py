"""Public launching API, delegating to the backend for the running OS.

This module used to hold the Linux implementation directly. That code now lives
in :mod:`platforms.linux`, with a Windows counterpart in :mod:`platforms.windows`,
but the names below keep their original signatures so existing callers
(``ui.window`` in particular) are unaffected.
"""

from __future__ import annotations

from gridmath import compute_grid, tile_rects
from models import (
    MODE_PANES,
    MODE_STANDALONE,
    MODE_TMUX,
    LaunchResult,
    TerminalInfo,
)
from platforms import get_backend

__all__ = [
    "LaunchResult",
    "TerminalInfo",
    "MODE_STANDALONE",
    "MODE_TMUX",
    "MODE_PANES",
    "compute_grid",
    "tile_rects",
    "get_available_emulators",
    "get_default_emulator",
    "supports_single_window",
    "single_window_labels",
    "screen_geometry",
    "launch_terminals",
]


def get_available_emulators() -> list[tuple[str, str]]:
    """Terminals installed on this machine as ``(label, command)`` pairs."""
    return get_backend().available_emulators()


def get_default_emulator() -> str:
    """Label of the emulator to pre-select when the user has no preference."""
    return get_backend().default_emulator


def supports_single_window() -> bool:
    """Whether several shells can share one window here (tmux / wt panes)."""
    return get_backend().supports_single_window()


def single_window_labels() -> tuple[str, str]:
    """``(title, subtitle)`` for the shared-window toggle in the UI."""
    backend = get_backend()
    return (backend.single_window_title, backend.single_window_subtitle)


def screen_geometry() -> tuple[int, int, int, int]:
    """Usable desktop rect as ``(x, y, width, height)``."""
    return get_backend().work_area()


def launch_terminals(
    count: int,
    emulator_name: str,
    auto_tile: bool = True,
    use_tmux: bool = True,
    on_status=None,
) -> LaunchResult:
    """Open ``count`` terminals. Never raises; failures come back in the result.

    ``use_tmux`` is the historical name for what is now "share one window": tmux
    panes on Linux, Windows Terminal split panes on Windows.
    """
    return get_backend().launch(
        count,
        emulator_name,
        auto_tile=auto_tile,
        single_window=use_tmux,
        on_status=on_status,
    )
