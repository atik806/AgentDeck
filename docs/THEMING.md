# Theming

Everything visual in AgentDeck comes from `windows_launcher/theme.py`. This is
the reference for changing it: the three axes, the token contract, how to add a
colour scheme, and how the glass window styles actually work.

---

## The three axes

They are independent. Any combination is valid.

| Axis | Config key | Values | Owner |
|---|---|---|---|
| **Mode** | `theme` | `system` \| `light` \| `dark` | `theme.mode()` / `set_mode()` |
| **Colour scheme** | `color_scheme` | one of 18, see below | `theme.scheme()` / `set_scheme()` |
| **Window style** | `window_style` | `solid` \| `acrylic` \| `mica` | `theme.glass_style()` / `set_glass()` |

Plus two settings that only mean anything alongside a glass window style:
`window_opacity` (60–100) and `terminal_translucent` (bool).

All five roam with the signed-in account — they are in `account.CLOUD_KEYS`.

### How a change propagates

There is exactly one fan-out. Nothing polls, and nothing needs a restart.

```
Settings panel writes config + emits  (scheme_changed | glass_changed | theme_changed)
  -> terminal_panel._on_settings_*    -> theme.set_scheme / set_glass / set_mode
    -> theme.manager().changed        (a Qt signal carrying the mode string)
      -> terminal_panel._on_theme_changed
         _apply_glass()               the window style itself
         _apply_window_chrome(), _toolbar_qss(), _style_status_bar(), ...
         sidebar / plugins / notes / routines / skills / worktrees / settings
           .apply_theme()
         each Workspace.apply_theme() -> TerminalPane -> TerminalView
           -> TerminalCanvas.apply_theme() -> rebuilds vt_screen.Palette
```

Two widgets subscribe to `theme.manager().changed` on their own rather than
through the panel: `button_fx.py` and `voice_overlay.py`.

---

## The token contract

`theme.color(token)` **always returns an opaque 6-digit hex string** — in every
mode, every scheme and every window style. Around 26 call sites do
`QColor(theme.color(...))` to paint icons and glows, and several use a surface
colour as an opaque knockout (`notes_panel.py`, `navbar.py`). Do not change
this.

Translucency is a separate, explicit accessor:

| Function | Returns | Use for |
|---|---|---|
| `theme.color(t)` | `"#1e1e2e"` | anything painted, and any non-background QSS value |
| `theme.qcolor(t)` | opaque `QColor` | painters |
| `theme.surface(t)` | `"#1e1e2e"` **or** `"rgba(30, 30, 46, 0.850)"` | QSS `background:` only |
| `theme.qcolor_surface(t)` | `QColor` with alpha | a painter that must honour translucency (`vt_screen.Palette`) |

`surface()` is identical to `color()` in the solid style, so moving a call site
over is free — there is a test asserting exactly that, for all 57 tokens in
both modes. It only diverges for tokens in `theme._SURFACE_TOKENS`, and for
`term_bg` only when the terminal opt-in is on.

**Never pass `surface()` to `QColor(...)`.** Qt stylesheets parse `rgba(...)`
but not `#RRGGBBAA`; `QColor` is the other way round. That mismatch is the
reason there are two functions instead of one.

### Adding a token

Add it to `_DARK` and `_LIGHT` (both, or `color()` falls back across modes) and
to the `_expand()` mapping so every scheme gets it. A token that exists in
neither resolves to magenta `#ff00ff` — that is the designed tell, and
`test_theme.py` asserts no scheme hits it.

---

## Colour schemes

18 of them. Catppuccin (the default, matching the splash and logo) is authored
token-by-token as `_DARK` / `_LIGHT`. Every other scheme is a compact ~20-value
spec run through `_expand()`, so they all map onto the token table the same way
and read as one considered set rather than a pile of hand-picked values.

### Adding one

1. **`theme.py`** — write a spec dict with **all 20 `_expand()` keys**
   (`crust mantle base layer1 surface surface_hi overlay overlay_hi fg fg_dim
   fg_faint accent accent_hi accent_soft accent2 on_accent danger warn ok
   cursor`, plus optional `selection`) and a 16-slot `"ansi"` map. `_expand()`
   uses `s["key"]`, not `.get`, so a missing key is an import-time `KeyError`.
2. Register it in `_SCHEMES`. Dict order is picker order. Supplying `"light"` /
   `"ansi_light"` makes it a light+dark pair; leaving them out makes it
   dark-only and the Settings picker says so automatically.
3. **`config.py`** — add the key to `CONFIG_CHOICES["color_scheme"]`.

> **Step 3 is not optional.** `load_config()` hard-resets any value missing from
> `CONFIG_CHOICES` back to the default with a `[WARN]`. Skip it and the scheme
> works perfectly for one session, then silently reverts to Catppuccin on the
> next launch. `test_theme.py` §8 asserts the two lists match, in both
> directions.

Sanity-check contrast before committing: `fg` on `base`, `fg_dim` on `surface`
and `fg_faint` on `layer1` should each clear ~4.5:1, and `accent_soft` has to
be distinguishable from `surface` — it is the active-nav background.

---

## Window styles (glass)

### What actually renders it

The recipe is not the obvious one, and was found by probing rather than from
the docs. On Windows 11 build 26200, all four of these DWM calls return
`S_OK` — but only one combination renders anything but solid black:

