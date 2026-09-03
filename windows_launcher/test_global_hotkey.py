"""Offline tests for global_hotkey — parsing, register/unregister, WM_HOTKEY.

RegisterHotKey / UnregisterHotKey are stubbed; a synthetic MSG struct is fed
straight into the native event filter.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_global_hotkey.py
"""

import ctypes
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import global_hotkey
from global_hotkey import GlobalHotkey, parse_hotkey, _MSG, _WM_HOTKEY, _HOTKEY_ID

app = QApplication(sys.argv)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


NOREPEAT = 0x4000

# ---------------------------------------------------------------------------
print("[1] parse_hotkey")
check("Ctrl+Shift+X", parse_hotkey("Ctrl+Shift+X") == (0x2 | 0x4 | NOREPEAT, 0x58))
check("Ctrl+Alt+D", parse_hotkey("Ctrl+Alt+D") == (0x2 | 0x1 | NOREPEAT, 0x44))
check("F9", parse_hotkey("F9") == (NOREPEAT, 0x78))
check("case + dashes tolerated", parse_hotkey("ctrl-shift-x") == (0x2 | 0x4 | NOREPEAT, 0x58))
check("meta/win maps", parse_hotkey("Win+Space") == (0x8 | NOREPEAT, 0x20))
check("empty -> None", parse_hotkey("") is None)
check("modifier-only -> None", parse_hotkey("Ctrl+Shift") is None)
check("garbage -> None", parse_hotkey("Ctrl+Frobnicate") is None)


# ---------------------------------------------------------------------------
print("[2] bind unregisters the old then registers the new; failure surfaces")


class FakeUser32:
    def __init__(self, ok=True):
        self.ok = ok
        self.registered = []
        self.unregistered = 0

    def RegisterHotKey(self, hwnd, hk_id, mods, vk):
        self.registered.append((hk_id, mods, vk))
        return 1 if self.ok else 0

    def UnregisterHotKey(self, hwnd, hk_id):
        self.unregistered += 1
        return 1


class FakeCtypes:
    def __init__(self, u32):
        self.windll = type("W", (), {"user32": u32})()


u32 = FakeUser32(ok=True)
global_hotkey.ctypes = FakeCtypes(u32)

gk = GlobalHotkey()
fails = []
gk.failed.connect(fails.append)

check("bind returns True", gk.bind("Ctrl+Shift+X") is True)
check("registered with parsed args", u32.registered[-1] == (_HOTKEY_ID, 0x2 | 0x4 | NOREPEAT, 0x58))

check("re-bind unregisters first", gk.bind("F9") is True and u32.unregistered >= 1)
check("second register used F9", u32.registered[-1] == (_HOTKEY_ID, NOREPEAT, 0x78))

u32.ok = False
check("a taken combo -> bind False + failed()", gk.bind("Ctrl+Alt+P") is False and fails)

gk.unbind()
check("unbind unregisters", u32.unregistered >= 2)


# ---------------------------------------------------------------------------
print("[3] nativeEventFilter emits activated only for our WM_HOTKEY")
global_hotkey.ctypes = ctypes            # restore real ctypes for _MSG.from_address
u32.ok = True
global_hotkey.ctypes = FakeCtypes(u32)   # bind still stubbed
gk2 = GlobalHotkey()
gk2.bind("Ctrl+Shift+X")
hits = []
gk2.activated.connect(lambda: hits.append(1))


def feed(message_id, wparam):
    m = _MSG()
    m.message = message_id
    m.wParam = wparam
    # from_address needs a real address; _MSG is a real ctypes.Structure
    return gk2.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(m))


feed(_WM_HOTKEY, _HOTKEY_ID)
check("our hotkey fires activated", hits == [1])
feed(_WM_HOTKEY, 0x1234)
check("a different hotkey id is ignored", hits == [1])
feed(0x0100, _HOTKEY_ID)   # WM_KEYDOWN
check("a non-hotkey message is ignored", hits == [1])
feed(_WM_HOTKEY, _HOTKEY_ID)
check("a second press fires again", hits == [1, 1])

res = feed(_WM_HOTKEY, _HOTKEY_ID)
check("filter returns (False, 0) — never eats the message", res == (False, 0))


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
