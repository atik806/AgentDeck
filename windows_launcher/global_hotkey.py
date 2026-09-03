"""A Windows system-wide hotkey, delivered as a Qt signal.

The in-app ``Ctrl+Shift+X`` (a ``QAction`` with ``ApplicationShortcut``) only
fires while an AgentDeck window has focus. This registers the same combo with
the OS via ``RegisterHotKey`` so it works from any app, and turns each press
into :attr:`GlobalHotkey.activated`.

Pure ``ctypes`` + ``PySide6.QtCore`` -- no third-party dependency, and
``RegisterHotKey`` is a benign call (not a keyboard hook), so it doesn't draw
SmartScreen / AV attention. Non-Windows or a failed registration is reported on
:attr:`failed` and never raises; the caller keeps the focused ``QAction`` as a
fallback.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Optional, Tuple

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

__all__ = ["GlobalHotkey", "parse_hotkey"]

_WM_HOTKEY = 0x0312
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000
_HOTKEY_ID = 0xA9D0  # arbitrary, per-process

_MODS = {
    "ctrl": _MOD_CONTROL, "control": _MOD_CONTROL,
    "alt": _MOD_ALT, "shift": _MOD_SHIFT,
    "win": _MOD_WIN, "meta": _MOD_WIN, "super": _MOD_WIN,
}


def _vk_map() -> dict:
    m: dict = {}
    for c in range(ord("A"), ord("Z") + 1):
        m[chr(c).lower()] = c
    for d in range(0, 10):
        m[str(d)] = 0x30 + d
    for i in range(1, 25):
        m[f"f{i}"] = 0x6F + i          # VK_F1 = 0x70
    m.update({
        "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
        "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "insert": 0x2D,
        "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
        "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28,
        "left": 0x25, "right": 0x27,
        "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
        "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
    })
    return m


_VK = _vk_map()


def parse_hotkey(sequence: str) -> Optional[Tuple[int, int]]:
    """``"Ctrl+Shift+X"`` -> ``(MOD_CONTROL|MOD_SHIFT|MOD_NOREPEAT, 0x58)``.

    Returns ``None`` for an empty / unparseable / modifier-only sequence.
    """
    if not sequence:
        return None
    mods = _MOD_NOREPEAT
    key_vk: Optional[int] = None
    key_name = ""
    for raw in str(sequence).replace("-", "+").split("+"):
        part = raw.strip().lower()
        if not part:
            continue
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _VK:
            key_vk = _VK[part]
            key_name = part
        else:
            return None
    if key_vk is None:
        return None
    # A bare key with no modifier is only sensible for the F-keys (F1..F24);
    # anything else would swallow that key everywhere.
    if mods == _MOD_NOREPEAT and not (key_name.startswith("f") and key_name[1:].isdigit()):
        return None
    return mods, key_vk


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("time", ctypes.c_uint),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


class GlobalHotkey(QAbstractNativeEventFilter, QObject):
    """Registers one system-wide hotkey and emits :attr:`activated` on press."""

    activated = Signal()
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        QAbstractNativeEventFilter.__init__(self)
        QObject.__init__(self, parent)
        self._installed = False
        self._bound: Optional[Tuple[int, int]] = None
        self._supported = sys.platform == "win32"

    # -- lifecycle -----------------------------------------------------------

    def install(self, app) -> None:
        if self._installed or not self._supported or app is None:
            if not self._supported:
                self.failed.emit("Global hotkeys are Windows-only.")
            return
        app.installNativeEventFilter(self)
        self._installed = True

    def bind(self, sequence: str) -> bool:
        """(Re)register ``sequence``. Returns True on success."""
        if not self._supported:
            self.failed.emit("Global hotkeys are Windows-only.")
            return False
        self.unbind()
        parsed = parse_hotkey(sequence)
        if parsed is None:
            self.failed.emit(f"Can't read the hotkey {sequence!r}.")
            return False
        mods, vk = parsed
        try:
            ok = bool(ctypes.windll.user32.RegisterHotKey(None, _HOTKEY_ID, mods, vk))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Couldn't register the hotkey: {exc}")
            return False
        if not ok:
            self.failed.emit(
                f"{sequence} is already taken by another program — "
                "pick a different combo in Settings."
            )
            return False
        self._bound = parsed
        return True

    def unbind(self) -> None:
        if self._bound is None or not self._supported:
            self._bound = None
            return
        try:
            ctypes.windll.user32.UnregisterHotKey(None, _HOTKEY_ID)
        except Exception:  # noqa: BLE001
            pass
        self._bound = None

    def dispose(self, app=None) -> None:
        self.unbind()
        if self._installed and app is not None:
            try:
                app.removeNativeEventFilter(self)
            except Exception:  # noqa: BLE001
                pass
        self._installed = False

    # -- native events -----------------------------------------------------

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        try:
            if self._bound is not None and bytes(event_type) == b"windows_generic_MSG":
                msg = _MSG.from_address(int(message))
                if msg.message == _WM_HOTKEY and (int(msg.wParam or 0) == _HOTKEY_ID):
                    self.activated.emit()
        except Exception:  # noqa: BLE001 - a filter must never raise into Qt
            pass
        return False, 0
