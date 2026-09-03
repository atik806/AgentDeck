<p align="center"><img src="assets/logo.svg" alt="AgentDeck" width="420"></p>

# AgentDeck

*Windows multi-terminal panel — every terminal, every agent, one deck.*

Every terminal in one window. Panes are real terminals — not textboxes wired to
a pipe — so colour, line editing, arrow keys, `cls`, `vim` and `less` all work
the way they do in Windows Terminal. A launch animation plays first, then a
setup wizard; each workspace you open afterwards asks which coding agent to run
in its terminals.

![panes](https://img.shields.io/badge/panes-1--16-blue) ![conpty](https://img.shields.io/badge/backend-ConPTY-green)

## Why panes, not windows

Handing a shell a pipe tells it that it isn't talking to a terminal: it drops
colour, stops paging, and its line editor never sees the arrow keys. This app
gives every pane a **pseudo-console** (`CreatePseudoConsole`, Windows 10 1809+)
via `pywinpty`, which is the supported way to put a real terminal behind a
shell on Windows. Output is parsed with `pyte` and drawn by a custom Qt widget.

Reparenting real console windows with `SetParent` does not work, and that is not
a bug to be fixed: console windows belong to `conhost.exe`, a different process,
so they cannot be embedded into a Qt layout. ConPTY is the way around it.

## Requirements

- Windows 10 1809 or newer (ConPTY), or Windows 11
- Python 3.10+
- One of PowerShell 7, Windows PowerShell, Command Prompt, or Git Bash

## Install

**As an app** — download `AgentDeck-win-Setup.exe` from the
[latest release](https://github.com/atik806/AgentDeck/releases/latest) and run
it. It installs per-user (`%LOCALAPPDATA%\AgentDeck\`), no admin prompt. The
first run is unsigned, so Windows SmartScreen shows a warning — click
**More info → Run anyway**.

**From source** —

```cmd
cd E:\Workspace\V4\windows_launcher
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```cmd
python main.py
```

## Updating

An installed build carries an **Update** button in the toolbar: click it to check
GitHub for a newer version, download it, and restart into it. It also checks
automatically ~1.5 s after launch (turn that off with
`"auto_check_updates": false`). Updates are per-user file swaps — no admin. The
button is hidden when you run from source.

## Building a release

See [`packaging/README.md`](../packaging/README.md). In short: bump
`windows_launcher/version.py`, run `packaging/build.py` (PyInstaller onedir +
Velopack `vpk pack`), then `vpk upload github --tag v<version>` — or just push a
`v*` tag and let `.github/workflows/release.yml` do it.

## Accounts (required)

Before the wizard, a **sign-in window** requires **Continue with Google** (via
Supabase) — a signed-in account is mandatory; the only other button quits. It
also brings a toolbar profile chip and cloud sync of a small config slice
(working folder, recent folders, agent, terminal count, layout, font, shell,
theme) across machines. The session is stored DPAPI-encrypted at
`%APPDATA%\multi-terminal\session.bin`; **Sign out** in the ⚙ account dialog
forgets it and puts the sign-in window back up. If the session is lost while the
app is running, you're prompted to sign in again. `--no-login` (build-only,
used by the packaging smoke test) skips the window. Full setup — enabling the
Google provider, the redirect allowlist and the database migration — is in
[`docs/ACCOUNTS.md`](../docs/ACCOUNTS.md).

## Setup wizard

`main.py` opens a three-step wizard first:

1. **Start** — a welcome, plus one-click **recent folders**.
2. **Layout** — pick the **working folder** every terminal starts in, and choose
   **how many terminals** (1 / 2 / 4 / 6 / 8 / 10 / 12). Each tile previews the
   grid it produces — the same `grid_dims()` the real layout uses.
3. **Agents** — pick the coding agent that **auto-runs in every terminal**.
   Every agent AgentDeck knows about is a choice — **Claude Code**, **Codex**,
   **GitHub Copilot CLI**, **Gemini CLI**, **Cursor Agent**, **opencode**,
   **Amp**, **Antigravity CLI**, **Qwen Code**, **Crush**, **Aider**, **Goose** —
   plus **Plain shell** and **Custom command…**. Each shows an *installed* /
   *not installed* pill. Pick one that isn't installed and the card unfolds the
   install command (with **Copy**), an **Open guide ↗** link, and a **Re-check**
   button — install it in another terminal, hit Re-check, and Launch lights up
   without reopening the wizard.

`Launch` opens the panel; your choices are saved and pre-fill the wizard next
time. **Skip — use last setup** (step 1) jumps straight in. `python main.py
--no-wizard` (or `"skip_wizard": true` in the config) bypasses it entirely and
opens from saved settings — for `run.bat` / scripted use.

When the agent is **Claude Code**, its "Is this a project you trust?" prompt is
pre-accepted for the working folder (written to `~/.claude.json`
`projects[<folder>].hasTrustDialogAccepted`), so every pane opens straight into
the session instead of sitting on that question. Set
`"pretrust_agent_folder": false` to keep the prompt.

## Using it

Click a pane to focus it and type. Drag the dividers to resize.

| Action | Shortcut |
|---|---|
| New pane | `Ctrl+Shift+T` (or `Ctrl+Shift+D`) |
| Close active pane | `Ctrl+Shift+W` |
| Reset a wedged screen | `Ctrl+Shift+R` |
| Expand / restore active pane | `Ctrl+Shift+E` |
| Next / previous pane | `Ctrl+Tab` / `Ctrl+Shift+Tab` |
| Focus pane 1–9 | `Alt+1` … `Alt+9` |
| New workspace | `Ctrl+Shift+N` |
| Next / previous workspace | `Ctrl+Shift+PgDn` / `Ctrl+Shift+PgUp` |
| Show / hide the workspace sidebar | `Ctrl+B` |
| Copy / paste | `Ctrl+Shift+C` / `Ctrl+Shift+V` (also `Shift+Insert`) |
| Font size | `Ctrl+=` / `Ctrl+-` / `Ctrl+0` to reset |
| Scroll history | `Shift+PgUp` / `Shift+PgDn`, or the wheel |
| Voice input on / off | `Ctrl+Shift+X` (or `Ctrl+X` while the voice widget is focused) |

`Ctrl+C` sends the interrupt as usual — unless text is selected, in which case it
copies, matching Windows Terminal.

Select with the mouse; double-click selects a word. Pasted text is sent in
bracketed-paste mode when the program asks for it, so a multi-line paste into a
shell isn't run line by line.

Drag files or folders from Explorer onto a pane and their paths are typed at the
prompt — space-separated, and quoted when a path contains a space or a shell
metacharacter, the same as dropping onto Windows Terminal or cmd. The drop
focuses the pane but never presses Enter, so you can check the command first.
Dropping plain text from another app pastes it.

The toolbar picks the shell for **new** panes, switches between Grid / Columns /
Rows, and steps the font. A pane whose shell exits turns red and grows a
**Restart** button; the pane keeps its place in the layout.

## Voice input

A small **voice widget** floats over the terminal area (the `🎤` toolbar button
shows/hides it; drag it anywhere inside the panes). `Ctrl+Shift+X` starts and
stops listening — the equaliser reacts to your voice while it does. Each finished
sentence is **typed at the active pane's prompt with no Enter**, exactly like a
file drop, so you read it before running it. It also flashes in the widget as it
lands.

Transcription is local (whisper.cpp via `pywhispercpp`, WebRTC VAD, the
`voice_capture` sibling project's pipeline). The first `Ctrl+Shift+X` downloads
the `tiny.en` model (~75 MB) into `~/.cache/pywhispercpp/`; after that it's
offline. Plain `Ctrl+X` is left for the shell (nano, readline) and only toggles
when the widget itself has focus.

The audio dependencies (`sounddevice`, `webrtcvad-wheels`, `pywhispercpp`,
`numpy`) are optional: without them the mic button is just disabled and the panel
is unaffected. Settings: `voice_model`, `voice_mic_device`,
`voice_overlay_visible`, `voice_overlay_x/y` in `config.json`.

Each pane header carries an **expand** button (`⤢`) next to its close button.
Expanding gives one pane the whole window; the button turns into `⤡` and puts the
layout back. The other panes are only hidden — their shells keep running and
their output is all there when you come back. Switching panes while one is
expanded (`Ctrl+Tab`, `Alt+2`, …) moves the expansion to the pane you switch to,
rather than focusing something you cannot see.

## Workspaces

The **WORKSPACES** sidebar on the left holds independent groups of panes. Click a
row to switch; `＋` (or `Ctrl+Shift+N`) opens the new-workspace dialog — name it
(defaults to `Workspace N`), pick the agent, pick the pane count. The number on
each row is that workspace's pane count.

Rename any time: hover a row (or the active one) and click the **✎** button, or
double-click the name. Layout, shell and font settings are shared across every
workspace. The **✕** next to ✎ closes it — with live shells it asks first, and
the last workspace can't be closed.

Only the active workspace is on screen — the rest are hidden, but their shells
keep running and their scrollback is intact when you switch back. `Ctrl+B` hides
the sidebar; `Ctrl+Shift+PgDn` / `Ctrl+Shift+PgUp` step between workspaces.

## Configuration

`%APPDATA%\multi-terminal\config.json`

```json
{
  "default_count": 4,
  "default_shell": "auto",
  "layout": "grid",
  "font_size": 11,
  "scrollback": 5000,
  "window_width": 1400,
  "window_height": 880,
  "start_maximized": false,
  "working_folder": "",
  "recent_folders": [],
  "agent": "none",
  "agent_command": "",
  "skip_wizard": false,
  "voice_model": "tiny.en",
  "voice_mic_device": null,
  "voice_overlay_visible": true,
  "voice_overlay_x": -1,
  "voice_overlay_y": -1
}
```

| Key | Meaning |
|---|---|
| `default_count` | Panes opened at startup, 1–16 |
| `default_shell` | `auto`, `pwsh`, `powershell`, `cmd`, `bash` — `auto` picks the best installed |
| `layout` | `grid`, `columns`, `rows` |
| `font_size` | 6–48 |
| `scrollback` | Lines kept per pane; `0` disables |
| `working_folder` | Folder the terminals start in; `""` = home. The wizard's step 2. |
| `recent_folders` | The wizard's quick-launch list, newest first |
| `agent` / `agent_command` | `agents.py` key auto-run in every pane (`none`, `claude`, …, `custom`) and the command for `custom` |
| `skip_wizard` | `true` opens straight from config, no wizard (also `--no-wizard`) |
| `pretrust_agent_folder` | `true` (default) pre-accepts Claude Code's folder-trust prompt for the working folder |
| `voice_model` | whisper.cpp model for voice input (`tiny.en`, `base.en`, …) |
| `voice_mic_device` | `null` = default mic, or a device id / name substring |
| `voice_overlay_visible` | Show the voice widget on startup (it still starts idle) |
| `voice_overlay_x` / `voice_overlay_y` | Saved spot inside the terminal area; `-1` = auto (bottom-right) |

Out-of-range numbers are clamped and bad values fall back to defaults, with a
warning on stderr — a hand-edited config can't stop the app from opening.

Changing the layout, shell or font size from the toolbar/shortcuts writes the new
value straight back to `config.json`, so the next startup keeps it. (Workspaces
themselves are still session-only.)

## Architecture

Layered bottom-up:

| Module | Responsibility |
|---|---|
| `pty_backend.py` | ConPTY sessions. One blocking reader thread per shell, emitting decoded text on a Qt signal. Shell discovery. |
| `vt_screen.py` | `pyte.Screen` subclass adding scrollback (captured as lines scroll off) and the alternate screen buffer, plus pyte-token → `QColor` mapping (Campbell palette). |
| `terminal_view.py` | The terminal widget: paints the screen, translates key events to VT sequences, selection/clipboard, scrollbar, file drop. Batches pty output on a 16 ms timer so a flood of output can't stall the UI. |
| `workspace.py` | `TerminalPane` (a terminal plus its header) and `Workspace` — one group of panes with the nested `QSplitter` tree that arranges them, plus active/expand bookkeeping. |
| `workspace_sidebar.py` | The **WORKSPACES** list widget: switch, add, close, rename. Pure view — emits signals, touches no shell. Also owns the top nav strip (**Plugins**, **Notes**). |
| `notes_panel.py` / `notes_store.py` | The **Notes** view (sidebar nav): a local notebook — note list ∥ title/body editor with debounced autosave. `NotesStore` is a Qt-free JSON store at `%APPDATA%\multi-terminal\notes.json` (machine-local, not cloud-synced). |
| `terminal_panel.py` | The window. Holds the sidebar and a `QStackedWidget` of workspaces, the toolbar and the shortcuts; routes both to the active workspace. Owns the voice overlay + engine. Takes the wizard's `startup=` and threads the working folder + agent into the first workspace. |
| `setup_wizard.py` | The 3-step `QDialog` shown before the panel (amber accent). Returns `{folder, count, agent_key, agent_command}`. |
| `agents.py` | Agent discovery — `available_agents()` (installed) / `known_agents()` (all) / `resolve_agent()`, same shape as `pty_backend`'s shell discovery. `install_hint(key)` → install command + docs URL for the wizard's guide. `pretrust_folder()`: for a Claude Code command, pre-accepts the folder-trust prompt in `~/.claude.json`. Qt-free. |
| `voice_engine.py` | Qt bridge over the `voice_capture` sibling project's capture / VAD / transcription pipeline. Worker threads behind `state` / `level` / `transcription` / `error` signals. All deps optional — a failed import just sets `available = False`. |
| `voice_overlay.py` | The floating, draggable voice widget: mic button, level-reactive equaliser (custom `paintEvent` + one 33 ms timer), fading transcript preview. Parented to the window, kept over the panes by `set_bounds()`. |

`config.py` handles settings. `launcher.py` and `main_window.py` are a separate,
older feature that tiles *external* terminal windows with the Win32 API; they are
independent of the panel.

### Notes for anyone changing this

- **Relayout reparents panes, it does not rebuild them.** `_relayout()` detaches
  every pane before deleting the old splitter tree, which is what lets a running
  shell survive a layout change. `_build_tree()` always returns a splitter, never
  a bare pane, so the root can be deleted safely.
- **A rebuilt tree has to be shown explicitly.** Adding a freshly built widget to
  a layout does not show it — a new child widget starts hidden and Qt only
  cascades visibility downwards from an explicit `show()`. The *first* relayout
  happens before the window is shown, so `panel.show()` covers it; without the
  `self._root.show()` in `_relayout`, every later one (new pane, closed pane,
  layout change, expand) leaves a blank panel with the shells running invisibly.
- **Pane visibility is set after the tree is parented, never before.**
  `setVisible(True)` on a pane that `_relayout` has just detached would promote it
  to a stray top-level window for the instant before it is reparented.
- **Resize has to reach the pty — but only a real size.** Changing the widget size
  without calling `setwinsize` leaves the shell formatting for the old width;
  prompts wrap in the wrong place. `TerminalView._apply_geometry` resizes screen
  *and* pty. It is driven off a 40 ms coalescing timer because a splitter rebuild
  walks through sizes no pane ever actually has: detaching a pane hands its space
  to its neighbour for an instant, and a pane mid-reparent is 0 pixels wide, which
  `visible_cols()` floors to a single column. Applying those is destructive —
  pyte truncates every line to the new width, so one transient narrow size
  permanently shortens the pane's contents, and a full-screen program repaints at
  a width it never had. `_apply_geometry` re-reads the canvas when the timer
  fires, and skips it entirely while it is hidden: a pane behind an expanded one
  has no meaningful geometry.
- **Shrinking the screen clips from the top.** pyte does that itself;
  `TerminalScreen.resize` only captures the doomed lines as scrollback before
  delegating. Pre-scrolling them yourself drops them twice.
- **pyte has no alternate screen buffer; `TerminalScreen` adds it.** Full-screen
  programs (`vim`, `less`, `htop`, Claude Code's fullscreen renderer) send DECSET
  `1049` on entry and reset it on exit to get a scratch screen that leaves the
  shell's screen and scrollback untouched. pyte ignores the mode entirely, so
  without the override every frame such a program paints lands in the primary
  buffer: the shell's screen is destroyed on exit, and every repaint that reaches
  the bottom row scrolls another copy of the program's borders and rules into
  scrollback — a pane that fills with stacked separator lines. `set_mode` /
  `reset_mode` swap `(buffer, cursor, margins)`, `_history_enabled` keeps the
  alternate screen out of the scrollback, and `history_length` reports `0` while
  it is up so the scrollbar disables itself the way a real terminal's does.
  Note pyte passes *raw* private-mode numbers to `set_mode` (it only shifts by 5
  for the codes it handles itself), so match on `1049`, not `1044`.
- **A full-screen program that dies without sending `1049l` leaves the pane
  wedged.** A crash, a `kill`, a dropped SSH session — the alternate screen stays
  up forever, `history_length` stays pinned at `0`, and scrollback plus the
  scrollbar and wheel are dead for the rest of the session. The program is always
  a child of the shell, so `TerminalView` arms a 1.5 s watchdog while the
  alternate screen is up (`_check_alt_screen`) and, once `PtySession.has_child_process()`
  reports the shell is alone at its prompt again, calls
  `TerminalScreen.exit_alternate_screen()` to drop back to the primary buffer —
  the same thing typing `reset` would do. `Ctrl+Shift+R` forces it by hand. The
  child check is a dependency-free Toolhelp snapshot walk; a recycled parent PID
  can only ever delay the recovery, never trigger a false one that yanks a live
  full-screen app.
- **On the alternate screen the wheel is forwarded, not swallowed.** While a
  full-screen program is up there is no scrollback to move through, so a wheel
  event that just called `set_scroll_top` would do nothing — the "scrolling is
  dead inside `less` / `vim` / Claude Code" report. `TerminalCanvas.wheelEvent`
  matches what xterm and Windows Terminal do: if the program set a mouse
  tracking mode (`1000`/`1002`/`1003`, with `1006` for SGR coordinates) the
  notch is sent as a wheel button report (`64`/`65`); otherwise, on the
  alternate screen, it is translated to cursor-key presses (`ESC [ A`/`B`, or
  `ESC O A`/`B` when DECCKM is set) so the program scrolls its own view. On the
  primary screen it still scrolls our scrollback. `Shift+wheel` opts out of
  mouse reports (so drag-select still works) but keeps the alternate-scroll
  arrows, since those *are* the local wheel behaviour there. Mode state is read
  straight off `pyte`'s `screen.mode` set — pyte records every private mode it's
  handed, shifted `<< 5`, the same trick `_MODE_BRACKETED_PASTE` already used.
- **Row pitch is `max(height, lineSpacing)`.** Cascadia Mono's leading is
  negative at some sizes and positive at others, so either metric used alone
  clips descenders at one font size or gaps the box-drawing glyphs at another.
- **`TerminalStream`, not `pyte.Stream`.** Two corrections to pyte's parser, both
  load-bearing:
  - pyte's CSI dispatch table has no entry for `b` (REP, "repeat preceding
    character"), which programs use to compress the long runs that make up
    horizontal rules and box borders. `TerminalStream` extends a *copy* of the
    table so pyte's class attribute is left alone.
  - **A CSI sequence with a `<`, `=` or `>` private prefix must not reach pyte.**
    pyte skips a `>` prefix byte without recording that it saw one
    (`streams.py`: `elif char in SP_OR_GT: pass`) and then dispatches on the final
    byte as an ordinary CSI. `CSI > 4 ; 2 m` is XTMODKEYS, which modern TUIs
    (Claude Code among them) send at startup to ask for disambiguated key
    encoding — pyte runs it as `SGR 4` and turns **underline on for the rest of
    the session**. Nothing ever turns it off, because the program never asked for
    underline in the first place. The renderer then underlines blank cells too —
    correctly; underlined whitespace is still underlined — so every row of the
    pane becomes a full-width horizontal rule and the text looks double-spaced.
    `<` is worse: it is not in pyte's skip set at all, so the sequence aborts at
    the prefix and its real final byte is drawn as text (`CSI < u`, kitty keyboard
    pop, paints a stray `u`). `TerminalStream.feed` strips these before pyte sees
    them, holding back a partial one at the end of a chunk — pty reads are
    arbitrary chunks, and half of `CSI > 4 m` fed to pyte is the same bug. A `?`
    prefix is deliberately *not* stripped: pyte handles those, and DECSET `1049`
    is one of them.
- **Repaint groups runs, not cells.** `_build_runs()` batches adjacent cells that
  share a style into one `drawText`. Per-cell drawing is roughly 80× slower.
- **The voice overlay is parented to the window, not the terminal stack.** A
  plain child of a `QStackedWidget` loses the stacking fight with the current
  page (it renders behind the panes until the next `setCurrentWidget`).
  `VoiceOverlay` is a child of the `QMainWindow` and is kept over the terminal
  area by `set_bounds()` — the panel feeds it the stack rect mapped into window
  coordinates on every `_position_overlay()` (startup, resize, workspace switch,
  show). Its saved position in `config.json` is an **offset inside that rect**,
  so it survives a toolbar-height or window change.
- **Dragging a child widget: never mix coordinate spaces.** The overlay's drag
  stores `event.position()` (local to the widget) on press, and on move computes
  `parent.mapFromGlobal(cursor) - grabPoint`. The earlier bug stored
  `globalPos - frameGeometry().topLeft()` — a child's `frameGeometry()` is in
  *parent* coordinates, so subtracting it from a *screen* coordinate gave a
  garbage offset and the widget teleported (only visible when the window is not
  at screen origin — `test_voice_overlay.py` §6 fakes that with a shifted parent).
- **Qt graphics effects don't nest.** A `QGraphicsDropShadowEffect` on the
  overlay silently suppressed the caption `QLabel`'s own `QGraphicsOpacityEffect`
  (used for the transcript fade). The card gets its depth from a painted 1px top
  highlight + the border instead — no widget-level effect.
- **The voice pipeline never touches the GUI thread.** `voice_engine.py` runs
  capture / VAD / whisper.cpp on worker threads and marshals every callback
  through a `QObject` bridge — same pattern as `voice_capture/app.py`. Model load
  (a download on first run) happens on a start thread, so the mic only opens once
  it's ready; `Ctrl+Shift+X` during the load cancels cleanly. All the audio deps
  are optional: a failed import sets `VoiceEngine.available = False` and the
  panel is otherwise untouched.
- **The agent is typed at the shell, not passed as argv.** `TerminalView` holds
  a `startup_command` and sends it (plus `\r`) once, 300 ms after the *first*
  output from the shell — i.e. once the banner/prompt proves it's up and
  PSReadLine / bash's line editor has initialised. Shell-agnostic, and `Restart`
  re-runs it, so a dead agent pane relaunches the agent in the same folder. Only
  the wizard's initial batch of panes gets it; `Ctrl+Shift+T` panes are plain
  shells (in the same folder). The layout grid math lives once in
  `workspace.grid_dims()` so the wizard's tile preview matches what opens.
- **`main.py` pre-trusts the folder for Claude Code *before* building the
  panel.** `agents.pretrust_folder()` does a read-modify-write of
  `~/.claude.json` — best effort, never raises, writes both `\`- and
  `/`-separated forms of the path since Claude stores either depending on how it
  was launched. Doing it before the panel (and its shells) start means the entry
  is in place well before the 300 ms-delayed `claude` launch reads it.

Ten test suites, all plain scripts:

```cmd
python test_vt_screen.py
python test_wheel.py
python test_panel.py
python test_voice_engine.py
python test_voice_overlay.py
python test_voice_models.py
python test_voice_postprocess.py
python test_voice_download.py
python test_agents.py
python test_setup_wizard.py
```

`test_vt_screen.py` covers the screen model — alternate screen buffer, REP,
scrollback, and the `<`/`=`/`>` prefix handling. Run it after touching
`vt_screen.py`. `test_wheel.py` covers wheel routing — local scrollback vs.
alternate-scroll cursor keys vs. mouse-button reports, in both coordinate
encodings. Run it after touching `wheelEvent` or the mouse-mode handling in
`terminal_view.py`. `test_panel.py` drives a real window with real shells and
covers the layout: relayout visibility, expand/restore, that every resize path
reaches the pty, and that a file drop types its quoted path without an Enter.
Run it after touching `terminal_panel.py` or the geometry code in
`terminal_view.py` — none of those bugs are visible to a screen-model test,
because each is about *when* Qt shows and sizes a widget. `test_voice_engine.py`
(stubs the mic + model — offline, no network) covers the engine state machine and
its thread→signal plumbing (including that the model/VAD/segmentation tuning
config reaches the pipeline constructors); `test_voice_overlay.py` covers the
widget alone (state → visuals, drag clamping, the preview fade, Ctrl+X).
`test_voice_models.py` covers model recommendation + name resolution;
`test_voice_postprocess.py` covers the finished-utterance clean-up (capitalise,
drop whisper's trailing period). The panel suite's sections 25–28 cover the
wiring between them, without ever starting audio.
`test_agents.py` covers agent discovery; `test_setup_wizard.py` covers the
wizard's pages, validation and the choices it returns (offscreen — stub
`QFileDialog`); panel-suite section 29 checks `startup=` reaches every pane's
shell (CWD + auto-run command, and `Restart` re-running it). Don't run the panel
suite with another GUI process alongside — a stray window steals focus and the
"drop moved focus to the pane" check fails.

## Troubleshooting

**`ImportError: pywinpty is required`** — dependencies aren't installed:
`pip install -r requirements.txt`.

**A pane says "failed to start"** — the chosen shell isn't installed. Pick
another in the toolbar; `auto` only ever offers shells it actually found.

**No colour in Git Bash** — panes set `TERM=xterm-256color` already; if a
specific program is still monochrome it is probably checking `isatty` on a
redirected stream, not the terminal.

**ConPTY unavailable** — needs Windows 10 1809 or newer. Nothing to work around
on older builds.

**The mic button is greyed out** — the optional audio dependencies aren't
installed: `pip install -r requirements.txt` (pulls `sounddevice`,
`webrtcvad-wheels`, `pywhispercpp`, `numpy`). Hover the button for the exact
import error.

**Voice input says "model load failed"** — the first use downloads `tiny.en`
(~75 MB) from Hugging Face into `~/.cache/pywhispercpp/`; it needs network that
once. Set `voice_model` to something already in that cache to skip it.
