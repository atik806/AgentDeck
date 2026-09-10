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
   drop. Model auto-downloads on first use: `voice_model` defaults to `"auto"`
   (`voice_models.recommend_model()` → `base.en`/`small.en` by RAM+cores; old
   `tiny.en` configs migrate to `"auto"` at config v3). `_ensure_built()` reads
   accuracy/VAD/segmentation knobs from config (`voice_beam_size`,
   `voice_n_threads`, `voice_initial_prompt` (default = code/terminal
   vocabulary), `voice_vad_aggressiveness`, `voice_silence_ms` /
   `voice_min_speech_ms` / `voice_preroll_ms`, `voice_language`). The engine
   emits the **raw** utterance; `terminal_panel._on_voice_text` runs
   `voice_postprocess.apply()` (drop whisper's trailing period; opt-in spoken
   punctuation / command fixups — no auto-capitalise) after checking it for a
   spoken command (see section 5's voice-overhaul note). Overlay is
   parented to the **window** (not the stack — loses the z-fight otherwise) and
   kept over the panes via `VoiceOverlay.set_bounds()`. Audio deps optional:
   missing → mic disabled, panel fine. One additive hook in
   `voice_capture/audio/capture.py` (`on_level` callback);
   `TranscriptionEngine` gained additive `initial_prompt`/`beam_size`/
   `no_context` args. Tests: `test_voice_engine.py`, `test_voice_overlay.py`,
   `test_voice_models.py`, `test_voice_postprocess.py`, `test_voice_download.py`
   (all offline), panel suite §25–28. Needs `sounddevice webrtcvad-wheels
   pywhispercpp numpy` in `windows_launcher/.venv` (added to requirements.txt).
   **Settings ▸ Voice input** (`settings_dialog._build_voice_section`, Pro-gated
   via `voice_enabled=` kwarg) exposes the master on/off (`voice_input_enabled`),
   mic device, model (with a "Download now" `voice_download.ModelDownloadController`
   + progress bar), language, mic sensitivity, and post-processing. First-run of
   a not-yet-cached model shows `model NN%` in the overlay caption
   (`VoiceEngine._prefetch_model` → `model_progress` signal →
   `overlay.set_progress`). `voice_model`/`voice_language`/`voice_beam_size`/
   `voice_n_threads`/`voice_initial_prompt`/`voice_vad_aggressiveness`/
   `voice_post_processing` are in `account.CLOUD_KEYS`; mic device, overlay
   position, the master switch and segmentation ms are machine-local.
   `terminal_panel._open_settings` diffs the voice keys and calls
   `VoiceEngine.apply_config()` — while idle it drops the built pipeline; while
   listening it stops and restarts so a model change takes effect at once.
   **Mic robustness:** `AudioCapture` gained `on_lost` (fatal input failure,
   distinct from transient `on_error`) — fired on a stream-open error or when
   the capture loop starves for `starve_timeout` (4 s, WASAPI often just stops
   calling the callback on unplug). `VoiceEngine._on_capture_lost` turns a
   PortAudio permission error into "turn the mic on in Windows Settings ▸
   Privacy ▸ Microphone" and, once per session, auto-retries on the system
   default device when a *custom* mic dies (`voice_mic_autofallback`, default
   on). The raw daemon-thread model is kept (proven, 48 engine tests) rather
   than a QThread rewrite — the worker→GUI hop is already a queued `_Bridge`
   signal.
   **Global hotkey** (`global_hotkey.py`): a `QAbstractNativeEventFilter` +
   `ctypes` `RegisterHotKey` (no new dep, not a keyboard hook). `parse_hotkey`
   → `(mods|MOD_NOREPEAT, vk)`; a bare key is allowed only for F1–F24.
   `terminal_panel._build_voice` installs it + `_rebind_global_hotkey()` (bound
   only when `voice_global_hotkey_enabled` + `voice_input_enabled` + Pro;
   re-run from `_apply_entitlements` and after Settings). A press fires
   `_on_global_voice_hotkey` (250 ms de-dupe vs the focused `QAction`):
   `voice_global_target="agentdeck"` un-minimises + raises the window then
   toggles; `"foreground"` records `GetForegroundWindow()` and
   `_paste_to_foreground()` sends the transcript there via clipboard +
   synthesised Ctrl+V (clipboard restored after 400 ms). `voice_hotkey` /
   `voice_global_hotkey_enabled` / `voice_global_target` are machine-local.
   Settings ▸ Voice has a `QKeySequenceEdit` + a target combo. `_shutdown_all`
   → `GlobalHotkey.dispose()`.
   **Theming + partial transcript:** the capsule now paints from `theme.py`
   `voice_*` tokens (Mocha + Latte) instead of a fixed dark HUD — repaints on
   `theme.manager().changed`; grew to 208×34. `VoiceOverlay.set_partial(text)`
   shows whisper's interim per-segment text (dim, italic, left-elided tail, no
   auto-revert) while `listening`, cleared by the next `set_state` or the final
   `flash_text`. Pipeline: `TranscriptionEngine.transcribe(audio, on_partial=)`
   forwards `new_segment_callback`; `AudioCapture(on_partial=)` →
   `VoiceEngine._emit_partial` (gated by `voice_show_partial`) → `partial`
   signal → `overlay.set_partial`. Not word-streaming — whisper.cpp fires the
   segment callback near the end of each `transcribe()` pass; a true rolling
   partial would need overlapping-window re-decode (out of scope).
   **Spoken commands + punctuation:** `voice_commands.parse(text, cfg)` →
   `(action, rest)` where a *whole* utterance matching a phrase set is
   `submit`/`newline`/`scratch`/`stop` (mid-sentence "send" stays literal;
   **"enter" is NOT a submit phrase** — whisper hallucinates it on trailing
   silence and it was auto-running the prompt).
   `terminal_panel._on_voice_text` dispatches: `submit`/auto-send →
   `TerminalView.submit()` (writes `\r` + emits `submitted`), `scratch` →
   `TerminalView.erase_text(_last_voice_len)` (DEL bytes, best-effort, never
   crosses `\n`), `stop` → `stop_listening()`. `_on_voice_text` **bails when
   `engine.is_listening` is False** — a decode that resolves after the user hit
   Ctrl+Shift+X / Enter / "stop" is dropped GUI-side too (not just in
   `_emit_transcription`), so a stop never leaks stray text or an auto-submit.
   The floating overlay's own bare-Enter (`submit_requested` →
   `_on_overlay_submit`) just **stops dictation + refocuses the pane**, it does
   not run the line. The **engine now emits the raw utterance**; all clean-up moved to the panel via `voice_postprocess.apply`
   (no auto-capitalise — feeds a shell; opt-in `voice_spoken_punctuation`
   "period"→"." and experimental `voice_command_fixups` "get"→"git").
   `voice_auto_send` (default off) presses Enter after each phrase unless it
   ends with a `\`/`|`/`&&` continuation. `voice_commands_enabled` /
   `voice_spoken_punctuation` / `voice_auto_send` / `voice_command_fixups` are
   in `CLOUD_KEYS`.

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

11. **Workspace activity glow dot (2026-08-31)** — each row in the WORKSPACES
    sidebar carries an `_ActivityDot` (`workspace_sidebar.py`) that glows green
    with a breathing halo while an agent is working in that workspace, and
    paints nothing when idle (its layout slot is kept either way, so the badge
    never shifts). "Working" = the pane is producing pty output: `TerminalView`
    already stamps `_last_output_at` on every flush, so `TerminalView.is_busy()`
    is `now - _last_output_at < _BUSY_WINDOW_S` (2.5 s — bridges spinner
    frames); `TerminalPane.is_busy()` / `Workspace.is_busy()` fan it up. The
    panel's 1 s `_watchdog` (`_refresh_status`) calls
    `WorkspaceSidebar.refresh_activity()`, which flips each existing row's dot
    without rebuilding it (rebuilding would kill the pulse animation). New theme
    token `activity`. Tests: `test_plugins_panel.py` §3 (offline).

12. **Free / Pro plan gating (2026-08-31)** — before this `profile.plan` only
    drove the toolbar badge; now it gates features per the pricing page at
    `vibeflow.tech/agentdeck`. New `entitlements.py` (Qt-free, single source of
    truth): Free = 1 workspace / ≤4 panes / manual updates; Pro = unlimited
    workspaces & panes, voice input (Ctrl+Shift+X), cloud settings sync,
    background auto-update, per-workspace folders & agents. `is_pro()` set =
    `pro|paid|team|plus`. Wiring: `Workspace(max_panes=)` + `set_max_panes()`
    clamp `initialize`/`add_pane`; `terminal_panel._apply_entitlements()` (runs
    on `account.profile_ready` and once at wiring) sets every workspace's cap,
    swaps the voice-button tooltip, one-shot tops the first workspace back up to
    `default_count` if Pro resolves after launch, and kicks
    `_auto_check_updates()` (Pro only, once). `_new_workspace_interactive`
    blocks a 2nd workspace for Free; `_toggle_voice` / `_toggle_overlay_visible`
    gate via `_voice_gated()`; `_prompt_upgrade(feature)` = status line + a
    QMessageBox whose "See Pro" opens the pricing URL. `account.py`:
    `pull_cloud_settings` now reads the profile row first (learns the plan,
    emits `profile_ready`) then gates the settings read on `cloud_sync_enabled`;
    `push_cloud_settings` gated too; `__init__` + `_refresh._done` auto-fetch the
    profile so a restored session resolves the plan without opening the account
    dialog. `account_dialog` sync checkbox disabled + "(Pro)" for Free.
    `main.py` startup update check → `panel._auto_check_updates`. Tests:
    `test_entitlements.py` (offline), `test_panel_entitlements.py`
    (offscreen), `test_account.py` +[16b]. `test_panel.py` builds its panels
    with a Pro `AccountController` (it tests pane mechanics, not the gate).

13. **Automatic subscription expiry (2026-08-31)** — a Pro grant now carries an
    end date and downgrades on its own. New migration
    `supabase/migrations/20260831150000_plan_expiry.sql`: adds
    `profiles.plan_expires_at timestamptz` (NULL = never) + `plan_interval`
    (`month|year` hint for the admin renew button); a `security definer`
    `public.expire_stale_plans()` that sets `plan='free'` where
    `plan_expires_at < now()`; a **pg_cron** job (`expire-stale-plans`, every
    15 min) that calls it; and it **drops `profiles: update own` / revokes
    client UPDATE** on `profiles` (the client only ever selects it — this closes
    a self-upgrade hole). Requires pg_cron enabled once on the hosted project.
    Client: `entitlements.plan_active(plan, expires_at)` / `plan_expiry()` are
    the new time-aware helpers (`is_pro()` stays a pure name check);
    `AccountController.plan` now returns the **effective** plan (a lapsed Pro
    reads back `"free"`), with `raw_plan` / `plan_expires_at` for the stored
    values — so every existing gate downgrades with no call-site change.
    `account.py` captures `plan_expires_at` from the profile row and the cloud-
    sync gates read the effective plan. `terminal_panel`: a 30-min `_plan_watch`
    re-fetches the profile, a one-shot `_plan_expiry_timer` fires at the exact
    expiry moment, both → `_recheck_plan` → `_apply_entitlements` (non-
    destructive: open panes stay, only new ones past the Free cap are blocked).
    `account_dialog` shows a "Pro renews …" / "Pro expired …" line. VibeFlow
    Admin (`api/agentdeck.js` + `AdminAgentDeck.jsx`, separate repo) gained the
    expiry field + "Grant Pro · 1 month / 1 year" buttons. Tests:
    `test_entitlements.py` [6], `test_account.py` [5c], `test_panel_entitlements.py`
    [5b], `test_account_dialog.py` [2b], `test_navbar.py` (lapsed-badge checks).

14. **Free tier is a 7-day trial (2026-08-31, v0.7.0)** — after signup a Free
    user has 7 days, then must be on an active Pro plan or the app won't open.
    New migration `20260831160000_free_trial.sql`: `profiles.trial_ends_at`
    (`NOT NULL default now()+7d` — backfills existing rows, and the signup
    trigger gets it free). No cron, no RLS change (client already select-only).
    Client: `entitlements.access_allowed(plan, trial_ends_at, plan_expires_at)`
    is the master gate = `plan_active` OR `trial_active` (fail-open on a missing
    date); `trial_active` / `trial_days_left` / `trial_deadline`(= `plan_expiry`)
    / `TRIAL_DAYS`. `AccountController`: `_absorb_profile()` now the shared
    plan/expiry/trial extractor; new `trial_ends_at` / `access_allowed` /
    `trial_days_left` props + `load_profile_blocking()` (sync fetch for the
    startup gate). `main.py`: after the login gate, `load_profile_blocking()` +
    `TrialGateDialog` (new `trial_gate.py`, sibling of `LoginWindow` — Upgrade /
    re-check / sign-out-&-quit) if `not access_allowed`. `terminal_panel`: new
    `_trial_timer` (fires at the deadline) + `_recheck_trial`; `_apply_entitlements`
    front-guards with `_enforce_trial_block()` (shows the gate, `_force_quit` +
    close on decline — mirrors `_require_login`); a themed `TrialBanner`
    (`trial_banner.py`) inserted above the sidebar/stack in `_build_body`, shown
    in the last 3 days via `_refresh_trial_banner`, dismissal remembered in
    `config["trial_banner_dismissed_on"]` (epoch-day, machine-local); a last-day
    `QMessageBox`. `navbar` chip shows **TRIAL**; `account_dialog` shows the
    countdown / "Trial ended" note. Admin: `trial_ends_at` in `USER_COLS`,
    `extend_trial` on the PUT, "Trial +7d" button, "Trials ending ≤3d" stat.
    Tests: `test_entitlements.py` [7], `test_account.py` [5e]/[5h],
    `test_trial_gate.py` (new), `test_panel_entitlements.py` [7],
    `test_account_dialog.py` [2c], `test_navbar.py` (TRIAL badge). Same 2–3
    pre-existing offscreen panel-suite fails.

15. **Catppuccin reskin (2026-08-31, v0.7.1)** — the chrome was flat near-black
    + a generic blue while the splash and logo already used Catppuccin. Unified
    on it: `theme.py` `_DARK` = **Mocha**, `_LIGHT` = **Latte**, `_ANSI` = the
    Mocha/Latte ramps (Latte brights nudged darker for a light ground). New
    `accent_2` token (blue→teal) drives the account-chip avatar disc gradient
    (`navbar.circular_avatar`). `on_accent` is now dark (`#1e1e2e`) so text on
    the pastel accent reads — `workspace.py` gained a per-state `badge_fg`
    (idle pane badge uses `text_muted`, not `on_accent`, or it clashes with its
    own border bg; this also fixes a latent light-mode invisibility). `_WS_ACCENTS`
    → Catppuccin hues; sidebar swatch text → `on_accent`; active pane title →
    bold; wordmark 12→13px; `vt_screen` static-fallback palette refreshed.
    **Deliberately not changed:** setup wizard / login / trial gate / `agents_ui`
    stay amber (front-door vs in-app split). (`voice_overlay.py` used to keep a
    fixed dark HUD palette — now theme-token driven, see the voice-overhaul
    Phase 5 note in section 5.)
    Qt QSS has no box-shadow/transition, so the pitch's glows/motion were
    dropped — this is palette + typography only. Tests: `test_theme.py` green;
    same pre-existing panel-suite fails.

