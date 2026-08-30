# V4 — working context

Quick-start notes for anyone (or any AI session) picking this repo up. Read this
first; it says which parts are live and which are noise.

## What this repo actually is

`E:\Workspace\V4` holds **more than one project**. Only one is under active work:

| Path | Status | What it is |
|---|---|---|
| `windows_launcher/` | **ACTIVE** | **AgentDeck** — Windows multi-terminal panel, every shell in one window as a real ConPTY pane, each workspace running a coding agent of its choice. This is what "the project" means. (User-visible name is "AgentDeck"; the config dir, `pip` package internals and module names still say `multi-terminal`.) |
| `voice_capture/` | **library for the panel** | Standalone PySide6 voice-to-text app with its own `.venv`, but its Qt-free `voice_capture/{audio,vad,transcription}` modules are now imported by `windows_launcher/voice_engine.py` for the panel's voice overlay. Changing those three modules affects both. |
| `assets/` | branding source | `icon.svg` / `logo.svg` + `build_icons.py` rasteriser for the panel and voice_capture marks. |
| `.opencode/`, `.claude/` | tooling scaffolding | Ignore. |

The older cross-platform GTK/Tk launcher (`ui/`, `platforms/`, root `launcher.py`,
`cli.py`, `main.py`, `gridmath.py`, `config.py`, `models.py`, `multi-terminal.desktop`)
and the stale `ARCHITECTURE.md` were removed in the 2026-08-29 cleanup — see git history.

`E:\Workspace\V4` is a git repo as of 2026-08-29 (the cleanup snapshot is the first commit).

Environment: Windows 11, PySide6. Shell for tools here is Git Bash (POSIX) but the
app is Windows-only (ConPTY via `pywinpty`).

## windows_launcher — architecture

The real docs live in `windows_launcher/README.md` ("Architecture" + "Notes for
anyone changing this" — the notes are load-bearing, every bullet is a bug that
was fixed). Summary:

Layered bottom-up:

- `pty_backend.py` — ConPTY sessions, one reader thread per shell, shell discovery.
- `vt_screen.py` — `pyte.Screen` subclass: scrollback, alternate-screen buffer,
  token→QColor. `TerminalStream` fixes two pyte parser bugs (REP `b`, and
  `<`/`=`/`>` CSI private prefixes). Covered by `test_vt_screen.py`.
- `terminal_view.py` — the terminal widget. `TerminalCanvas` paints the grid +
  handles keyboard/mouse/selection/clipboard **and file drop**; `TerminalView`
  wraps it with a scrollbar and owns the pty. Output batched on a 16 ms timer;
  resizes coalesced on a 40 ms timer (a splitter rebuild walks through sizes no
  pane ever really has — applying them truncates pyte lines).
- `workspace.py` — `TerminalPane` (terminal + header strip) and
  `Workspace(QWidget)` (one group of panes, the nested `QSplitter` tree,
  active/expand bookkeeping). Signals: `changed`, `active_pane_changed`,
  `empty`, `notice`.
- `workspace_sidebar.py` — `WorkspaceSidebar` + `_WorkspaceRow`, the WORKSPACES
  list. Pure view: emits `selected` / `created` / `closed` / `renamed`, never
  touches a shell.
- `terminal_panel.py` — the window (`TerminalPanel(QMainWindow)`): sidebar ∥
  `QStackedWidget` of workspaces, toolbar, shortcuts. Routes toolbar + shortcuts
  to the **active** workspace. Keeps `_panes` / `_relayout` / `_zoomed` / … as
  thin proxies onto the active workspace (older callers + tests use them).
- `config.py` — settings at `%APPDATA%\multi-terminal\config.json`.
- `launcher.py`, `main_window.py` — the *older* external-window feature, bolted
  in the same folder, independent of the panel. Leave alone.

Entry point: `windows_launcher/main.py` (also `run.bat` → `pythonw.exe`, no
console — hence the crash-to-MessageBox handler in `main.py`).

## Key design invariants (don't regress these)

- **Relayout reparents panes, never rebuilds them** — that's what keeps a running
  shell alive across a layout/zoom/workspace change. `_build_tree()` always
  returns a splitter, never a bare pane.
- **A rebuilt tree must be `show()`n explicitly**, and pane visibility is set
  *after* reparenting (a parentless visible widget flashes as a top-level window).
- **Geometry only reaches the pty for a real, visible size.** Hidden panes
  (behind an expanded pane, or in a non-current workspace) do not resize their
  pyte screen until shown again — this is correct, not a bug.
