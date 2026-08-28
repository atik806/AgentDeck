"""The interface every platform backend implements."""

from __future__ import annotations

import abc
from typing import Callable

from models import LaunchResult, TerminalInfo

StatusCallback = Callable[[str], None]


class TerminalBackend(abc.ABC):
    """Launch terminals and manage their lifecycle on one platform."""

    #: Short platform identifier, e.g. ``"linux"`` / ``"windows"``.
    name: str = "generic"

    #: Emulator label chosen when the user has no saved preference.
    default_emulator: str = ""

    #: UI text for the "put everything in one window" toggle.
    single_window_title: str = "Single window"
    single_window_subtitle: str = "Share one window instead of opening several"

    # -- discovery ---------------------------------------------------------

    @abc.abstractmethod
    def available_emulators(self) -> list[tuple[str, str]]:
        """Installed terminals as ``(label, command)`` pairs, best first."""

    def supports_single_window(self) -> bool:
        """True when several shells can share one window on this platform."""
        return False

    @abc.abstractmethod
    def work_area(self) -> tuple[int, int, int, int]:
        """Usable desktop rect as ``(x, y, width, height)``, excluding panels."""

    # -- launching ---------------------------------------------------------

    @abc.abstractmethod
    def launch(
        self,
        count: int,
        emulator: str,
        auto_tile: bool = True,
        single_window: bool = True,
        on_status: StatusCallback | None = None,
    ) -> LaunchResult:
        """Open ``count`` terminals. Never raises; failures come back in the result."""

    # -- lifecycle ---------------------------------------------------------

    @abc.abstractmethod
    def is_alive(self, info: TerminalInfo) -> bool:
        """Whether the terminal is still running. Must not disturb the process."""

    @abc.abstractmethod
    def kill(self, info: TerminalInfo, force: bool = False) -> None:
        """Ask the terminal to close, or terminate it outright when ``force``."""

    def focus(self, info: TerminalInfo) -> bool:
        """Raise the terminal's window. False when unsupported or not found."""
        return False

    # -- shared-window sessions (tmux and friends) -------------------------

    def session_alive(self, session_name: str) -> bool:
        return False

    def session_pane_ids(self, session_name: str) -> list[str]:
        return []

    def kill_session(self, session_name: str) -> None:
        pass

    def kill_pane(self, pane_id: str) -> None:
        """Close one pane of a shared window, where panes are addressable."""
        pass