16. **UI regression fixes vs. the design mockup (2026-08-31, v0.7.2)** — three
    chrome details had drifted from the reference mockup:
    - `workspace_sidebar.py`: the **Plugins** nav strip (`#wsNav` + its
      `#navRule`) is now **pinned to the bottom** of the sidebar, under the
      workspace list, instead of sitting above the header. Only the
      `root.addWidget` order changed — the widgets are still built top-down and
      `plugins_selected` / `set_plugins_active` are untouched.
    - Workspace-row **name truncation** ("Workspace 1" clipped to "Workspa"):
      sidebar `WIDTH` 214 → 232 and `_WorkspaceRow` spacing 8 → 6, so a default
      name fits even on the active row (which also shows the edit + close
      buttons). Long custom names can still clip — pre-existing, left alone.
    - Sidebar heading "WORKSPACES" → "Workspaces" (letter-spacing 1 → 0.5px,
      10 → 11px).
    - `terminal_panel.py`: toolbar section labels "SHELL/LAYOUT/FONT" →
      "Shell/Layout/Font" (QSS has no text-transform; the caps were literal),
      label weight 700 → 600. The **Update** button is now a filled accent
      button (`objectName="toolbarUpdate"` + a `_toolbar_qss` rule) instead of a
      grey surface button.
    Not touched (out of scope this pass): the status bar still shows the
    workspace/pane/running counts + shortcut hints rather than the mockup's
    `N panes · shell · agent` / `workspace · branch`. Tests: `test_plugins_panel.py`,
    `test_theme.py`, `test_navbar.py` green; panel suite unchanged.