- **Settings are global** across workspaces (layout mode, shell, font size) and
  are persisted to `config.json` on change (`TerminalPanel._save_settings`), so a
  restart keeps them. Session-only: workspaces are NOT persisted; restart = one
  default workspace.

## Features added in recent sessions

1. **File drag-and-drop onto a pane** (`terminal_view.py`,
   `TerminalCanvas.dragEnter/dragMove/dropEvent` + `_drop_text` / `_quote_path` /
   `_as_paste`). Drop files/folders → their paths typed at the prompt,
   space-separated, double-quoted when a path has whitespace or a shell
   metachar (`()&^;,!` `` ` `` `'`). Native `\` separators. Focuses the pane,
   snaps to prompt, **never appends Enter**. Plain-text drop = paste.
   Matches Windows Terminal / cmd behavior.

3. **Stuck alternate-screen recovery** — a full-screen program (`vim`, `less`,
   a TUI) that crashes or is killed never sends the `1049l` that restores the
   primary buffer, so the pane was left with no scrollback and a dead scrollbar
   for the rest of the session ("scrolling stops working after a while").
   `TerminalView` now watches for it: while the alt screen is up a 1.5 s timer
   (`_check_alt_screen`) polls `PtySession.has_child_process()` (a ctypes
   Toolhelp walk, no new dep) and, once the shell is alone at its prompt again,
   calls `TerminalScreen.exit_alternate_screen()`. `Ctrl+Shift+R` forces it.
   Tests: `test_vt_screen.py` section 8b.

4. **Wheel routing on the alternate screen** (2026-08-28) — even with the
   program alive and correctly on the alt screen, the wheel did nothing
   ("scrolling not working" while Claude Code / `less` / `vim` runs), because
   there is no local scrollback to move. `TerminalCanvas.wheelEvent` now does
   what xterm/Windows Terminal do: forward the notch as a mouse-button report
   (`64`/`65`, SGR when `1006` is set) if the program set a mouse-tracking mode
   (`1000`/`1002`/`1003`); else on the alt screen translate to cursor keys
   (`ESC [ A`/`B`, or `ESC O A`/`B` under DECCKM). Primary screen still scrolls
   our scrollback. `Shift+wheel` opts out of mouse reports but keeps the
   arrows. Mode state comes straight from `pyte`'s `screen.mode` set (private
   modes shifted `<< 5`). Tests: `test_wheel.py` (new). NB: mouse *clicks* are
   still not forwarded — Claude Code's clickable UI won't respond yet.

