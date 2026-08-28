"""Minimal ctypes bindings for the Win32 calls this app needs.

No third-party dependencies: the whole point of the Windows support is that
``python main.py`` works on a stock CPython install.

Everything here is a no-op returning a safe default on non-Windows platforms so
that importing the module never explodes during test collection.
"""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"

# -- Win32 constants ------------------------------------------------------

SW_RESTORE = 9
SW_SHOW = 5
WM_CLOSE = 0x0010

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

SM_CXSCREEN = 0
SM_CYSCREEN = 1
SPI_GETWORKAREA = 0x0030

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x00000102

ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87

LOGPIXELSX = 88

# Process creation flags (re-exported so callers don't need the subprocess
# constants, which only exist on Windows).
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    _WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL

    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL

    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL

    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int

    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int

    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int

    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL

    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL

    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL

    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.restype = wintypes.BOOL

    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL

    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostMessageW.restype = wintypes.BOOL

    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int

    _user32.SystemParametersInfoW.argtypes = [
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    _user32.SystemParametersInfoW.restype = wintypes.BOOL

    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = wintypes.LONG

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE

    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD

    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


# -- window enumeration ---------------------------------------------------


def enum_windows() -> list[int]:
    """Handles of visible, titled, non-toolbar top-level windows."""
    if not IS_WINDOWS:
        return []

    import ctypes

    found: list[int] = []

    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        if _user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        # Tool windows are palettes and tray helpers, never terminals.
        if _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        found.append(int(hwnd))
        return True

    try:
        _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    except OSError:
        return []
    return found


def window_title(hwnd: int) -> str:
    if not IS_WINDOWS:
        return ""
    import ctypes

    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    if not IS_WINDOWS:
        return ""
    import ctypes

    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    """Pid that owns ``hwnd``, or 0."""
    if not IS_WINDOWS:
        return 0
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def is_window(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    return bool(_user32.IsWindow(hwnd))


# -- window manipulation --------------------------------------------------


def move_window(hwnd: int, x: int, y: int, width: int, height: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    return bool(
        _user32.SetWindowPos(
            hwnd,
            None,
            int(x),
            int(y),
            int(width),
            int(height),
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
    )


def focus_window(hwnd: int) -> bool:
    """Restore and raise ``hwnd``.

    Windows only lets the foreground process reassign focus, so
    ``SetForegroundWindow`` can legitimately fail. ``BringWindowToTop`` at least
    surfaces the window in that case, which is the behaviour users care about.
    """
    if not IS_WINDOWS or not is_window(hwnd):
        return False
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        _user32.ShowWindow(hwnd, SW_SHOW)
    if _user32.SetForegroundWindow(hwnd):
        return True
    return bool(_user32.BringWindowToTop(hwnd))


def close_window(hwnd: int) -> bool:
    """Ask ``hwnd`` to close, the same as clicking its X button."""
    if not IS_WINDOWS or not is_window(hwnd):
        return False
    return bool(_user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


# -- process liveness -----------------------------------------------------


def pid_alive(pid: int) -> bool:
    """Whether ``pid`` is running, without signalling it.

    This exists because ``os.kill(pid, 0)`` is not a probe on Windows: any
    signal other than a console event routes to ``TerminateProcess``, so the
    "harmless" liveness check kills the process it is asking about.
    """
    if pid <= 0:
        return False
    if not IS_WINDOWS:
        import os

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    import ctypes

    handle = _kernel32.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        # Access denied means it exists but belongs to someone else.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        # A process handle becomes signalled when the process exits, so still
        # timing out means still running.
        return _kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        _kernel32.CloseHandle(handle)


# -- display metrics ------------------------------------------------------


def work_area() -> tuple[int, int, int, int]:
    """Primary monitor's usable rect as ``(x, y, w, h)``, taskbar excluded."""
    if not IS_WINDOWS:
        return (0, 0, 1920, 1080)

    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    if _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width > 0 and height > 0:
            return (rect.left, rect.top, width, height)

    width = _user32.GetSystemMetrics(SM_CXSCREEN)
    height = _user32.GetSystemMetrics(SM_CYSCREEN)
    if width > 0 and height > 0:
        return (0, 0, width, height)
    return (0, 0, 1920, 1080)


def system_dpi() -> int:
    """Effective system DPI (96 = 100% scaling)."""
    if not IS_WINDOWS:
        return 96
    try:
        get_dpi = _user32.GetDpiForSystem
    except AttributeError:
        get_dpi = None
    if get_dpi is not None:
        try:
            get_dpi.restype = __import__("ctypes").wintypes.UINT
            value = int(get_dpi())
            if value > 0:
                return value
        except OSError:
            pass
    try:
        dc = _user32.GetDC(None)
        if dc:
            try:
                value = int(_gdi32.GetDeviceCaps(dc, LOGPIXELSX))
                if value > 0:
                    return value
            finally:
                _user32.ReleaseDC(None, dc)
    except OSError:
        pass
    return 96


def dpi_scale() -> float:
    """System scaling factor, e.g. 1.5 at 150%."""
    return system_dpi() / 96.0


_dpi_awareness_set = False


def enable_dpi_awareness() -> bool:
    """Opt into per-monitor DPI awareness.

    Without this, ``SetWindowPos`` coordinates are virtualised on scaled
    displays and tiled terminals land in the wrong place. Must run before any
    window is created, and only takes effect once per process.
    """
    global _dpi_awareness_set
    if not IS_WINDOWS or _dpi_awareness_set:
        return _dpi_awareness_set

    import ctypes

    # PER_MONITOR_AWARE_V2 (Windows 10 1703+).
    try:
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _dpi_awareness_set = True
            return True
    except (AttributeError, OSError):
        pass
    # PROCESS_PER_MONITOR_DPI_AWARE (Windows 8.1+).
    try:
        shcore = ctypes.WinDLL("shcore")
        if shcore.SetProcessDpiAwareness(2) == 0:
            _dpi_awareness_set = True
            return True
    except (AttributeError, OSError):
        pass
    try:
        if _user32.SetProcessDPIAware():
            _dpi_awareness_set = True
            return True
    except (AttributeError, OSError):
        pass
    return False
