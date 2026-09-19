"""Offline tests for the taskbar identity (AppUserModelID). Run:

    .venv\Scripts\python.exe test_app_user_model_id.py

Windows resolves a taskbar button's icon through the window's
AppUserModelID, not through the window icon: an id that matches no shortcut
resolves to nothing and the button falls back to the generic "application"
icon. Velopack stamps ``"velopack." + <packId>`` on every shortcut it creates,
so the id the app declares and the id the packer uses have to stay in step --
that is the whole point of these checks.
"""

import re
import sys
from pathlib import Path

import main
import version

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
print("[1] the app declares Velopack's shortcut identity")
check("id is velopack.<APP_ID>", main.APP_USER_MODEL_ID == f"velopack.{version.APP_ID}")
check("id is a non-empty str", isinstance(main.APP_USER_MODEL_ID, str) and main.APP_USER_MODEL_ID)
check(
    "no invented ids sneak back in",
    main.APP_USER_MODEL_ID not in ("AgentDeck.Panel", "multi-terminal.panel"),
)

print("[2] the packer reads the same constant")
build_src = (Path(__file__).resolve().parent.parent / "packaging" / "build.py").read_text(
    encoding="utf-8"
)
check('--packId is not hard-coded', '"--packId", "' not in build_src)
check('--packId comes from read_app_id()', '"--packId", read_app_id()' in build_src)
check(
    "read_app_id() parses version.APP_ID",
    re.search(r"def read_app_id\(\).*?re\.search\(.*?APP_ID", build_src, re.S) is not None,
)

print("[3] the icon the window falls back to is really shipped")
check("assets/icon.ico exists", main._ICON.exists())
check("icon.ico is a real ICO", main._ICON.read_bytes()[:4] == b"\x00\x00\x01\x00")

# ---------------------------------------------------------------------------
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