2. **Workspaces** — the WORKSPACES sidebar (see `workspace_sidebar.py` +
   `workspace.py` + the workspace half of `terminal_panel.py`). One window,
   sidebar swaps the terminal area, badge = pane count. `＋`/`Ctrl+Shift+N` new,
   double-click to rename, `✕` to close (confirms on live shells; `force=True`
   param skips the modal — used by tests; last workspace can't close).
   `Ctrl+B` toggle sidebar, `Ctrl+Shift+PgUp/PgDn` switch.

5. **Voice-to-text overlay** (2026-08-28) — `voice_overlay.py` (draggable
   floating widget: mic button + level-reactive equaliser + fading transcript
   preview) + `voice_engine.py` (Qt bridge that reuses `voice_capture`'s
   capture/VAD/whisper.cpp pipeline on worker threads). Visible-but-idle on
   startup; `Ctrl+Shift+X` toggles listening (`Ctrl+X` too, but only while the
   widget has focus, so the shell keeps its own `Ctrl+X`). Each utterance is
   `insert_text()`'d at the active pane's prompt — **no Enter**, same as a file
   drop. `tiny.en` model auto-downloads on first use. Overlay is parented to the
   **window** (not the stack — loses the z-fight otherwise) and kept over the
   panes via `VoiceOverlay.set_bounds()`. Audio deps optional: missing → mic
   disabled, panel fine. One additive hook in `voice_capture/audio/capture.py`
   (`on_level` callback). Tests: `test_voice_engine.py`, `test_voice_overlay.py`
   (both offline), panel suite §25–28. Needs `sounddevice webrtcvad-wheels
   pywhispercpp numpy` in `windows_launcher/.venv` (added to requirements.txt).

6. **Setup wizard** (2026-08-28) — `main.py` opens a 3-step `QDialog`
   (`setup_wizard.py`, amber accent) before the panel: **Start** (welcome +
   recent folders) / **Layout** (working folder + terminal-count tiles) /
   **Agents** (auto-run a coding agent in every terminal). `agents.py` knows
   **12** agents (claude, codex, copilot, gemini, cursor-agent, opencode, amp,
   antigravity=`agy`, qwen, crush, aider, goose) + "Plain shell" + "Custom".
   **Every one is a selectable card** (`all_agents()` → `(k,label,cmd,installed)`);
   a not-installed card unfolds `agents_ui.InstallHint` (command + Copy + Open
   guide + **Re-check**) and blocks Launch until found. Re-check runs
   `agents.refresh_path()` (re-reads user+machine PATH from the registry) so an
   agent installed in another terminal is picked up without a restart. Same
   picker logic in `new_workspace_dialog` (combo + hint panel). Choices
   persist to config and pre-fill next launch; `--no-wizard` / `skip_wizard`
   bypasses. `TerminalPanel(config, startup=…)` threads the folder (`cwd`) +
   agent (`startup_command`) down through `Workspace`/`TerminalPane` to
   `TerminalView`, which types the command once ~300 ms after the shell's first
   output. `Restart` re-runs it; later `Ctrl+Shift+T` panes are plain shells in
   the same folder. Grid math shared via `workspace.grid_dims()`.
   `main.py` calls `agents.pretrust_folder()` before building the panel — for a
   Claude Code agent it writes `projects[<folder>].hasTrustDialogAccepted=true`
   in `~/.claude.json` so every pane opens straight into Claude instead of its
   "trust this folder?" prompt (`pretrust_agent_folder` config toggle, **default
   off** — it suppresses a security prompt; and even when on, `pretrust_folder()`
   refuses any folder carrying its own `.claude/` or `.mcp.json` config). Tests:
   `test_agents.py`, `test_setup_wizard.py` (offscreen-OK), panel suite §29.

7. **Toolbar restyle (2026-08-29)** — `terminal_panel._TOOLBAR_QSS`: consistent
   button sizing, subtle checked tint (was solid blue), styled combo arrows,
   visible separators, bottom border. Voice toggle uses `voice_overlay.mic_icon()`
   (a drawn QIcon — the `🎤` emoji rendered as a broken glyph). `main.py` dropped
   `setApplicationDisplayName` (Qt was appending it → doubled window title).

8. **AgentDeck rename + launch splash + per-workspace agent (2026-08-29)** —
   the product is now **AgentDeck** in every user-visible place (window title,
   wizard title/heading, close dialogs, `setApplicationName`, AppUserModelID
   `AgentDeck.Panel`, README). The config dir (`%APPDATA%\multi-terminal`),
   module names and `agents.py` internals are unchanged, so existing configs
   still load.
   - **Launch splash** — `agentdeck_splash.py`: a frameless translucent
     `QWidget`, custom-painted from one eased `_p` 0→1 (`QVariantAnimation`,
     ~1.5 s): icon fades/scales in, "AgentDeck" wordmark slides up, blue→green
     accent line sweeps, tagline fades in, then a `windowOpacity` fade-out.
     `show_splash(icon, enabled=…)` runs it on a nested `QEventLoop` with a hard
     4 s cap; click/keypress skips to the fade. `main.py` calls it right after
     the `QApplication` is built, before the wizard. Off via `--no-splash` or
     `show_splash: false` (new config key, default true). Tests:
     `test_agentdeck_splash.py` (offline).
   - **Per-workspace agent** — `new_workspace_dialog.py` (`NewWorkspaceDialog`,
     blue accent, agent dropdown + custom field + terminals spinbox + live
     preview). `TerminalPanel._new_workspace_interactive()` shows it,
     `pretrust_folder()`s the pick, then calls the unchanged
     `_add_workspace(pane_count=…, startup_command=…)`. The 3 UI entry points
     (toolbar ＋ Workspace, sidebar +, `Ctrl+Shift+N`) route through it; bare
     `_add_workspace()` stays dialog-free for startup + `test_panel.py`. Last
     pick is remembered for the session (`_last_ws_agent[_custom]`, seeded from
     the wizard / config) and seeds the dialog default. So a later workspace is
     no longer "scratch space" — it runs whatever agent you choose. Tests:
     `test_new_workspace_dialog.py` (offline).
   - **Logo** — see "Branding / icons" below. New deck mark + wordmark; the
     toolbar and wizard start page now show it, not just the title bar.
     Rebuild after editing: `assets\build_icons.py windows_launcher\assets\icon.svg
     windows_launcher\assets assets`.

9. **Sidebar nav strip + Plugins panel (2026-08-29)** — `workspace_sidebar.py`
   now has a small nav strip above the WORKSPACES list (`#wsNav`), currently one
   item: a checkable **Plugins** button (`plugin_icon()` drawn puzzle-piece —
   emoji renders broken here). It emits `plugins_selected`. `plugins_panel.py`
   (`PluginsPanel`) is a styled "coming soon" empty state. `terminal_panel.py`
   wraps the workspace `QStackedWidget` in an outer `_main_stack` (`_ws_stack`
   at 0, `_plugins_panel` at 1) — `_ws_stack.count()` stays == workspace count,
   which older callers/tests rely on. `_show_plugins()` / `_leave_plugins()`
   flip `_main_stack` and hide/restore the voice overlay; `_plugins_active`
   gates `_refresh_sidebar` (passes `active=None` so no row highlights, keeps
   the list populated). Clicking any workspace / creating one leaves the panel.
   Tests: `test_plugins_panel.py` (offline), panel suite §24b.

10. **Light / dark theme (2026-08-30)** — `theme.py` owns every colour token
    for both modes plus a `_Manager` QObject with a `changed(str)` signal.
    `theme.init(config)` resolves `config["theme"]` (`system|light|dark`) once
    at startup (`main.py`, before any window); the toolbar's **sun/moon button**
    calls `theme.toggle()`. `terminal_panel._on_theme_changed` fans it out:
    re-runs the toolbar QSS (`_toolbar_qss()` is now a method, not the old
    `_TOOLBAR_QSS` constant), window/statusbar/brand, `sidebar.apply_theme()`,
    `plugins_panel.apply_theme()`, and `workspace.apply_theme()` → each
    `TerminalPane` → `TerminalView.apply_theme()` → `TerminalCanvas` rebuilds
    its `Palette` (which now pulls terminal bg/fg + the 16 ANSI slots from
    `theme`; light = a GitHub-light set) and repaints. Navbar custom-paint
    colours and the account/new-workspace dialogs read `theme.color(...)` live.
    The new **gear button** opens `settings_dialog.SettingsDialog` (theme,
    splash/wizard, updates, Claude trust — writes straight into `config`).
    Persisted via `config["theme"]` + `_save_settings()`. **Not yet themed:**
    the voice overlay, the setup wizard, and the launch splash stay dark.
    Tests: `test_theme.py` (offline).

## Running / testing

```cmd
cd E:\Workspace\V4\windows_launcher
.venv\Scripts\python.exe main.py            # run the app
.venv\Scripts\python.exe test_panel.py      # real window + real shells; ALL PASS
.venv\Scripts\python.exe test_vt_screen.py  # screen model; ALL PASS
.venv\Scripts\python.exe test_voice_engine.py   # voice pipeline, stubbed; offline
.venv\Scripts\python.exe test_voice_overlay.py  # voice widget; offline
.venv\Scripts\python.exe test_agents.py         # agent discovery; offline
.venv\Scripts\python.exe test_setup_wizard.py   # wizard pages/validation; offline
.venv\Scripts\python.exe test_new_workspace_dialog.py  # new-workspace agent dialog; offline
.venv\Scripts\python.exe test_agentdeck_splash.py      # launch splash; offline
.venv\Scripts\python.exe test_plugins_panel.py         # sidebar nav + plugins panel; offline
.venv\Scripts\python.exe test_theme.py                 # light/dark theme + toggle; offline
```

- `test_panel.py` is a **scripted integration test** (no pytest): steps are
  **chained** — each schedules the next only after it returns. Do not go back to
  front-loading `QTimer.singleShot` at fixed offsets; that silently reorders
  steps when a step's body outruns its slice.
- Both suites end in `os._exit` (pty reader threads never unblock). A run can take
  60–90 s. Run it in the background and read the output file.
- A GUI test that hits a modal `QMessageBox` will **hang forever** headless —
  the event loop is blocked so the step timer never fires. Give the code a
  `force`/no-confirm path for tests.
- No console? Use the `_probe*.py` pattern (run under `pythonw.exe`, write
  findings + a `panel.grab().save(...)` screenshot to files). `_probe.py` exists;
  probes are scratch, delete after use.

## Branding / icons

Each app has its own mark, same visual family (dark rounded tile, Catppuccin
Mocha palette, blue→sky primary shape + green accents):

- `windows_launcher/assets/` — **AgentDeck mark (2026-08-29)**: a diagonally
  stacked *deck* of three terminal panes, the front one carrying the blue→cyan
  prompt chevron + green cursor. `logo.svg` is that mark + an "Agent" (light) /
  "Deck" (blue) split wordmark. Wired in `main.py` (`_load_icon` /
  `app.setWindowIcon` / `panel.setWindowIcon`, plus AppUserModelID
  `AgentDeck.Panel`), shown in-window by `terminal_panel._build_toolbar`
  (mark + wordmark at the far left) and on the setup-wizard start page, and
  painted into the launch splash. `create-desktop-shortcut.bat` points the
  `.lnk` (now `AgentDeck.lnk`) at `assets/icon.ico`.
- `voice_capture/assets/` — microphone + level bars. Wired in
  `voice_capture/app.py` the same way. Untouched by the AgentDeck rename.
- `assets/` (repo root) — source `icon.svg` / `logo.svg` (kept byte-identical to
  `windows_launcher/assets/` copies) plus `build_icons.py`, the shared rasteriser.

Regenerate the PNG/ICO after editing an `icon.svg`:

```cmd
windows_launcher\.venv\Scripts\python.exe assets\build_icons.py ^
    windows_launcher\assets\icon.svg windows_launcher\assets assets
windows_launcher\.venv\Scripts\python.exe assets\build_icons.py ^
    voice_capture\assets\icon.svg voice_capture\assets
```

The `.ico` is hand-assembled with 16/24/32/48/64/128/256 PNG frames (Qt's writer
only emits one frame).

## Packaging / releases (2026-08-29)

The repo is now a **public GitHub monorepo** (`github.com/atik806/AgentDeck`,
branch `main`). Distribution = **PyInstaller onedir** frozen app + **Velopack**
installer/updater, published to **GitHub Releases**.

- `windows_launcher/version.py` — `__version__` (single source; `main.py`,
  `setup_wizard.py` footer, `updater.py`, `packaging/build.py` all read it),
  `APP_ID`, `UPDATE_FEED_URL`.
- `windows_launcher/updater.py` — `run_velopack_bootstrap()` (called first in
  `main.py`), `is_packaged()` (frozen + sibling `Update.exe`), `UpdateController`
  (QObject: `check`/`download`/`apply_and_restart` on a `_Worker(QThread)`,
  signals to the panel). `import velopack` guarded; inert from source.
- `terminal_panel.py` — `self.updater` + an **Update** `QPushButton` in
  `_build_toolbar` (visible only when `updater.enabled`), `_wire_updater()` +
  `_on_update_*` slots, `_shutdown_all()` extracted from `closeEvent` and reused
  before `apply_and_restart()`.
- `config.py` — `auto_check_updates` / `update_channel` / `update_prerelease` /
  `last_update_check`.
- `main.py` — `--smoke` flag (waits for shells, exits 0/3) for the build script.
- `packaging/` — `AgentDeck.spec` (onedir; `collect_all` winpty/sounddevice/
  pywhispercpp, `collect_submodules('voice_capture')`, big Qt `excludes`),
  `hooks/hook-pywhispercpp.py` (delvewheel root DLLs), `build.py` (freeze +
  bundle asserts + smoke + `vpk pack` + `checksums.py`), `checksums.py`
  (`SHA256SUMS.txt` over `packaging/Releases/`), `README.md` (runbook).
- `windows_launcher/constraints.txt` — exact pins for the whole dependency
  closure; CI installs with `-c constraints.txt`. Regenerate on a deliberate
  bump (`pip freeze` from `.venv-build`, minus the editable/self lines).
- `.github/workflows/release.yml` — push a `v*` tag → build on `windows-latest`
  → `vpk upload github`, then `gh release upload SHA256SUMS.txt`. Actions are
  pinned to commit SHAs; Azure signing secrets are scoped to the build step
  only (never `$GITHUB_ENV`).
- **Not touched:** `voice_engine.py` (its `sys.path` hack is skipped when frozen;
  `voice_capture` must be `pip install`ed into the build venv instead).
- Build with **python.org 3.11** in `windows_launcher/.venv-build` — the run
  `.venv` is MS Store Python and can't build. `pywin32` was dropped
  (unused; only `ctypes.windll` is used).
- Unsigned unless the `AZURE_*` / `TRUSTED_SIGNING_*` repo secrets are set (then
  the CI signs every binary with Azure Trusted Signing); unsigned → SmartScreen
  "More info → Run anyway". Either way every release carries `SHA256SUMS.txt`.
  Per-user install to `%LOCALAPPDATA%\AgentDeck\` (no UAC) is what makes
  self-update work.

## Gotchas hit before

- `QDropEvent` in tests needs `Qt` imported in the test file (PySide6
  `QDropEvent(QPointF, Qt.DropAction, QMimeData, Qt.MouseButton, Qt.KeyboardModifier)`).
- `QUrl.fromLocalFile(r"C:\a\b").toLocalFile()` returns `C:/a/b` — normalize with
  `QDir.toNativeSeparators` before showing a Windows path.
- Connecting a Qt signal straight to another signal that takes fewer args can
  fail — wrap in `lambda: target.emit()`.
