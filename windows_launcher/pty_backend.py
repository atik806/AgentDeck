"""Real pty sessions -- one per pane. Platform dispatch.

Windows and POSIX (Linux/macOS) put a real terminal behind a shell through
completely different kernel APIs (ConPTY vs. a POSIX pty), so the two
implementations live in separate modules -- ``_pty_backend_win.py`` and
``_pty_backend_posix.py`` -- each importing only its own platform's
dependency (``pywinpty`` vs. ``ptyprocess``). This module just re-exports the
right one, so every other file keeps doing ``from pty_backend import
DEFAULT_SHELL, PtySession`` unchanged.

Both implementations expose the identical contract:

    class PtySession(QObject):
        output: Signal(str)      # decoded text, UTF-8-boundary-safe
        exited: Signal(int)      # exit code, emitted once
        def __init__(self, shell=DEFAULT_SHELL, rows=24, cols=80,
                     cwd=None, parent=None): ...
        error: Optional[str]     # property: why the spawn failed, or None
        pid: int                 # property
        def is_alive(self) -> bool: ...
        def has_child_process(self) -> bool: ...
        def write(self, data: str) -> None: ...
        def resize(self, rows: int, cols: int) -> None: ...
        def close(self) -> None: ...

    def available_shells() -> list[tuple[str, str, list[str]]]: ...
    def resolve_shell(key: str = DEFAULT_SHELL) -> tuple[str, list[str]]: ...
    DEFAULT_SHELL: str
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from _pty_backend_win import (  # noqa: F401
        DEFAULT_SHELL,
        PtySession,
        available_shells,
        resolve_shell,
    )
else:
    from _pty_backend_posix import (  # noqa: F401
        DEFAULT_SHELL,
        PtySession,
        available_shells,
        resolve_shell,
    )

__all__ = ["PtySession", "available_shells", "resolve_shell", "DEFAULT_SHELL"]
