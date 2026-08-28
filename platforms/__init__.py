"""Per-platform terminal launching and process control.

``get_backend()`` returns the backend for the running OS. Everything above this
package (``launcher``, ``ui``, ``cli``) talks only to the abstract interface in
:mod:`platforms.base`, so nothing outside here needs to branch on ``sys.platform``.
"""

from __future__ import annotations

import sys

from platforms.base import TerminalBackend

_backend: TerminalBackend | None = None


def get_backend() -> TerminalBackend:
    """Return the backend for this OS, constructing it once and caching it."""
    global _backend
    if _backend is None:
        if sys.platform == "win32":
            from platforms.windows import WindowsBackend

            _backend = WindowsBackend()
        else:
            # Linux, the BSDs, and macOS all get the POSIX backend. macOS lacks
            # most of the emulators in its table but tmux and iTerm-style
            # `-e` invocation still work.
            from platforms.linux import LinuxBackend

            _backend = LinuxBackend()
    return _backend


def set_backend(backend: TerminalBackend | None) -> None:
    """Override the cached backend. For tests."""
    global _backend
    _backend = backend


__all__ = ["TerminalBackend", "get_backend", "set_backend"]
