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
    stay amber (front-door vs in-app split); `voice_overlay.py` keeps its own
    dark HUD palette (most visible against Latte — top follow-up candidate).
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
    - Tests: `test_agent_sessions.py` (new, 63 — incl. `after=` watermark,
      empty→None, working-folder doc store), `test_handoff_dialog.py` (new, 17),
      `test_entitlements.py` [+1], `test_panel.py` §30 (Pro gate + resume with
      watermark + transcript pane spawn). Same lone pre-existing offscreen "drop
      focus" flake in `test_panel.py`.
      Manual smoke still pending (needs sign-in): claude→claude fork with two
      concurrent claude windows, claude→(installed target) transcript,
      opencode→opencode fork, plain-shell source, Free-plan upsell.

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
.venv\Scripts\python.exe test_notes_store.py           # notebook JSON store; offline
.venv\Scripts\python.exe test_notes_panel.py           # notes panel + sidebar nav; offline
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
