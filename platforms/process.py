"""Cross-platform process liveness and termination.

The Windows paths deliberately avoid ``os.kill``: on Windows every signal other
than CTRL_C_EVENT/CTRL_BREAK_EVENT is implemented as ``TerminateProcess``, so
``os.kill(pid, 0)`` — the usual POSIX "does this pid exist" probe — terminates
the process instead of reporting on it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

from platforms import win32

IS_WINDOWS = sys.platform == "win32"


def pid_alive(pid: int) -> bool:
    """Whether ``pid`` is running. Never signals the process."""
    return win32.pid_alive(pid)


def kill_pid(pid: int, force: bool = False) -> bool:
    """Terminate ``pid`` and its children. True if the request was delivered."""
    if pid <= 0:
        return False

    if IS_WINDOWS:
        # /T covers the child shell as well as the terminal host.
        cmd = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            cmd.append("/F")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                creationflags=win32.CREATE_NO_WINDOW,
                check=False,
            )
        except (OSError, ValueError):
            return False
        return result.returncode == 0

    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True