17. **Rounded terminal panes + focus glow + always-on Update button
    (2026-08-31, v0.7.3)** — matched the pane chrome to the mockup:
    - `workspace.py` `_refresh_style`: `TerminalPane` gets `border-radius: 10px`;
      `#paneHeaderHost` gets `border-top-left/right-radius: 9px` so the header bg
      doesn't square off the top corners. Bottom corners stay clean because the
      frame bg and the canvas fill are both `term_bg` — the canvas's square
      corner is invisible against the frame's rounded `term_bg` fill.
    - `Workspace._body` margins 0 → 6px and splitter `setHandleWidth` 4 → 8, so
      the rounded panes float off the window/sidebar edge and each other.
    - Pane header: the **restart** control is now an always-visible `↻` icon
      button (was a "Restart" text button shown only on failure/exit); header
      order is now expand · restart · close (`⤢ ↻ ✕`), matching the mockup. All
      the `_restart_btn.setVisible(...)` toggles removed.
    - `terminal_view.py`: the pane scrollbar is **hidden while there's nothing
      to scroll** (`_sync_scrollbar` → `setVisible(maximum > 0)`) and restyled
      thin/quiet (`_style_scrollbar`, called from `__init__` + `apply_theme`;
      added `import theme`) with a 6px bottom margin so it clears the rounded
      corner. Verified live (dev build screenshot) in dark mode.
    - `test_panel.py` step 26: the "spoken text reached the prompt" check now
      matches against the screen with line breaks stripped — a 40-col pane
      hard-wraps the prompt line, which is not a regression.
    - **Active-pane focus glow**: `TerminalPane` now carries a
      `QGraphicsDropShadowEffect` (`self._glow`, offset 0, blur 18, colour
      `accent` / `pane_border_dead` when dead). `_refresh_style` enables it only
      for the active pane. QSS has no box-shadow, so a graphics effect is the
      only way — same trick as the Update-button glow.
    - **Update button always visible**: `terminal_panel._build_toolbar` no
      longer hides `_update_btn` when `updater.enabled` is False (source /
      non-Velopack builds). It stays in the toolbar; a build that can't update
      itself just shows `updater.unavailable_reason` as the tooltip and reports
      it on click (`updater.check` already emits `error` → status bar when
      `_mgr is None`).

18. **Animated update download/install dialog (2026-08-31)** — the self-update
    only ever showed a transient status-bar line while a release downloaded /
    installed. New `update_progress.py` (`UpdateProgressDialog`, themed, modal,
    no close box): a determinate `QProgressBar` that tracks download percent,
    switches to an indeterminate sweeping chunk for the "Installing update —
    restarting" phase, plus a looping opacity pulse on a ⬇/⚙ glyph so it never
    looks frozen. `terminal_panel`: `_show_update_dialog` / `_close_update_dialog`
    / `_forget_update_dialog` + `self._update_dialog`; `_on_update_available`
    puts it up before `updater.download()`; `_on_update_progress` feeds it (still
    also writes the status bar); `_on_update_error` closes it; `_on_update_ready`
    → on "Restart now" it flips to `start_installing()`, `processEvents()` so it
    paints, then `_shutdown_all()` + `apply_and_restart()`. No `updater.py`
    change. Tests: `test_update_progress.py` (offline, 13 checks).

19. **Update controls moved into Settings + feature review (2026-08-31)** — the
    toolbar **Update** button (`_update_btn`, `#toolbarUpdate` QSS, `_UPDATE_GLOW_QSS`)
    is **gone**; updating now lives in `settings_dialog.py` → Updates section:
    a **Check for updates** button + an inline status `QLabel` fed by the
    `UpdateController` signals (`busy_changed`/`up_to_date`/`available`/`progress`/
    `ready`/`error`) while the dialog is open, connections dropped in
    `done()`/`closeEvent`. `SettingsDialog(..., updater=, current_version=)` new
    kwargs; `terminal_panel._open_settings` passes them.
    - The **"an update is waiting" glow** now pulses on the **gear/settings
      button** (`_install_update_glow` → `self._settings_btn`), pointing at where
      updates live; the solid-red text restyle is dropped (it was button-text
      specific), just the halo + a tooltip swap.
    - `_wire_updater` no longer touches `_update_btn`; the modal download prompt
      / progress dialog / restart prompt still fire from the panel on a launch
      check.
    - **Feature fix:** the `update_channel` setting (Stable/Beta) was dead —
      never reached Velopack. `updater._update_options(channel)` now builds a
      `velopack.UpdateOptions(AllowVersionDowngrade=False,
      MaximumDeltasBeforeFallback=10, ExplicitChannel=…)` (`""`/`"stable"` →
      `None`; any binding-shape drift → `None` → URL-only ctor as before),
      `UpdateController(channel=)` passes it, `terminal_panel` seeds it from
      config. NB there is still **no beta release pipeline** (`build.py` packs
      the default channel only) — the client wiring is correct but picking Beta
      today finds nothing; the combo warns "restart to take effect".
      `UpdateController.busy` property added.
    Tests: `test_settings_dialog.py` (new, offline, 19), `test_panel_account.py`
    [2]-[4] rewritten (glow on `_settings_btn`, no `_update_btn`), `test_updater.py`
    green. `test_panel.py` unchanged (same lone offscreen "drop focus" flake).

20. **Notes panel (2026-09-02)** — a second sidebar nav item, **Notes**, pinned
    under **Plugins** in `workspace_sidebar.py`'s bottom nav strip (`notes_selected`
    signal + `set_notes_active()`; the nav button build is now factored into a
    local `_nav_button()` helper). It opens a full-area local notebook:
    - `notes_store.py` (Qt-free) — `NotesStore` over one JSON file,
      `%APPDATA%\multi-terminal\notes.json` (beside `config.json`, **not** in it
      and **not** cloud-synced). `Note` dataclass; ordered newest-`updated`
      first; every mutation writes the whole file back atomically (temp +
      `os.replace`); missing / corrupt / wrong-shape files load as an empty
      list, never raise. `derive_title()` = explicit title else first non-blank
      body line (leading `#` stripped) else "Untitled note".
    - `notes_panel.py` — `NotesPanel(store=, config=)`: a note list (custom
      `_NoteRow`: title / preview / relative time) ∥ a title `QLineEdit` + body
      `QPlainTextEdit` + a "Saved 3m ago" footer, plus New note / Delete.
      Debounced autosave (600 ms `QTimer` → `flush()`); also flushes on row
      switch, `hideEvent`, and `_shutdown_all`. Empty state when there are no
      notes. `apply_theme()` re-runs its QSS. `note_icon()` = a drawn ruled page
      (emoji renders broken here, same as `plugin_icon`).
    - `terminal_panel.py` — `_notes_panel` added to `_main_stack` at index 2
      (`_ws_stack` 0, `_plugins_panel` 1); `_notes_active` flag mirrors
      `_plugins_active`. `_show_notes()` / `_leave_notes()` mirror the plugins
      pair; the voice-overlay hide/restore is now shared via
      `_hide_voice_overlay()` / `_restore_voice_overlay()`. `_show_plugins` and
      `_show_notes` each clear the other; `_select_workspace` leaves both;
      `_refresh_sidebar` passes `active=None` and sets both nav buttons when
      either view is up. `_on_theme_changed` calls `_notes_panel.apply_theme()`.
    Tests: `test_notes_store.py` (new, offline, 26), `test_notes_panel.py` (new,
    offline, 25 — note: assert with `isHidden()`, not `isVisible()`, on an
    unshown panel). `test_plugins_panel.py` / `test_theme.py` / `test_navbar.py`
    still green.

21. **Pane header buttons visibility fix (2026-09-02)** — the `⤢ ↻ ✕`
    (expand / restart / close) controls in `TerminalPane`'s header strip were
    near-invisible: bare `pane_title` (muted `#a6adc8`) glyphs on `transparent`,
    no border, 11px, on the active pane's navy `pane_header_bg_active` header
    (on a single-pane workspace that header spans the window, so they read like
    faint title-bar buttons). `workspace.py` `_refresh_style`: the three buttons
    now use the brighter `text` token on a `surface` fill with a `1px border` +
    `border-radius: 5px` (accent fill/border when `_expand_btn` is toggled on),
    glyph 11→13px; button size `setFixedWidth(22)` → `setFixedSize(26, 22)`.
    Hover rules unchanged. Verified with offscreen screenshots in dark + light,
    active + inactive panes. `test_theme.py` (25) / `test_plugins_panel.py` (68)
    green; no test asserted the old width.

22. **Vercel / Jira MCPs on opencode — plugin OAuth allowlist Phase 3
    (2026-09-02)** — symptom: opencode's `/mcp` panel showed only `github`
    ("1 MCP") even with Vercel + Jira connected, because those two tokenless
    OAuth plugins were gated to `mcp_targets.OAUTH_ALLOWLIST = {"claude"}`
    (`docs/PLUGINS.md §14` Phase 2). Fix is one data change:
    `OAUTH_ALLOWLIST = {"claude", "opencode"}`. opencode's native remote-MCP
    support does auto-DCR/PKCE OAuth and opens the browser on first tool use, so
    `vercel_mcp.inject` / `jira_mcp.inject` now write
    `~/.config/opencode/opencode.json` (`mcp` map, `{"type":"remote","url":…,
    "enabled":true,"x-agentdeck-managed":true}`, no token). `caps()` /
    `supports_agent` / the Plugins detail "Enabled for:" line and per-agent OAuth
    hints all key off the allowlist, so they update for free. Already-connected
    plugins re-wire on the controller's next `__init__` (app relaunch) via
    `ensure_wired` (`plugins_wire_all_agents` default True → every installed
    agent); also a new **Re-sync to agents** button on each of `_VercelDetail` /
    `_JiraDetail` calls `controller.ensure_wired()` live. Still Claude-only for
    `codex` / `gemini` / `qwen` / … until each in-pane OAuth command is verified.
    Tests: `test_mcp_targets.py` [1]/[3], `test_vercel_mcp.py` [1]/[7],
    `test_jira_mcp.py` [1]/[7], `test_vercel_controller.py` [4],
    `test_jira_controller.py` [4], `test_plugins_panel.py` [5]/[6] extended.
    Manual verify still pending: launch opencode, confirm 3 MCPs + a live Vercel
    OAuth handshake in the pane.

