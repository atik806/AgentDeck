"""Offline tests for the light / dark theme module + the panel's toggle.

    .venv\\Scripts\\python.exe test_theme.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ADK_NO_VOICE_PREWARM", "1")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QRegion
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

# Every scheme must survive a restart. load_config() hard-resets any value that
# is not in CONFIG_CHOICES, so a scheme added to theme._SCHEMES and nowhere else
# works for one session and then silently reverts to Catppuccin.
import config as _config

_keys = [k for k, _ in theme.scheme_labels()]
_choices = set(_config.CONFIG_CHOICES["color_scheme"])
check("every scheme is in CONFIG_CHOICES (or it reverts on restart)",
      [k for k in _keys if k not in _choices] == [])
check("CONFIG_CHOICES has no scheme that theme dropped",
      [k for k in _choices if k not in _keys] == [])

# Each scheme, in both modes: tokens resolve, ANSI is complete, and nothing
# falls through to color()'s magenta last resort.
for _k in _keys:
    theme.init({"theme": "dark", "color_scheme": _k})
    _bad = []
    for _m in theme.MODES:
        if len(theme.ansi(_m)) != 16:
            _bad.append(f"{_m}: ansi")
        for _tok in ("window_bg", "text", "accent", "term_bg", "danger", "activity"):
            _v = theme.color(_tok, _m)
            if not (_v.startswith("#") and len(_v) == 7):
                _bad.append(f"{_m}:{_tok}={_v}")
            if _v == "#ff00ff":
                _bad.append(f"{_m}:{_tok} fell through to magenta")
    check(f"scheme {_k} resolves cleanly in both modes", _bad == [])

check("the five new schemes are all present",
      all(k in _keys for k in
          ("github", "materialocean", "carbonfox", "vitesse", "midnight")))
check("GitHub and Vitesse ship a light variant",
      not theme.scheme_is_dark_only("github")
      and not theme.scheme_is_dark_only("vitesse"))
check("Midnight is a true-black ground",
      theme.color("window_bg", "dark") == "#000000"
      if theme.init({"theme": "dark", "color_scheme": "midnight"}) else False)

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

print("[10] window style (glass)")
theme.init({"theme": "dark", "color_scheme": "catppuccin"})
check("defaults to solid", theme.glass_style() == "solid" and not theme.glass_active())

# The whole point of a separate accessor: in the solid style surface() must be
# indistinguishable from color(), for every token, or swapping a QSS call site
# over would be a behaviour change.
_all_tokens = sorted(theme._DARK.keys())
for _m in theme.MODES:
    _diff = [t for t in _all_tokens if theme.surface(t, _m) != theme.color(t, _m)]
    check(f"{_m}: solid surface() == color() for all {len(_all_tokens)} tokens",
          _diff == [])

theme.set_glass("acrylic", 85, False)
check("glass_active once a backdrop is picked", theme.glass_active())
check("a background token goes rgba",
      theme.surface("window_bg").startswith("rgba("))
check("the toolbar goes rgba too", theme.surface("toolbar_bg").startswith("rgba("))
check("text stays opaque hex", theme.surface("text") == theme.color("text"))
check("borders stay opaque hex", theme.surface("border") == theme.color("border"))
check("accents stay opaque hex", theme.surface("accent") == theme.color("accent"))

# color() must keep its contract in *every* style -- 26 QColor(theme.color(...))
# painter call sites and section [2] above depend on it.
for _m in theme.MODES:
    _bad = [t for t in _all_tokens
            if not (theme.color(t, _m).startswith("#") and len(theme.color(t, _m)) == 7)]
    check(f"{_m}: color() is still 7-char hex under glass", _bad == [])

check("the window ground gets exactly the slider value",
      theme.surface("window_bg").endswith("0.850)"))
check("chrome above it is nudged more solid",
      theme.surface("toolbar_bg").endswith("0.930)"))

check("term_bg stays opaque while the opt-in is off",
      theme.surface("term_bg") == theme.color("term_bg"))
check("terminal_translucent() is False while the opt-in is off",
      theme.terminal_translucent() is False)
theme.set_glass("acrylic", 85, True)
check("term_bg goes rgba once the opt-in is on",
      theme.surface("term_bg").startswith("rgba("))
check("terminal_translucent() is True", theme.terminal_translucent() is True)
check("qcolor_surface carries the alpha",
      theme.qcolor_surface("term_bg").alpha() == round(0.85 * 255))
check("qcolor() is unaffected and stays opaque",
      theme.qcolor("term_bg").alpha() == 255)

# The opt-in alone means nothing -- translucency is a property of the window.
theme.set_glass("solid", 85, True)
check("the terminal opt-in does nothing in the solid style",
      theme.terminal_translucent() is False
      and theme.surface("term_bg") == theme.color("term_bg"))

print("[11] set_glass normalises and notifies")
theme.set_glass("solid", 85, False)
seen = []
theme.manager().changed.connect(seen.append)
theme.set_glass("mica", 85, False)
check("set_glass fires changed once", seen == [theme.mode()])
seen.clear()
theme.set_glass("mica", 85, False)  # same
check("set_glass to the same values is a no-op", seen == [])
theme.set_glass("chartreuse", 85, False)
check("an unknown style falls back to solid", theme.glass_style() == "solid")
theme.set_glass("acrylic", 5, False)
check("opacity clamps up to the floor", theme.glass_opacity() == 60)
theme.set_glass("acrylic", 5000, False)
check("opacity clamps down to the ceiling", theme.glass_opacity() == 100)
theme.set_glass("acrylic", "not a number", False)
check("garbage opacity falls back to the default",
      theme.glass_opacity() == theme.DEFAULT_OPACITY)
theme.set_glass("acrylic", 100, False)
check("a fully opaque glass window emits hex, not rgba(…, 1.000)",
      theme.surface("window_bg").startswith("#"))

check("init reads the window-style keys", (
    theme.init({"theme": "dark", "color_scheme": "catppuccin",
                "window_style": "mica", "window_opacity": 70,
                "terminal_translucent": True}) == "dark"
    and theme.glass_style() == "mica"
    and theme.glass_opacity() == 70
    and theme.terminal_translucent() is True))
check("init normalises a garbage style", (
    theme.init({"theme": "dark", "window_style": "nope", "window_opacity": -4})
    and theme.glass_style() == "solid" and theme.glass_opacity() == 60))
theme.init({"theme": "dark", "color_scheme": "catppuccin"})

print("[12] the panel survives a live window-style change")
theme.set_glass("solid", 85, False)
panel._on_settings_glass_changed()  # config still says solid -> no-op
cfg["window_style"] = "acrylic"
cfg["window_opacity"] = 75
cfg["terminal_translucent"] = True
panel._on_settings_glass_changed()
app.processEvents()
check("the panel pushed the config into theme",
      theme.glass_style() == "acrylic" and theme.glass_opacity() == 75)
check("the window QSS went translucent",
      "rgba(" in panel.styleSheet())
check("the toolbar QSS went translucent too",
      "rgba(" in panel._toolbar.styleSheet())
check("the sidebar went translucent",
      "rgba(" in panel._sidebar.styleSheet())
cfg["window_style"] = "solid"
cfg["terminal_translucent"] = False
panel._on_settings_glass_changed()
app.processEvents()
check("back to solid leaves no rgba in the window QSS",
      "rgba(" not in panel.styleSheet())
check("...nor in the toolbar", "rgba(" not in panel._toolbar.styleSheet())

print("[13] a translucent terminal repaints without smearing")
# The regression this guards: a translucent ground drawn with the default
# SourceOver blend accumulates over the pixels a dirty-row repaint leaves
# behind, so a row darkens a little more every frame until it goes black. The
# fix is a CompositionMode_Source base fill -- and, less obviously,
# WA_OpaquePaintEvent has to STAY ON so Qt does not erase the rows outside the
# damage rect (that bug blanked the whole terminal except the cursor line).
from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402,F811

from terminal_view import TerminalCanvas, preferred_font  # noqa: E402
from vt_screen import TerminalScreen, TerminalStream  # noqa: E402

theme.init({"theme": "dark", "color_scheme": "catppuccin"})
theme.set_glass("acrylic", 70, True)

_screen = TerminalScreen(40, 8, scrollback=100)
_stream = TerminalStream(_screen)
_stream.feed("hello world\r\n")
_canvas = TerminalCanvas(_screen, preferred_font(11))
_canvas.resize(320, 128)
_canvas.apply_theme()

check("a translucent pane keeps the opaque-paint promise",
      _canvas.testAttribute(Qt.WA_OpaquePaintEvent) is True)
check("the palette ground carries alpha",
      _canvas._palette.BACKGROUND.alpha() == round(0.70 * 255))


def _render(widget, damage, times=1):
    """Paint ``widget`` into a fresh ARGB image, damaging only ``damage``."""
    img = QImage(widget.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    for _ in range(times):
        p = QPainter(img)
        p.setClipRect(damage)
        widget.render(p, QPoint(0, 0), QRegion(damage))
        p.end()
    return img


# One row band, repainted many times over the same buffer -- exactly what a
# busy pane does. The pixel must land on the same colour every time.
_band = QRect(0, 0, 320, 16)
_once = _render(_canvas, _band, times=1)
_many = _render(_canvas, _band, times=30)
_a, _b = _once.pixelColor(200, 8), _many.pixelColor(200, 8)
check("30 partial repaints land on the same colour as one",
      (_a.red(), _a.green(), _a.blue(), _a.alpha())
      == (_b.red(), _b.green(), _b.blue(), _b.alpha()))
check("...and that colour is still translucent, not built up to opaque",
      _b.alpha() < 255)

# The text must survive a repaint that only damages a *different* row.
_full = _render(_canvas, QRect(0, 0, 320, 128))
_glyph_row = [_full.pixelColor(x, 8) for x in range(0, 200, 2)]
check("the first row actually drew glyphs (not a blank pane)",
      len({(c.red(), c.green(), c.blue()) for c in _glyph_row}) > 1)

theme.set_glass("solid", 85, False)
_canvas.apply_theme()
check("back to solid the ground is opaque again",
      _canvas._palette.BACKGROUND.alpha() == 255)
check("...and the opaque-paint promise is unchanged",
      _canvas.testAttribute(Qt.WA_OpaquePaintEvent) is True)
_canvas.deleteLater()

# ---------------------------------------------------------------------------
print(f"\n{_passed} passed, {_failed} failed")
sys.stdout.flush()
os._exit(1 if _failed else 0)