| Recipe | Result |
|---|---|
| `WA_TranslucentBackground` + `DWMWA_SYSTEMBACKDROP_TYPE` | **black** |
| no `WA_TranslucentBackground` + backdrop | **black** |
| backdrop = Mica, no frame extension | **black** |
| `WA_TranslucentBackground` + **`DwmExtendFrameIntoClientArea(-1,-1,-1,-1)`** + backdrop | **works** |

The backdrop only paints into the window's *extended frame*, so the all `-1`
"sheet of glass" margins are mandatory. With them, acrylic, mica and mica-alt
all work. `window_glass.py` implements exactly that, plus
`DWMWA_USE_IMMERSIVE_DARK_MODE` so the OS-drawn title bar follows the mode.

### The handle swap

Toggling `WA_TranslucentBackground` on a window that is already visible makes
Qt destroy and recreate the native window. The `HWND` you had is then dead, the
DWM calls against it still return `S_OK`, and nothing renders.

`terminal_panel._apply_glass()` therefore sets the attribute and applies the
backdrop **twice** — once immediately and once via `QTimer.singleShot(0, ...)`,
by which point `winId()` returns the handle that actually exists. The attribute
is also set once in `__init__`, before the first `show()`, so the ordinary
startup path never pays for a recreation.

### The fallback chain

`window_glass.supported()` is true only on Windows 11 22H2+ (build 22621). Off
Windows, and on Windows 10, `_apply_glass` falls back to
`setWindowOpacity(window_opacity / 100)`. That fades the whole window, text
included — a different look, not a lesser version of the same one, which is why
the Settings hint says so out loud rather than letting the user conclude the
feature is broken.

### Translucent terminal panes

Off by default; `terminal_translucent` turns it on, and it does nothing unless
a glass style is active (`theme.terminal_translucent()` enforces that).

Two non-obvious things make it work, both guarded by `test_theme.py` §13:

* **The base fill must replace, not blend.** `TerminalCanvas.paintEvent` fills
  the damage rect with `CompositionMode_Source` when the ground has alpha.
  With the default `SourceOver`, a dirty-row repaint composites over the row's
  own previous pixels, so it darkens a little more every frame until it goes
  black.
* **`WA_OpaquePaintEvent` stays ON.** It reads like a lie for a see-through
  widget, and is not: it promises we paint *every pixel* of the damage rect,
  not that the result is opaque, and the Source-mode fill keeps that promise
  alpha and all. Turning it off — the obvious reading — makes Qt erase the
  whole widget before each paint, so a dirty-row repaint wipes every row
  outside the damage band. The terminal comes up blank except the cursor line.

Measured with `bench_terminal.py`: paint cost with the opt-in **off** is
unchanged from before the feature (within ±2.5% run-to-run noise).

### Which surfaces dissolve

`theme._SURFACE_TOKENS` — window, toolbar, sidebar, status bar, cards, menus
and pane headers. Text, borders and accents are deliberately excluded; fading
those is how a theme becomes unreadable.

`window_bg` and `term_bg` (`_GROUND_TOKENS`) get exactly the opacity the slider
says. Everything layered above them is nudged `_CHROME_BOOST` (0.08) more
solid, so the chrome still reads as sitting *on* the window rather than
dissolving into the same flat sheet.

---

## QSS traps

**Never style `QComboBox::drop-down` or `::down-arrow`.** Touching either
subcontrol makes Qt stop painting the native chevron, and a combo with no
arrow reads as a text field — which is exactly what every picker in Settings,
the handoff dialog, the new-workspace dialog, Routines, Skills and the toolbar
looked like until 2026-09-19. The usual web workaround (a zero-sized box with
transparent left/right borders and a coloured top border) does **not** port:
Qt draws borders as plain rectangles rather than mitring them, so it paints a
short bar. Style the `QComboBox` itself — background, border, radius, padding,
`:hover`, `:focus`, `:disabled` — and leave the arrow to the platform style,
which already follows the palette in both modes.

**A `QSlider` under a stylesheet ignores `setTickPosition`.** Ticks are drawn
by the base style, and `QStyleSheetStyle` does not forward them, so the call
is silently inert — don't add one expecting marks to appear.

**A styled slider handle needs vertical room.** QSS sizes the handle from the
groove height plus the handle's negative margins, but the slider's own
`sizeHint` is shorter than that sum, so the handle renders as a clipped pill.
Give the widget a `min-height` (the Settings panel uses 20px) if you want a
round one.

---

## What is deliberately not themed

The **front door** stays amber, by design — `setup_wizard.py`,
`login_window.py`, `trial_gate.py` and `agents_ui.py` carry their own
`_AMBER`/`_BG`/`_CARD` constants. The split is front-door versus in-app; see
`context.md` §15. `agentdeck_splash.py` is Catppuccin-locked for the same
reason.

Also still hardcoded, and a fair thing to fix next:
`terminal_panel._WS_ACCENTS`, the six workspace swatch colours, is a Catppuccin
list that does not follow the active scheme.

---

## Tests

| File | Covers |
|---|---|
| `test_theme.py` | modes, tokens, ANSI, the `changed` signal, all 18 schemes × 2 modes, the `CONFIG_CHOICES` sync, `surface()` vs `color()`, `set_glass` normalisation, a live panel restyle, and the translucent-repaint regression |
| `test_window_glass.py` | the ctypes shim is safe off Windows, on old Windows and with a bogus handle |
| `test_settings_dialog.py` §6b/§6d | the scheme picker and the window-style block |

Run them as scripts, not via pytest:

```
cd windows_launcher
.venv\Scripts\python.exe -u test_theme.py
```