23. **Conversation handoff (2026-09-02)** — a pane's header now carries a `⤳`
    button (before `⤢`): hand this pane's agent conversation to a new pane in the
    same workspace, running whichever agent you pick.
    - `agent_sessions.py` (new, Qt-free) — one `SessionAdapter` per agent in a
      registry; `adapter_for()` never returns `None` (unknown → `_GenericAdapter`,
      all-`None`). Full impls: **claude** (`~/.claude/projects/<slug>/<uuid>.jsonl`,
      slug = `re.sub(r"[^a-zA-Z0-9]","-",path)` — verified), **codex**
      (`~/.codex/sessions/**/rollout-*.jsonl`), **opencode** (SQLite
      `opencode.db` — reads the **legacy `message`+`part` tables** this machine
      uses, then `session_message`, then `opencode export` subprocess). Best-effort:
      aider (history file is already Markdown), goose. Resume-only stubs:
      gemini/qwen/cursor-agent. `ADK_AGENT_HOME_DIR` env override redirects every
      agent's state dir for tests (mirrors `ADK_MCP_CONFIG_DIR`).
      API: `locate_latest` / `resume_command(…, fork=)` / `transcript_markdown(…,
      include_thinking=, max_chars=)` (head+tail truncation w/ omission marker) /
      `initial_prompt_command` / `supports_resume`.
    - **Same-agent** → native resume command in the new pane
      (`claude --fork-session --resume <id>`, `opencode --session <id> --fork`,
      `codex resume <id>`, `goose session --resume --name <n>`). **Cross-agent** →
      `agent_sessions.transcript_markdown` → `github_mcp.write_handoff_doc(folder,
      md)` writes `.agentdeck/handoff-<n>.md` (reuses `AGENTDECK_DIR` +
      `_git_exclude`), target launched with the prompt as a CLI arg
      (claude/codex/gemini/qwen) or — for agents with no prompt arg — bare + the
      instruction `insert_text`'d in **without Enter** 2.5 s later.
    - `handoff_dialog.py` (new) — `HandoffDialog` (blue accent, model on
      `new_workspace_dialog`): source-agent combo (+ Plain shell), editable source
      folder, target-agent combo + `InstallHint`, live mode note (Resumes… ↔
      Exports…), Fork / Include-thinking / any-cwd checkboxes.
    - `workspace.py`: `TerminalPane.handoff_requested` signal + `_handoff_btn`
      (`#paneHandoff`, shares the `_refresh_style` button QSS), `startup_command` /
      `source_dir` / `detect_agent_key()` props; `Workspace.pane_handoff_requested`
      + `add_pane_with_command(cmd)` (new — `add_pane` stays plain-shell for
      Ctrl+Shift+T; respects `max_panes`).
    - `terminal_panel.py`: `_start_handoff` (Pro gate → dialog) / `_do_handoff`
      (locate → build command → `add_pane_with_command`; pre-trust + plugin
      re-wire first, like `_add_workspace`). `entitlements.handoff_enabled` (Pro,
      mirrors `plugins_enabled`). `config.py`: `handoff_fork_session` (True),
      `handoff_include_thinking` (False), `handoff_max_transcript_chars` (200k,
      range 10k–5M).
    - **cwd limitation:** no OSC-7 tracking, so `source_dir` stays the folder the
      pane opened in even after `cd`; the dialog's editable folder field + the
      any-cwd checkbox are the workarounds.
    - **Fixes 2026-09-02 (same day, after first end-to-end test):**
      - **Wrong-conversation bug** — `locate_latest` used newest-mtime, so it
        grabbed whatever claude session was written last (often a *different*
        Claude Code window). Now: `TerminalView` stamps `_agent_started_at`
        (epoch) when it types the startup command; `TerminalPane.agent_started_at`
        → `_do_handoff` passes `after=` → `agent_sessions._pick()` filters
        candidates to those **created** (`st_ctime`, = creation time on Windows)
        at/after that moment, falling back to newest. Verified: picks the pane's
        own session, not a concurrent one.
      - **`%APPDATA%` redirection** — the cross-agent transcript was briefly
        written under the config dir; on MS Store Python (the dev `.venv`) that
        redirects into a per-package LocalCache the *target agent's* plain shell
        can't read. Reverted to `<working_folder>/.agentdeck/handoff-<stamp>.md`
        (git-excluded, pruned to 8) — `agent_sessions.write_handoff_doc(folder,
        md, …)`, removed from `github_mcp`.
      - **Silent failures** — `_start_handoff` now wraps the dialog/handler in
        try/except → status-bar message + traceback instead of vanishing; the
        source-agent default falls back through startup-command → shell title →
        the workspace's configured agent; a not-installed target says so.
      - **Empty transcript** — `transcript_markdown` returns `None` for a
        bootstrap-only session (`_Doc.__bool__`), so the handoff cleanly starts
        the target fresh instead of writing a header-only file.
      - Transcript budget default 200k→**60k**; tool-result cap 2000→**600**.
      - **opencode cross-agent target** launches `opencode --prompt "<abs path>"`
        (was: bare launch + `insert_text` into the TUI, which opencode's TUI
        wipes on first paint). "no session / empty transcript / not installed"
        are now Yes/No dialogs, not transient status lines. Every step is logged
        to `~/.agentdeck-handoff.log` (`TerminalPanel._hlog`).
    - Tests: `test_agent_sessions.py` (new, 63 — incl. `after=` watermark,
      empty→None, working-folder doc store), `test_handoff_dialog.py` (new, 17),
      `test_entitlements.py` [+1], `test_panel.py` §30 (Pro gate + resume with
      watermark + transcript pane spawn). Same lone pre-existing offscreen "drop
      focus" flake in `test_panel.py`.
      Manual smoke still pending (needs sign-in): claude→claude fork with two
      concurrent claude windows, claude→(installed target) transcript,
      opencode→opencode fork, plain-shell source, Free-plan upsell.

