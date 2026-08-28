"""Tracks the terminals this app has launched and keeps their status current.

All process and window operations go through the platform backend — nothing here
touches ``os.kill`` or shells out to ``tmux`` directly. That matters beyond
tidiness: ``os.kill(pid, 0)`` is a harmless liveness probe on POSIX but calls
``TerminateProcess`` on Windows, so the old implementation killed every terminal
it polled.
"""

from __future__ import annotations

from typing import Callable

from models import MODE_STANDALONE, MODE_TMUX, TerminalInfo
from platforms import get_backend

__all__ = ["TerminalInfo", "TerminalManager", "infos_from_result"]


def infos_from_result(result) -> list[TerminalInfo]:
    """Turn a :class:`~models.LaunchResult` into the rows to track.

    One row per tmux pane, one per launched window, or one for the whole
    shared window when panes aren't individually addressable (Windows Terminal
    gives us no per-pane handle).
    """
    infos: list[TerminalInfo] = []

    if result.mode == MODE_TMUX:
        if result.panes:
            for pane in result.panes:
                infos.append(
                    TerminalInfo(
                        pid=pane.get("pid", 0),
                        emulator=result.emulator,
                        mode=MODE_TMUX,
                        session_name=result.session_name,
                        pane_id=pane.get("pane_id"),
                        index=pane.get("index", 0),
                    )
                )
        else:
            for index, pid in enumerate(result.pids):
                infos.append(
                    TerminalInfo(
                        pid=pid,
                        emulator=result.emulator,
                        mode=MODE_TMUX,
                        session_name=result.session_name,
                        index=index,
                    )
                )
        return infos

    if result.windows:
        for entry in result.windows:
            hwnd = entry.get("hwnd")
            infos.append(
                TerminalInfo(
                    pid=entry.get("pid", 0),
                    emulator=result.emulator,
                    mode=result.mode,
                    index=entry.get("index", 0),
                    hwnd=hwnd,
                    pane_count=result.pane_count,
                    detached=hwnd is None,
                )
            )
        return infos

    for index, pid in enumerate(result.pids):
        infos.append(
            TerminalInfo(
                pid=pid,
                emulator=result.emulator,
                mode=result.mode or MODE_STANDALONE,
                index=index,
                pane_count=result.pane_count,
            )
        )
    return infos


class TerminalManager:
    def __init__(self, backend=None):
        self.terminals: list[TerminalInfo] = []
        self._listeners: list[Callable] = []
        self._backend = backend

    @property
    def backend(self):
        if self._backend is None:
            self._backend = get_backend()
        return self._backend

    # -- membership --------------------------------------------------------

    def add(self, info: TerminalInfo) -> None:
        self.terminals.append(info)
        self._notify()

    def add_all(self, infos: list[TerminalInfo]) -> None:
        self.terminals.extend(infos)
        self._notify()

    def add_result(self, result) -> list[TerminalInfo]:
        """Track everything a launch produced. Returns the new rows."""
        infos = infos_from_result(result)
        if infos:
            self.add_all(infos)
        return infos

    def get_running_count(self) -> int:
        return sum(1 for t in self.terminals if t.status == "running")

    # -- closing -----------------------------------------------------------

    def close_terminal(self, info: TerminalInfo, force: bool = False) -> bool:
        backend = self.backend

        if info.mode == MODE_TMUX and info.session_name:
            if info.pane_id:
                backend.kill_pane(info.pane_id)
                info.status = "stopped"
                if not backend.session_alive(info.session_name):
                    self._mark_session_stopped(info.session_name)
            else:
                backend.kill_session(info.session_name)
                backend.kill(info, force)
                info.status = "stopped"
        else:
            backend.kill(info, force)
            info.status = "stopped"

        self._cleanup_stopped()
        self._notify()
        return True

    def close_all(self, force: bool = False) -> None:
        backend = self.backend
        sessions = {
            t.session_name
            for t in self.terminals
            if t.mode == MODE_TMUX and t.session_name
        }
        for session in sessions:
            backend.kill_session(session)

        for info in self.terminals:
            # Panes die with their session; only real processes need killing.
            if info.mode == MODE_TMUX and info.pane_id:
                continue
            backend.kill(info, force)

        self.terminals.clear()
        self._notify()

    # -- interaction -------------------------------------------------------

    def focus_terminal(self, info: TerminalInfo) -> bool:
        return self.backend.focus(info)

    # -- polling -----------------------------------------------------------

    def update_statuses(self) -> None:
        changed = False
        session_cache: dict[str, tuple[bool, list[str]]] = {}

        for info in list(self.terminals):
            if info.status != "running":
                continue

            if info.mode == MODE_TMUX and info.session_name:
                alive = self._tmux_alive(info, session_cache)
            else:
                alive = self.backend.is_alive(info)

            if not alive:
                info.status = "stopped"
                changed = True

        if changed:
            self._cleanup_stopped()
            self._notify()

    def _tmux_alive(
        self, info: TerminalInfo, cache: dict[str, tuple[bool, list[str]]]
    ) -> bool:
        session = info.session_name
        if session not in cache:
            backend = self.backend
            if backend.supports_single_window():
                alive = backend.session_alive(session)
                panes = backend.session_pane_ids(session) if alive else []
            else:
                # No tmux on this machine, so nothing to ask. Fall back to the pid.
                alive, panes = backend.is_alive(info), []
            cache[session] = (alive, panes)

        session_alive, panes = cache[session]
        if not session_alive:
            return False
        if info.pane_id:
            # An empty listing means we couldn't enumerate, not that the pane died.
            return info.pane_id in panes if panes else True
        return True

    # -- listeners ---------------------------------------------------------

    def on_change(self, callback: Callable) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in self._listeners:
            callback(list(self.terminals))

    def _cleanup_stopped(self) -> None:
        self.terminals = [t for t in self.terminals if t.status == "running"]

    def _mark_session_stopped(self, session_name: str) -> None:
        for info in self.terminals:
            if info.session_name == session_name and info.status == "running":
                info.status = "stopped"
