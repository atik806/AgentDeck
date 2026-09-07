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

print("[8] named colour schemes")
labels = theme.scheme_labels()
check("scheme_labels lists several, Catppuccin first",
      len(labels) >= 4 and labels[0][0] == "catppuccin")
cat_accent = theme.color("accent", "dark")
theme.init({"theme": "dark", "color_scheme": "dracula"})
check("init reads color_scheme", theme.scheme() == "dracula")
check("a scheme actually re-colours a token", theme.color("accent", "dark") != cat_accent)
check("scheme tokens are still hex", theme.color("window_bg").startswith("#"))
for m in theme.MODES:
    check(f"{m}: scheme ansi still has 16 slots", len(theme.ansi(m)) == 16)
check("Dracula is dark-only", theme.scheme_is_dark_only("dracula"))
check("Catppuccin / Gruvbox have a light variant",
      not theme.scheme_is_dark_only("catppuccin")
      and not theme.scheme_is_dark_only("gruvbox"))
check("a dark-only scheme in light mode still returns a colour",
      theme.color("window_bg", "light").startswith("#"))
seen = []
theme.manager().changed.connect(seen.append)
theme.set_scheme("nord")
check("set_scheme fires changed", seen == [theme.mode()])
seen.clear()
theme.set_scheme("nord")  # same
check("set_scheme to the same scheme is a no-op", seen == [])
theme.set_scheme("no-such-scheme")
check("an unknown scheme falls back to the default", theme.scheme() == theme.DEFAULT_SCHEME)
theme.init({"theme": "dark", "color_scheme": "catppuccin"})

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

print("[9] the panel applies a colour-scheme + font-family change")
import terminal_view as _tv
panel._on_settings_scheme_changed("dracula")
app.processEvents()
check("scheme change took", theme.scheme() == "dracula")
check("scheme change kept the mode", theme.mode() == "dark")
check("a Dracula token reached the toolbar QSS",
      theme.color("toolbar_bg") in panel._toolbar.styleSheet())
panel._on_settings_font_family_changed("Consolas")
app.processEvents()
check("font family recorded app-wide", _tv.active_font_family() == "Consolas")
panel._on_settings_font_family_changed("")
panel._on_settings_scheme_changed("catppuccin")

# ---------------------------------------------------------------------------
print(f"\n{_passed} passed, {_failed} failed")
sys.stdout.flush()
os._exit(1 if _failed else 0)
