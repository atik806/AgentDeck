"""Windows 11 DWM backdrops -- the real acrylic / mica behind the window.

Pure ``ctypes``, no third-party dependency and no Qt import, same rule as
``global_hotkey.py`` and ``secret_store.py``: it has to import cleanly on Linux
(where every function is an inert no-op) so ``terminal_panel`` can call it
unconditionally.

**The recipe matters, and it is not the obvious one.** Setting
``DWMWA_SYSTEMBACKDROP_TYPE`` on a window whose Qt side has
``WA_TranslucentBackground`` renders *solid black* -- the DWM call returns
``S_OK`` and lies. The backdrop only paints into the window's extended frame,
so ``DwmExtendFrameIntoClientArea`` with the all -1 "sheet of glass" margins is
mandatory. Measured on build 26200; all three backdrop types behave the same
way. So the caller's half of the contract is:

    win.setAttribute(Qt.WA_TranslucentBackground, True)
    window_glass.apply(int(win.winId()), "acrylic", dark=True)

and the window's own QSS background must carry alpha (``rgba(...)``), or an
opaque child just covers the backdrop up again.
"""

from __future__ import annotations

import sys

__all__ = ["STYLES", "supported", "apply", "clear", "set_dark_titlebar"]

#: The window styles ``config["window_style"]`` may hold. "solid" means "don't
#: call us"; it is here so callers can validate against one list.
STYLES = ("solid", "acrylic", "mica")

# -- DWM constants (dwmapi.h) ------------------------------------------------
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_SYSTEMBACKDROP_TYPE = 38

_DWMSBT_NONE = 1
_DWMSBT_MAINWINDOW = 2       # Mica -- the desktop wallpaper, heavily blurred
_DWMSBT_TRANSIENTWINDOW = 3  # Acrylic -- whatever is behind the window

_BACKDROP = {"acrylic": _DWMSBT_TRANSIENTWINDOW, "mica": _DWMSBT_MAINWINDOW}

#: Windows 11 22H2. ``DWMWA_SYSTEMBACKDROP_TYPE`` exists but is ignored below
#: this, which would leave the window transparent-with-no-backdrop = black.
_MIN_BUILD = 22621


def _win_build() -> int:
    if sys.platform != "win32":
        return 0
    try:
        return int(sys.getwindowsversion().build)
    except Exception:  # noqa: BLE001 - detection is best-effort
        return 0


def supported() -> bool:
    """True when a DWM backdrop will actually render on this machine.

    False on every non-Windows platform and on Windows 10 / early Windows 11,
    where the caller falls back to plain ``setWindowOpacity``.
    """
    if sys.platform != "win32" or _win_build() < _MIN_BUILD:
        return False
    try:
        import ctypes

        ctypes.windll.dwmapi  # noqa: B018 - presence check
        return True
    except Exception:  # noqa: BLE001 - no dwmapi = no backdrop
        return False


def _set_attr(hwnd: int, attr: int, value: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        v = ctypes.c_int(int(value))
        rc = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(attr),
            ctypes.byref(v), ctypes.sizeof(v),
        )
        return rc == 0
    except Exception:  # noqa: BLE001 - a themed window is never worth a crash
        return False


def _extend_frame(hwnd: int, sheet: bool) -> bool:
    """Extend (or retract) the frame into the client area.

    ``sheet=True`` passes the all -1 margins that turn the whole client area
    into frame -- the bit that makes a backdrop visible at all.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _MARGINS(ctypes.Structure):
            _fields_ = [
                ("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int),
            ]

        m = _MARGINS(-1, -1, -1, -1) if sheet else _MARGINS(0, 0, 0, 0)
        rc = ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
            wintypes.HWND(hwnd), ctypes.byref(m),
        )
        return rc == 0
    except Exception:  # noqa: BLE001
        return False


def set_dark_titlebar(hwnd: int, dark: bool) -> bool:
    """Match the native title bar to the app's light/dark mode.

    Useful on its own -- it works in the solid style too, and is the only way
    the OS-drawn caption follows :func:`theme.mode`.
    """
    if not supported():
        return False
    return _set_attr(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)


def apply(hwnd: int, style: str, dark: bool = True) -> bool:
    """Turn on the ``style`` backdrop for ``hwnd``. False = caller should fall back.

    The caller must already have set ``WA_TranslucentBackground`` on the
    window, and must give it an ``rgba(...)`` background -- see the module
    docstring.
    """
    backdrop = _BACKDROP.get(str(style or "").strip().lower())
    if backdrop is None or not supported() or not hwnd:
        return False
    set_dark_titlebar(hwnd, dark)
    # Order matters only in that both must happen; the frame extension is what
    # actually makes the backdrop visible (see the module docstring).
    if not _extend_frame(hwnd, sheet=True):
        return False
    return _set_attr(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, backdrop)


def clear(hwnd: int, dark: bool = True) -> None:
    """Back to an ordinary opaque window. Safe to call when never applied."""
    if not supported() or not hwnd:
        return
    _set_attr(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_NONE)
    _extend_frame(hwnd, sheet=False)
    set_dark_titlebar(hwnd, dark)
