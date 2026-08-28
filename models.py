"""Shared data types.

These live outside ``launcher`` and ``ui`` so that the platform backends can use
them without importing either package (which would be circular).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: A terminal launched as its own top-level window.
MODE_STANDALONE = "standalone"
#: Several shells sharing one window as tmux panes (Linux).
MODE_TMUX = "tmux"
#: Several shells sharing one window as native split panes (Windows Terminal).
MODE_PANES = "panes"


@dataclass
class TerminalInfo:
    pid: int
    emulator: str
    mode: str
    session_name: str | None = None
    pane_id: str | None = None
    index: int = 0
    status: str = "running"
    launched_at: float = field(default_factory=time.time)
    # Windows only: the top-level window we matched to this terminal. Liveness and
    # focus prefer this over ``pid``, because ``wt.exe`` hands off to an existing
    # WindowsTerminal.exe process and its own pid dies within milliseconds.
    hwnd: int | None = None
    # Number of shells sharing this window (MODE_PANES); 0 when not applicable.
    pane_count: int = 0
    # Launched successfully but we never found a window to track it by.
    detached: bool = False

    @property
    def display_name(self) -> str:
        if self.mode == MODE_TMUX and self.pane_id:
            return f"tmux pane {self.pane_id}"
        if self.mode == MODE_TMUX:
            return f"{self.emulator} (tmux)"
        if self.mode == MODE_PANES:
            if self.pane_count:
                return f"{self.emulator} ({self.pane_count} panes)"
            return f"{self.emulator} (panes)"
        return f"{self.emulator} #{self.index + 1}"


@dataclass
class LaunchResult:
    success: bool
    count: int
    mode: str
    emulator: str
    pids: list[int] = field(default_factory=list)
    session_name: str | None = None
    panes: list[dict] = field(default_factory=list)
    error: str | None = None
    #: Windows only: ``[{"pid": int, "hwnd": int, "index": int}, ...]``
    windows: list[dict] = field(default_factory=list)
    #: Non-fatal note for the user (e.g. some windows could not be tracked).
    warning: str | None = None
    #: Number of shells per launched window; >1 only in pane modes.
    pane_count: int = 0
