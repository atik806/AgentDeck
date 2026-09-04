"""Offline tests for the light / dark theme module + the panel's toggle.

    .venv\\Scripts\\python.exe test_theme.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ADK_NO_VOICE_PREWARM", "1")

from PySide6.QtWidgets import QApplication

import theme
from config import DEFAULT_CONFIG
from vt_screen import Palette

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


# ---------------------------------------------------------------------------
print("[1] init resolves the config preference")
check("explicit dark", theme.init({"theme": "dark"}) == "dark")
check("explicit light", theme.init({"theme": "light"}) == "light")
check("garbage falls back to a valid mode", theme.init({"theme": "chartreuse"}) in theme.MODES)
check("system resolves to a valid mode", theme.init({"theme": "system"}) in theme.MODES)

print("[2] colour tokens differ between modes and are always hex")
theme.set_mode("dark")
dark_bg = theme.color("toolbar_bg")
theme.set_mode("light")
light_bg = theme.color("toolbar_bg")
check("toolbar_bg changes with the mode", dark_bg != light_bg)
check("returns a hex string", light_bg.startswith("#") and len(light_bg) == 7)
check("an unknown token still returns something", theme.color("nope").startswith("#"))
check("explicit override ignores the current mode",
      theme.color("toolbar_bg", "dark") == dark_bg)

print("[3] ansi palette has all 16 slots per mode")
for m in theme.MODES:
    slots = theme.ansi(m)
    check(f"{m}: 16 ansi slots", len(slots) == 16)
    check(f"{m}: 'red' present and hex", str(slots.get("red", "")).startswith("#"))

print("[4] toggle + the changed signal")
theme.set_mode("dark")
seen = []
theme.manager().changed.connect(seen.append)
new = theme.toggle()
check("toggle returns the new mode", new == "light")
check("changed fired once with the new mode", seen == ["light"])
seen.clear()
theme.set_mode("light")  # already light
check("set_mode to the same mode is a no-op", seen == [])

print("[5] Palette follows the theme")
theme.set_mode("light")
pl = Palette()
check("light palette background is the light term bg",
      pl.BACKGROUND.name().lower() == theme.color("term_bg", "light").lower())
check("Palette records its mode", pl.mode == "light")
theme.set_mode("dark")
pd = Palette()
check("dark palette background differs", pd.BACKGROUND.name() != pl.BACKGROUND.name())
check("pyte 'brown' alias resolves", pd.resolve("brown", background=False).isValid())
check("pyte typo 'bfightmagenta' alias resolves",
      pd.resolve("bfightmagenta", background=True).isValid())

print("[6] apply_palette doesn't explode")
theme.apply_palette(app)
check("app palette window colour set", app.palette().window().color().isValid())

print("[7] the panel toggles every surface without raising")
theme.set_mode("dark")
from terminal_panel import TerminalPanel

cfg = dict(DEFAULT_CONFIG)
cfg["theme"] = "dark"
panel = TerminalPanel(cfg, persist_settings=False)
panel.show()
app.processEvents()
panel._toggle_theme()
app.processEvents()
check("mode flipped to light", theme.mode() == "light")
check("config was updated", cfg["theme"] == "light")
check("toolbar restyled to the light surface",
      theme.color("toolbar_bg", "light") in panel._toolbar.styleSheet())
panel._toggle_theme()
app.processEvents()
check("flipped back to dark", theme.mode() == "dark")

# ---------------------------------------------------------------------------
print(f"\n{_passed} passed, {_failed} failed")
sys.stdout.flush()
os._exit(1 if _failed else 0)
