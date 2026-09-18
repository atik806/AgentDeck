"""Offline tests for the DWM backdrop shim.

    .venv\\Scripts\\python.exe test_window_glass.py

No Qt and no window: the point of ``window_glass`` is that every entry point is
safe to call unconditionally -- off Windows, on an old Windows, and with a
handle that was never a window -- so that is what this checks.
"""

import sys

import window_glass

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


# ---------------------------------------------------------------------------
print("[1] the module is importable and honest about the platform")
check("STYLES lists solid + the two backdrops",
      window_glass.STYLES == ("solid", "acrylic", "mica"))
sup = window_glass.supported()
check("supported() returns a bool", isinstance(sup, bool))
if sys.platform != "win32":
    check("never supported off Windows", sup is False)
else:
    build = sys.getwindowsversion().build
    check(f"supported() agrees with the build number ({build})",
          sup == (build >= 22621))

print("[2] apply() rejects what it cannot do, and never raises")
check("an unknown style is refused", window_glass.apply(1, "frosted") is False)
check("solid is not a backdrop, so it is refused",
      window_glass.apply(1, "solid") is False)
check("a zero handle is refused", window_glass.apply(0, "acrylic") is False)
check("None style is refused", window_glass.apply(1, None) is False)
# A handle that is not a window: DwmSetWindowAttribute returns a failure HRESULT
# rather than crashing, and we must surface that as False rather than a raise.
check("a bogus handle returns False, not an exception",
      window_glass.apply(0xDEAD, "acrylic") is False)
check("apply returns a bool", isinstance(window_glass.apply(0xDEAD, "mica"), bool))

print("[3] clear() and set_dark_titlebar() are no-ops, never raises")
try:
    window_glass.clear(0)
    window_glass.clear(0xDEAD)
    window_glass.clear(0xDEAD, dark=False)
    ok = True
except Exception as exc:  # noqa: BLE001 - that is the bug we are testing for
    ok = False
    print(f"       raised {exc!r}")
check("clear() survives a zero and a bogus handle", ok)
check("set_dark_titlebar returns a bool",
      isinstance(window_glass.set_dark_titlebar(0xDEAD, True), bool))
check("set_dark_titlebar(0) is False", window_glass.set_dark_titlebar(0, True) is False)

print("[4] the styles line up with theme.GLASS_STYLES")
import theme  # noqa: E402 - imported late; this file is otherwise Qt-free

check("window_glass.STYLES == theme.GLASS_STYLES",
      tuple(window_glass.STYLES) == tuple(theme.GLASS_STYLES))
import config  # noqa: E402

check("...and == config.CONFIG_CHOICES['window_style']",
      tuple(window_glass.STYLES) == tuple(config.CONFIG_CHOICES["window_style"]))

# ---------------------------------------------------------------------------
print(f"\n{_passed} passed, {_failed} failed")
sys.stdout.flush()
raise SystemExit(1 if _failed else 0)