24. **Voice perf + Ctrl+Shift+X reliability + Settings scroll (2026-09-04,
    v0.12.1)** — three user-reported issues:
    - **Model too slow to load / transcribe.** `voice_models.recommend_model()`
      re-tiered: `"auto"` now resolves to **`base.en`** for almost every
      machine (was `small.en` for anything with ≥6 GB / >2 cores — ~450 MB,
      slow to load *and* to decode on CPU). `small.en` now needs ≥16 GB **and**
      ≥12 cores; `tiny.en` catches <4 GB / ≤2 cores. New
      `voice_models.recommend_threads()` = `min(8, cores-2)` (whisper.cpp
      barely scales past 8 and oversubscribing hurts while the agent runs);
      `voice_engine._resolve_threads()` uses it whenever `voice_n_threads` is 0
      (the default) instead of leaving whisper.cpp on its 4-thread default.
    - **First Ctrl+Shift+X pays the whole model load.** `VoiceEngine.prewarm()`
      (new) builds the pipeline + `ensure_loaded()`s the model on a daemon
      thread; `terminal_panel._build_voice` fires it 2.5 s after launch (and
      `_apply_entitlements` fires it when the plan resolves to Pro later).
      Pro-gated, honours `voice_input_enabled`, no-op if already built.
      Env opt-out `ADK_NO_VOICE_PREWARM=1` (set by the 4 panel-building test
      files) so a test never pulls 150 MB into RAM.
    - **"Ctrl+Shift+X stops working after a while."** Root cause: the OS
      `RegisterHotKey` posts `WM_HOTKEY` *even while AgentDeck is focused*, and
      the in-app `Ctrl+Shift+X` `QAction` (`ApplicationShortcut`) fires then
      too — so one keypress reached **both** `_on_global_voice_hotkey` **and**
      `_toggle_voice`, calling `engine.toggle()` twice = net no-op (it broke
      the instant the window had focus). The old 250 ms dedupe was one-sided
      (only guarded a second *global* activation; the QAction never stamped the
      clock). Fix: both handlers funnel through
      `TerminalPanel._voice_hotkey_dedupe()`. Plus a safety net in
      `VoiceEngine`: a `_busy` flag still set `> 25 s` (a wedged start/stop
      worker) is treated as stale so `toggle()` can recover instead of
      silently no-opping forever (`_busy_since` / `_busy_is_stale`).
    - **Settings dialog overflowed the screen** (the "Done" button off the
      bottom on a 1080p display). `settings_dialog.py`: all sections now live
      in a `QScrollArea` (`#settingsScroll` / `#settingsPage`), the button row
      moved to a pinned `#settingsFoot` outside it, `_fit_to_screen()` sizes
      the dialog to the content clamped to the screen (measures the inner page,
      since a scroll area's own sizeHint is tiny). Width 520–560. Styled thin
      scrollbar.
    Tests: `test_voice_models.py` retiered (16), `test_voice_engine.py` (50),
    `test_settings_dialog.py` (39), `test_global_hotkey.py` (19),
    `test_panel*.py` green bar the lone pre-existing "drop focus" flake.

25. **Voice: crash-on-native-fault + instant stop + Enter reliability
    (2026-09-04, follow-up to v0.12.1 — not yet released/tagged)** — the
    v0.12.1 `prewarm()` shipped three regressions; a 5-agent deep-dive fixed the
    root causes across `voice_engine.py`,
    `voice_capture/{audio/capture.py,transcription/engine.py}`,
    `voice_overlay.py`, `terminal_panel.py`, `main.py`.
    - **Crash (no `last-error.log`).** `prewarm()` never set `_busy`, so a
      `start()` / `apply_config()` / `_on_capture_lost()` that landed mid-prewarm
      nulled `_engine`/`_capture` from under it → **two `pywhispercpp.Model()`
      constructions on two threads** → whisper.cpp/ggml backend init is not
      reentrant → segfault, bypassing `sys.excepthook`. Fixes: (a) a
      **process-wide `_MODEL_INIT_LOCK`** around every `Model(...)` build in
      `TranscriptionEngine._load_model`; (b) `prewarm._work` now holds
      `VoiceEngine._build_lock` (now an **RLock**) across the *whole* build +
      model load, and `apply_config()` / `_on_capture_lost` teardown take that
      lock (bounded 3 s in `apply_config` so a wedged prewarm can't freeze the
      GUI) before nulling refs; (c) `_start_pipeline` opens the mic under the
      lock and re-checks `_listening`/`_aborting`; (d) `shutdown()` sets
      `_aborting` so a prewarm/start worker bails before the interpreter kills it
      mid-`Model()`; (e) **`faulthandler.enable(…, all_threads=True)`** in
      `main.py` → `%APPDATA%\multi-terminal\faulthandler.log` for the next
      native fault; (f) per-engine `.part<id>` temp name in `_prefetch_model`
      (was a shared name → prewarm + start clobbered each other's download).
    - **Ctrl+Shift+X slow to stop (up to ~12 s).** `AudioCapture.stop()` was
      synchronous: `_capture_thread.join(2s)` + a `_flush_segment()` + a
      `_transcribe_thread.join(10s)` that waited out a whole extra whisper pass,
      and `_busy` stayed set that whole time so re-pressing no-op'd. New
      **`stop(discard_pending=False)`**: closes the mic `InputStream`
      synchronously (`abort()`+`close()`, ~ms) and hands the worker joins to a
      daemon `voice-capture-cleanup` thread. `discard_pending=True` (every
      user/system stop — hotkey, Enter, "stop listening", mic-loss, shutdown)
      **skips the flush, drains the segment queue, and aborts the in-flight
      decode** via a real pywhispercpp **`abort_callback`** (verified working in
      1.5.1) wired through `TranscriptionEngine.set_abort_check` +
      `AudioCapture._abort_transcription`. A `_generation` counter drops any
      result that slips past the abort. `_toggle_voice_engine()` flips the
      overlay to idle immediately instead of waiting for the teardown signal.
    - **"Press Enter to stop dictation" unreliable.** Three causes: (a) the
      trailing flushed/queued utterance was transcribed and typed in *after*
      Enter — now `VoiceEngine._emit_transcription` drops anything that resolves
      once `_listening` is clear, plus the capture-level discard/generation
      gate; (b) `_on_pane_submitted` only stopped a fully-listening session —
      now also covers `_busy`/loading and sets the overlay idle at once;
      (c) **the capsule steals keyboard focus on a click/drag** and its
      `keyPressEvent` ignored Return — new `VoiceOverlay.submit_requested`
      signal (bare Enter) routed to the active pane's `submit()`.
    - **Double-fire hardening.** `_rebind_global_hotkey` now **disables the
      focused-window `Ctrl+Shift+X` QAction** whenever the OS-wide `RegisterHotKey`
      is live (it fires `WM_HOTKEY` even when focused), so one keypress can't
      reach both handlers and cancel out. `_toggle_voice` / `_on_overlay_toggle`
      clear a stale `_fg_hwnd` (was typing dictation into the last
      "foreground"-target window). Overlay mic/Ctrl+X now go through
      `_on_overlay_toggle` → same Pro + master-switch gating as the shortcut.
    - Also: `AudioCapture._emit_lost` closes the dead stream itself (was leaking
      an `InputStream` per mic unplug since `stop()` early-returns once
      `_is_running` is clear); `VoiceOverlay.set_available(True)` un-sticks the
      "unavailable" state.
    Tests: `test_voice_engine.py` (54, +[4b2] late-utterance drop),
    `test_voice_overlay.py` (64, +bare-Enter→submit_requested),
    `voice_capture/tests/test_pipeline.py` (33, +[5b] fast discard stop +
    [5c] default stop still flushes). `test_panel.py` green bar the same lone
    pre-existing "drop focus" flake; full offline suite green.

26. **Settings is now an embedded panel, not a popup dialog; prewarm never
    auto-downloads (2026-09-04, uncommitted)** — two follow-ups from a live
    user report ("it's opening a new window" / "why do I need to download the
    voice model, I don't want that").
    - **`settings_dialog.py` split into `SettingsPanel(QWidget)`** (the real
      thing: the category-nav + content UI from the earlier redesign) **and a
      thin `SettingsDialog(QDialog)`** wrapper (kept only for anything that
      still wants a modal popup, and for the test suite — attribute access
      not found on the dialog falls through to `self._panel` via
      `__getattr__`). `terminal_panel.py` builds one `SettingsPanel` in
      `_build_body()` and adds it to `_main_stack` alongside
      `_plugins_panel`/`_notes_panel` — the gear button now calls
      `_show_settings()`/`_leave_settings()` (mirrors `_show_plugins` /
      `_leave_notes`, incl. `_settings_active` folded into
      `_refresh_sidebar`'s `on_nav_view` and `_select_workspace`'s
      leave-every-nav-view call), so Settings is a page of the app like
      Plugins/Notes, not a separate OS window. Everything still saves live, no
      "Done" step: `SettingsPanel.theme_changed`/`font_size_changed` signals
      apply immediately (`theme.set_mode`/`self._set_font`);
      `voice_settings_changed` is debounced 500 ms onto
      `TerminalPanel._apply_voice_settings` (was the old dialog's
      close-time diff-apply) so a run of toggles doesn't each blip a live
      listen. `_apply_entitlements` calls `settings_panel.set_voice_pro(...)`
      to re-gate the voice controls live when the plan resolves/changes
      (the panel is built once and kept alive all session, unlike the old
      per-open dialog).
    - **`VoiceEngine.prewarm()` no longer ever triggers a first-run model
      *download*.** It already loaded a resolved model into RAM 2.5 s after
      launch (see §25/v0.12.1); the docstring said this could also silently
      download it on first run, and a live user report confirmed that's a bad
      surprise for a multi-hundred-MB background fetch nobody asked for. Fixed
      with one early check: `voice_download.model_is_downloaded(resolved)` —
      if the configured model isn't already on disk, `prewarm()` returns
      untouched (no build, no thread, nothing). The first real `Ctrl+Shift+X`
      (or Settings ▸ Voice input ▸ Download now) still downloads on demand,
      with the overlay's visible progress bar, same as always — only the
      *silent background* path is gone.
    Tests: `test_settings_dialog.py` (49, unchanged surface via delegation),
    `test_voice_engine.py` (57, +[4g] prewarm skips an uncached model / warms
    a cached one), `test_panel.py` green (143-144 depending on run; two
    unrelated timing flakes seen once each and not reproduced on rerun, on top
    of the documented pre-existing "drop focus" flake) — confirmed identical
    on a clean-HEAD throwaway `git worktree`, so not a regression.

27. **Routines — schedule an agent prompt to fire at a set time (2026-09-05,
    v0.13.0)** — a new sidebar nav view (below "Notes"). A routine is a saved
    `{prompt, agent, workspace target, weekdays, time}`; while AgentDeck is open
    and the wall clock hits that time, it opens (or reuses) a pane running the
    chosen agent and types the prompt in.
    - `routines_store.py` — Qt-free, same rules as `notes_store.py`: one
      atomic JSON file `%APPDATA%\multi-terminal\routines.json`, tolerant load
      (missing/corrupt → empty list, never raises), `Routine` dataclass with
      `from_dict` sanitising every field (days clamped to 0-6 Mon-first,
      `HH:MM` shape-checked). `RoutinesStore` = `all/get/create/update/
      mark_run/delete`; `_EDITABLE_FIELDS` whitelists what the editor may
      write, `mark_run` (scheduler-only) never bumps `updated`. Helpers
      `format_time_12h` / `schedule_summary` ("Mon, Wed, Fri 8:00 AM" /
      "Daily 8:00 AM"). **Field-order gotcha:** `created`/`updated`'s
      `default_factory=time.time` must be declared *before* the `time: str`
      field or the dataclass body shadows the `time` module.
    - `routine_scheduler.py` — Qt-free, stateless-per-call. `due(routines,
      now=None)` returns the routines whose `time`/`days` match this minute,
      deduped by `id → "YYYY-MM-DD HH:MM"` so `TerminalPanel`'s existing 1 s
      watchdog fires each one exactly once. **Session-local only**: no
      catch-up for a time missed while closed, no OS wake-up.
    - `routines_panel.py` — the list + editor UI (`RoutinesPanel`,
      `routine_icon`). List rows show schedule summary + last-run status;
      editor is prompt / agent picker (reuses `agents`) / workspace target
      ("new" or an existing name via `workspaces_provider`) / weekday chips /
      time. `flush()` on nav-away, `reload()` on nav-in — mirrors NotesPanel.
    - `terminal_panel.py` — `_routine_scheduler` + `_routines_store` +
      `_routines_panel` in `_main_stack`; `_show_routines` / `_leave_routines`
      / `_routines_active` folded into every nav-view switch and
      `_refresh_sidebar`. `_refresh_status` (the 1 s watchdog) calls
      `scheduler.due(...)` → `_run_routine`, which pretrust + MCP-wires the
      routine's own agent pick (the once-per-session flag only covers the
      launch agent), picks/creates a workspace honouring the Free plan cap,
      then seeds the prompt on a `QTimer` retry ladder (3.5/7/12 s, give up at
      20 s) once the pane has produced output. `mark_run` records
      `ok` / `skipped: <reason>`.
    - `entitlements.routines_enabled(plan)` = `is_pro` (same tier as
      `handoff_enabled` / `plugins_enabled`). Nav button stays visible for
      Free (discoverability); the click and `_run_routine` are both gated, so
      a routine created while Pro simply stops firing — quietly, no popup from
      a background timer — if the plan later lapses. **Not cloud-synced** —
      routines reference machine-local workspaces/agents.
    Tests: `test_routines_store.py` (36), `test_routine_scheduler.py` (14),
    `test_routines_panel.py` (37), `test_entitlements.py` (+routines gate).

28. **Terminal mouse clicks now reach programs that read the mouse (2026-09-05,
    v0.13.0)** — closes the caveat noted in feature §4. `terminal_view.py`
    `TerminalCanvas._forward_click` mirrors `wheelEvent`'s convention: if the
    program set a mouse-tracking mode (`1000`/`1002`/`1003`) and `Shift` is
    not held, a left/middle/right press+release is encoded (`_encode_mouse`,
    SGR when `1006`) and sent via `input_requested` instead of starting a
    local text selection. So a TUI's clickable buttons / list rows (Claude
    Code, `fzf --preview`, file pickers) actually respond; `Shift`-click still
    forces local selection. Covered by the existing `test_wheel.py` mode
    plumbing.

29. **Voice overlay visual polish + sidebar drag fix (2026-09-05, v0.13.0)** —
    `voice_overlay.py`: capsule `208x34 → 222x38`, nine thin equaliser bars →
    seven thicker rods with a vertical tint gradient, the bar timer now runs
    even at idle (a slow `sin` "breathing" arch so it reads as alive, not a
    dead graphic), bigger mic button (`22 → 25 px`) with a two-copy staggered
    pulse ring and a gradient disc while listening, a soft outer glow on the
    capsule edge while listening/error. `test_voice_overlay.py` updated (idle
    timer now expected active; footprint bound `220 → 230`).
    `workspace_sidebar.py`: `_WorkspaceRow` now emits `clicked` on
    mouse-**release**, not press — selecting rebuilds every row (see
    `WorkspaceSidebar.refresh`), which on press deleted the row out from under
    the in-progress drag gesture before `_start_drag()` could see enough
    motion.

30. **Terminal text selection works again inside mouse-tracking programs
    (2026-09-06)** — feature §28 forwarded *every* left click to a program
    that set a tracking mode, so in a pane running Claude Code you could no
    longer drag-select or copy any text (the only escape was the
    undiscoverable `Shift`-drag). `terminal_view.py` now defers the
    click-vs-drag decision the way iTerm2 / Windows Terminal do: a left press
    over a tracking program is held (`_pending_press`), then `mouseMoveEvent`
    turns it into a local selection once it travels past
    `QStyleHints.startDragDistance()`, while `mouseReleaseEvent` forwards a
    press that stayed put as a click (press+release emitted as one pair).
    `_forward_click` now also refuses to send a release whose press it never
    forwarded (no stray reports after a `Shift`-drag or a selection dragged
    out of the program). `Shift` still forces an immediate local selection;
    middle/right clicks still forward straight through. New
    `test_mouse_select.py` (7 checks); `test_wheel.py` unchanged and green.

31. **Voice overlay redesigned as a waveform strip (2026-09-06, v0.14.0)** — the old
    mic + seven-rod equaliser capsule now reads as a voice-memo recorder:
    `voice_overlay.py` `_Equalizer → _Waveform` (`self._eq` attr kept), **40
    dense rounded-cap bars mirrored about the centreline** inside a smooth
    taper envelope `_ENV` (low ends, full middle). While `listening` the wave
    swells **centre-out** to the eased mic level with light per-bar wobble;
    `idle` is a slim breathing resting trace; `loading` a soft sweep. The wave
    is **monochrome grey in every state** — the "on air" cue is the strip's
    **red edge + a slow breathing pulse** (`_edge_timer`), not a coloured wave.
    Strip is now a **rounded rectangle** (`_RADIUS = 12`, was a full pill),
    `230x42`, near-black `voice_bg`. `_MicButton` slimmed to 22 px, flat/
    borderless, one faint pulse ring, a filled-red disc + stop square while
    live. New **× dismiss affordance** at the right edge — `_close_rect()` is
    hit-tested in `mousePressEvent` before the drag branch and emits the new
    **`dismiss_requested`** signal (`terminal_panel._build_voice` wires it to
    `_set_overlay_visible(False)`). Caption / partial / model-% behaviour
    unchanged (faint line over a dimmed wave). `theme.py` voice tokens retuned
    (near-black bg + grey wave ramp, Mocha + Latte). Tests:
    `test_voice_overlay.py` 68 (added `_BARS >= 24`, `[6b]` × dismisses without
    dragging); `test_theme.py` / `test_panel.py` green.

32. **Notes panel — functionality + look pass (2026-09-06, v0.14.0)** — the
    plain title/body notebook (feature #20) gained real tools. `notes_store.py`
    `STORE_VERSION 1 → 2`: `Note` grew `pinned` + `color` (`NOTE_COLORS`, "" =
    none) plus `word_count` / `char_count` / `matches()`; `NotesStore` grew
    `search()`, `duplicate()`, and `update(pinned=, color=)` — **a pin/colour
    flag never bumps `updated`**; `_sorted` is now pinned-first then newest.
    **v1 files still load** (fields default). `notes_panel.py`: a search box
    (`Ctrl+F`) filtering the list, an editor action bar **Pin / Copy / Send to
    terminal / Duplicate** (drawn icons via `_draw_icon`), a colour-label
    swatch row (stripe + pin glyph on `_NoteRow`), a `N words · M chars`
    footer, a per-minute relative-time tick, `Ctrl+N` = new. New
    **`send_to_terminal`** signal → `terminal_panel._send_note_to_terminal`
    leaves the Notes view and `insert_text()`s the body at the active pane (no
    Enter). List is re-sorted only on `reload()` / pin toggle — never from
    `flush()` (it runs inside `currentItemChanged`; rebuilding a QListWidget
    from its own signal crashes). Tests: `test_notes_store.py` 47,
    `test_notes_panel.py` 39, both green.

33. **Plugins — GitLab + Linear (2026-09-07, v0.15.0)** — the last two
    `docs/PLUGINS.md` §9 "P5 more providers" cards go live. Both ship an
    **official hosted OAuth-only MCP server** (no bearer token, no local
    binary), so both are exact clones of the Vercel/Jira thin-plugin pattern
    (§12/§13 of PLUGINS.md): AgentDeck only drops a *tokenless* `{"type":"http",
    "url":…,"x-agentdeck-managed":true}` entry into each MCP-capable agent's
    user-scope config; the agent runs the OAuth itself (`/mcp` for Claude,
    auto-DCR for opencode, `oauth_hint()`'s per-agent command for the rest).
    **Same change widened `mcp_targets.OAUTH_ALLOWLIST` to ALL 11 agents**
    (`copilot`'s `oauth=False` guard dropped) — so Vercel/Jira/GitLab/Linear now
    also wire codex, gemini, qwen, cursor-agent, amp, antigravity, crush, goose,
    copilot. Only Claude + opencode are verified end-to-end; the rest ride on
    documented remote-MCP OAuth support and fail inert (dead entry, removed on
    disconnect). `aider` stays out (no MCP support, absent from `_TARGETS`).
    - **GitLab** — `gitlab_mcp.py` / `gitlab_controller.py` (`GitLabController`),
      server `gitlab`, URL `https://gitlab.com/api/v4/mcp`. gitlab.com only in
      v1 (self-hosted would need a per-connection URL field — override
      `REMOTE_MCP_URL` meanwhile).
    - **Linear** — `linear_mcp.py` / `linear_controller.py` (`LinearController`),
      server `linear`, URL `https://mcp.linear.app/mcp` (read-write;
      `/mcp/readonly` is a future toggle).
    - `plugin_store.py` +`GITLAB`/`LINEAR` constants (thin, no capability model);
      `plugins_panel.py` +`_gitlab_icon`/`_linear_icon` (drawn), `_GitLabDetail`/
      `_LinearDetail` (copies of `_JiraDetail`), catalog tuples → live, stack
      pages **4** (gitlab) / **5** (linear), `PluginsPanel(gitlab=, linear=)`
      kwargs; `terminal_panel.py` builds both controllers, `_wire_gitlab_for` /
      `_wire_linear_for` (called from `_add_workspace` / `_do_handoff` / routine
      launch), `_on_{gitlab,linear}_{connected,disconnected}` status nudges,
      teardown `unwire_all()`+`shutdown()`.
    - **No Supabase migration** (`plugin_connections` is provider-generic), **no
      `entitlements` change** (reuses `plugins_enabled`, Pro gate).
    - Tests: `test_gitlab_mcp.py` / `test_linear_mcp.py` (43 each),
      `test_gitlab_controller.py` / `test_linear_controller.py` (17 each),
      `test_plugin_store.py` §8/§9 (49 total), `test_plugins_panel.py` §7/§8
      (106 total). All green; `test_panel.py` unchanged bar the lone
      pre-existing offscreen "drop focus" flake.

34. **Routines actually send the prompt now (2026-09-07)** — a fired routine
    opened the agent but the prompt just sat unsent in the composer.
    `_run_routine` typed the prompt in with `insert_text` then `submit()`
    back-to-back; the lone `\r` lands inside the bracketed paste the TUI is
    still ingesting (Claude Code / Codex render-loop race) and never submits.
    - **Primary fix:** hand the prompt to the agent on its command line via
      the existing `agent_sessions.initial_prompt_command` (same path the
      handoff uses) — `claude "…"`, `codex "…"`, `opencode --prompt "…"`. The
      agent boots straight into the task; nothing to type. Guarded to ≤6000
      chars so it can't overflow a Windows command line.
    - **Fallback** (aider/goose/copilot/amp/crush/antigravity/cursor-agent/
      custom — no initial-prompt arg): new `TerminalView.insert_and_submit()`
      types the text, then fires `\r` after a 600 ms beat so the paste is
      committed first.
    - `RoutinesPanel` gained a **"Run now"** button (`run_now` signal →
      `TerminalPanel._run_routine_now`) so a routine can be tested off-schedule.
    - **Name the new workspace:** `Routine.new_workspace_name` (new store field,
      editable) — a text box under the Workspace combo, shown only when the
      target is "New workspace". `_run_routine` passes it as
      `_add_workspace(name=…)`; the list row shows `New: <name>`. Empty = the
      old auto "Workspace N".
    - Tests: `test_panel.py` §31/§31b (baked-command + paced-Enter paths),
      `test_routines_panel.py` §6b (Run now) / §2b (workspace name),
      `test_routines_store.py` §3 (field round-trip). `terminal_panel.py` half
      of the send fix was swept into commit `9c06409` by a concurrent session's
      `commit --amend`; the rest is its own commit(s).

35. **Settings ▸ Appearance — terminal font picker + named colour schemes
    (2026-09-07)**
    - **Terminal font:** new `config["font_family"]` ("" = automatic). A
      dropdown of installed *fixed-pitch* families (Settings ▸ Appearance),
      plus "Automatic (best installed)". `terminal_view` grew module state
      `_font_family` + `set_font_family()` / `active_font_family()` /
      `available_monospace_families()`; `preferred_font()` honours it.
      `TerminalPanel.__init__` seeds it from config before the first pane;
      `_on_settings_font_family_changed` sets it + `Workspace.reapply_font()`
      → `TerminalPane.reapply_font()` → `TerminalView.reapply_font()` (re-runs
      `canvas.set_font(preferred_font(size))`). A pane built later picks it up
      for free. A saved font that isn't installed still shows in the dropdown,
      flagged "(not installed)".
    - **Colour scheme:** new `config["color_scheme"]` (default `catppuccin`).
      `theme.py` keeps Catppuccin as the hand-authored base; other schemes
      (Dracula, Nord, Tokyo Night, Gruvbox, Rosé Pine, Kanagawa, One Dark,
      Synthwave) are a compact ~20-value spec run
      through `theme._expand()` onto the full token table. `_SCHEMES` +
      `scheme()` / `set_scheme()` / `scheme_labels()` / `scheme_is_dark_only()`
      / `DEFAULT_SCHEME`. `color()` / `ansi()` consult the active scheme, then
      fall back to the Catppuccin table for the mode. Rosé Pine ships a Dawn
      light variant; Kanagawa / One Dark / Synthwave and the earlier
      Dracula/Nord/Tokyo Night are dark-only and show their dark palette in Light mode
      (the picker says so). `set_scheme()` fires `theme.manager().changed`, so
      the existing `_on_theme_changed` fan-out repaints everything incl. the
      terminal (`vt_screen.Palette` already reads `term_*` + `ansi()` from
      `theme`). Settings ▸ Appearance dropdown → `scheme_changed` signal →
      `TerminalPanel._on_settings_scheme_changed`.
    - `_on_theme_changed` no longer clobbers `config["theme"]` when it's
      `"system"` (latent bug); the toolbar toggle now pins the concrete mode
      itself (`_toggle_theme`).
    - Both keys are in `account.CLOUD_KEYS` (sync with `theme`/`font_size`) and
      `config.CONFIG_SCHEMA` (+ `color_scheme` in `CONFIG_CHOICES`, kept in
      sync with `theme._SCHEMES` by hand).
    - Tests: `test_theme.py` §8, `test_settings_dialog.py` §6b/§6c.

36. **Skills — reusable SKILL.md instructions, wired into every agent
    (2026-09-07, v0.16.0, branch `feat/skills`)** — a new sidebar nav view
    (below "Routines"). A skill is a saved `{name, description, body}` Markdown
    doc; enabled skills are *materialized* where each agent discovers
    instructions, and an agent can review + rewrite one. **Pro** (same tier as
    Routines / Plugins / Handoff). Full design: `docs/SKILLS.md`.
    - `skills_store.py` — Qt-free, same rules as `notes_store` /
      `routines_store`: one atomic JSON file `%APPDATA%\multi-terminal\skills.json`
      (body inline — the JSON is the source of truth), tolerant load, `Skill`
      dataclass. `slug` derived from `name` once at creation, never changes (it
      is a dir/file name). `parse_frontmatter` (a tiny `key: value` YAML subset,
      no dep) / `render_skill_markdown`. `import_markdown` (frontmatter → first
      `#` heading → fallback name). `merge_cloud(rows)` / `cloud_rows()` = LWW by
      slug for the sync layer.
    - `skills_sync.py` — Qt-free materializer. **Claude**: one dir per skill at
      `~/.claude/skills/<slug>/SKILL.md` + a `.agentdeck-managed` marker (a dir
      without the marker is the user's own — never touched). **Everyone else**:
      `<folder>/.agentdeck/skills/<slug>.md` + a marker-delimited
      `<!-- agentdeck:skills:start -->…end -->` block in `<folder>/AGENTS.md`
      (created if absent; `skills_materialize_agents_md` config opt-out). Working
      copies at `%APPDATA%\multi-terminal\skills\<slug>.md`. Ledger
      `skills_state.json` records what we wrote so disable/delete/plan-lapse
      (`remove_all()`) undoes exactly that. Env overrides `ADK_AGENT_HOME_DIR` /
      `ADK_SKILLS_DIR` / `ADK_SKILLS_STATE` sandbox every path for tests.
    - `skills_panel.py` — `SkillsPanel` + `skill_icon`. List (New / Upload
      SKILL.md) ∥ editor (name / description / monospace body / enabled) + an
      action bar: **Improve with agent** (agent picker, remembers
      `skills_improve_agent`), **Open file**, **Export…**, **Delete**. Debounced
      autosave, `flush()` on nav-away — mirrors `RoutinesPanel`. Signals
      `changed` / `count_changed` / `improve_requested`.
    - `skills_cloud.py` — `SkillsCloud` (Qt): mirrors the library to
      `public.skills` (RLS `user_id = auth.uid()`). Pull on launch / when the
      plan resolves to Pro; debounced push on every change; soft-delete
      tombstones. Gated on `account_cloud_sync` + `cloud_sync_enabled` (Pro).
      Migration `supabase/migrations/20260908120000_skills.sql`.
    - `terminal_panel.py` — `_skills_store` / `_skills_panel` / `_skills_cloud`
      in `_main_stack`; `_show_skills` / `_leave_skills` / `_skills_active`
      folded into every nav-view switch + `_refresh_sidebar`. `_materialize_skills`
      runs on `_apply_entitlements` (plan resolve) and on `_skills_panel.changed`
      — no-op / `remove_all()` when not Pro. `_improve_skill_with_agent` bakes a
      review prompt (via `agent_sessions.initial_prompt_command`), spawns a pane,
      and registers a watch keyed on a content hash; the 1 s `_refresh_status`
      watchdog (`_check_skill_watches`) re-imports the agent's edits (source →
      `agent`, `last_reviewed_*`), re-materializes, and pushes to cloud.
    - `entitlements.skills_enabled(plan)` = `is_pro`.
    - Tests: `test_skills_store.py` (52), `test_skills_sync.py` (33),
      `test_skills_panel.py` (24), `test_entitlements.py` (+skills gate),
      `test_panel.py` §32 (Pro gate + improve-with-agent pane spawn + watch
      re-import). Full offline suite green; `test_panel.py` green bar the lone
      pre-existing offscreen "drop focus" flake.

37. **Isolated git worktree per pane + Review/Merge panel (v0.17.0, 2026-09-08,
    `feat/isolated-worktrees` → `main`)** — a workspace can now run each of its panes in
    its own `git worktree` on a scratch branch, so several agents work the same
    repo in parallel without colliding, and there's one place to triage what
    they produced. Solves the "parallel agents step on each other" problem the
    whole Conductor / Vibe-Kanban category exists for. Open-source: needs only
    `git`.
    - **New modules:** `git_worktree.py` (Qt-free `git` subprocess wrapper —
      `detect_repo` / `add_worktree` / `status` / `diff_stat` / `diff_text` /
      `merge_to_base` / `push_branch`; every call passes `CREATE_NO_WINDOW` +
      `GIT_TERMINAL_PROMPT=0`), `worktree_store.py` (Qt-free JSON store at
      `%APPDATA%\multi-terminal\worktrees.json`, mirrors `routines_store`;
      scratch trees under `%LOCALAPPDATA%\multi-terminal\worktrees\<key>\`,
      machine-local), `worktree_panel.py` (`WorktreePanel` — list ∥ file list ∥
      coloured read-only diff + Merge / Open PR / Open in pane / Discard;
      mutations go out as signals, `TerminalPanel` does them).
    - **New-workspace dialog:** "Isolate each terminal in its own git worktree"
      checkbox, enabled only when the folder is a git repo *and* the plan is Pro
      (`entitlements.worktrees_enabled`). Last state remembered in
      `config["worktree_isolate_default"]`.
    - **Panes:** `TerminalPane.pane_id` (stable across relayout reparents — the
      store keys off it, not the shifting list index); `Workspace.initialize(
      count, pane_cwds=)` + per-pane `cwd`; a `pane_close_requested` signal that
      `Workspace._intercept_pane_close` routes to the panel so it can prompt
      **Merge / Keep / Discard** for the pane's worktree before the shell dies
      and the cwd lock releases.
    - **Merge never touches the user's checkout:** `merge_to_base` merges in an
      ephemeral `--detach` worktree, then advances `refs/heads/<base>` with a
      compare-and-swap `update-ref`. The main checkout just shows "behind" —
      clean, expected.
    - **Crash recovery:** `_reconcile_worktrees_on_startup` → `store.reconcile()`
      marks vanished worktrees `orphaned`, adopts stray `agentdeck/*` trees,
      and sweeps `pending_delete` rows (a dir git had locked at close time).
    - **Open PR** reuses the GitHub plugin — `github_api.create_pull_request` /
      `find_pr_for_branch` + `github_controller.create_pull_request` (new,
      blocking). Disabled unless GitHub is connected and `origin` is a GitHub
      remote.
    - Sidebar gets a 4th nav item ("Worktrees", below Routines).
    - Tests: `test_git_worktree.py` (real temp repo — add/status/diff/merge/
      conflict/remove), `test_worktree_store.py` (CRUD + reconcile),
      `test_worktree_panel.py` (`offscreen` — rows, diff, signals, empty
      states); `test_entitlements.py` + `test_new_workspace_dialog.py` extended.
    - **Not done here:** handoff / routines don't create worktrees yet (v2); no
      one-click "base moved — rebase first" in the panel (badge shows, merge
      still works via a real merge commit); diff render for a huge repo is
      byte-capped, not threaded.
    - **Rebased onto `main` @ `b0acf22` for the v0.17.0 ship** — the only
      adaptation was the Worktrees nav button: `b0acf22` changed `_nav_button`
      to take an icon *factory*, so `worktree_icon(16)` → `worktree_icon`.
      Full offline suite + `test_panel.py` (160 checks) green;
      `test_panel_account.py` "signed-out controller" fails on `main` too
      (pre-existing, unrelated).

## Running / testing

```cmd
cd E:\Workspace\V4\windows_launcher
.venv\Scripts\python.exe main.py            # run the app
.venv\Scripts\python.exe test_panel.py      # real window + real shells; ALL PASS
.venv\Scripts\python.exe test_vt_screen.py  # screen model; ALL PASS
.venv\Scripts\python.exe test_wheel.py         # wheel routing; offline
.venv\Scripts\python.exe test_mouse_select.py  # click-vs-drag selection; offline
.venv\Scripts\python.exe test_voice_engine.py   # voice pipeline, stubbed; offline
.venv\Scripts\python.exe test_voice_overlay.py  # voice widget; offline
.venv\Scripts\python.exe test_voice_models.py       # model pick + resolve; offline
.venv\Scripts\python.exe test_voice_postprocess.py  # utterance clean-up; offline
.venv\Scripts\python.exe test_voice_download.py     # model download ctrl; offline
.venv\Scripts\python.exe test_global_hotkey.py      # OS hotkey parse+filter; offline
.venv\Scripts\python.exe test_voice_commands.py     # spoken-command parser; offline
.venv\Scripts\python.exe test_agents.py         # agent discovery; offline
.venv\Scripts\python.exe test_setup_wizard.py   # wizard pages/validation; offline
.venv\Scripts\python.exe test_new_workspace_dialog.py  # new-workspace agent dialog; offline
.venv\Scripts\python.exe test_agentdeck_splash.py      # launch splash; offline
.venv\Scripts\python.exe test_plugins_panel.py         # sidebar nav + plugins panel; offline
.venv\Scripts\python.exe test_plugin_store.py          # plugins.json + provider constants; offline
.venv\Scripts\python.exe test_gitlab_mcp.py            # GitLab MCP injector; offline
.venv\Scripts\python.exe test_linear_mcp.py            # Linear MCP injector; offline
.venv\Scripts\python.exe test_gitlab_controller.py     # GitLab Qt bridge; offline
.venv\Scripts\python.exe test_linear_controller.py     # Linear Qt bridge; offline
.venv\Scripts\python.exe test_notes_store.py           # notebook JSON store; offline
.venv\Scripts\python.exe test_notes_panel.py           # notes panel + sidebar nav; offline
.venv\Scripts\python.exe test_skills_store.py          # skills JSON store + frontmatter; offline
.venv\Scripts\python.exe test_skills_sync.py           # skills materialize/prune/ledger; offline
.venv\Scripts\python.exe test_skills_panel.py          # skills panel + sidebar nav; offline
.venv\Scripts\python.exe test_theme.py                 # light/dark theme + toggle; offline
.venv\Scripts\python.exe test_update_progress.py       # animated update download/install dialog; offline
.venv\Scripts\python.exe test_settings_dialog.py       # Settings dialog + Updates section; offline
.venv\Scripts\python.exe test_agent_sessions.py        # conversation-handoff session readers; offline
.venv\Scripts\python.exe test_handoff_dialog.py        # handoff target/mode dialog; offline
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
  pywhispercpp, `collect_submodules('voice_capture')`, `hiddenimports` names the
  launcher's lazily-reached modules incl. `voice_models`/`voice_postprocess`,
  big Qt `excludes`),
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
