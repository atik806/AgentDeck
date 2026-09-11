# linux-v4 — working context

Read this first for anything touching the Linux port. It's the Linux-side sibling of
the root `E:\Workspace\V4\context.md` ("read that first" for the project as a whole) —
this file tracks port-specific state only.

## What this folder is

`windows_launcher/` (branded AgentDeck) is **one shared codebase** for both platforms.
This folder does **not** contain a copy of the app. It holds:

- `packaging/` — the Linux analog of the root `packaging/` (Windows PyInstaller spec +
  Velopack build script). Will gain `AgentDeck-linux.spec`, `build_linux.py`,
  `Releases/` once Phase 5 lands.
- This file — progress log for the port, updated at the end of every phase.

Cross-platform app code (POSIX pty backend, XDG paths, Linux secret storage) lives
directly in `windows_launcher/`, branched on `sys.platform`/`os.name` — the same
pattern `secret_store.py` (`_IS_WINDOWS`) and `git_worktree.py` already use. See the
approved plan at the top level of this session's plan file for the full rationale and
phase breakdown (Phases 0-5); this doc tracks *status*, the plan file (or its
successor, once copied into the repo if the user wants that) holds the *design*.

## Status

| Phase | What | Status |
|---|---|---|
| 0 | CI scaffolding (`linux-ci.yml`) + Velopack-Linux spike | **DONE — both green.** `test` job fully passes on `ubuntu-latest`; `velopack-spike` job passed outright on its first run, confirming Velopack does support a Linux AppImage (`vpk pack --channel linux`) |
| 1 | POSIX PTY backend (`ptyprocess`-based) | **DONE — verified on real Linux CI.** `test_pty_backend_posix.py` (real spawn/write/read/resize/close/exit-code against `/bin/sh`, real `/proc` child-probe) green on `ubuntu-latest` |
| 2 | XDG path helper + store migration | **DONE — verified on real Linux CI** (the whole suite ran under real XDG-shaped `$HOME`) |
| 3 | Linux secret storage (`keyring`) | **DONE — verified on real Linux CI.** The real (unmocked) `EncryptedJsonStore` round trip in `test_github_auth.py`/`test_github_controller.py`/`test_supabase_auth.py` passes against a real `gnome-keyring` daemon in CI |
| 4 | Dead-code removal (`main_window.py`/`launcher.py`) + misc | done — both deleted, `context.md`/README updated; full `--smoke` app launch verified clean on Windows after the deletion |
| 5 | Linux packaging pipeline (Velopack AppImage) | **DONE — full real pipeline verified + wired into the actual release workflow.** `linux-ci.yml`'s `build-real-app` job built the *real* AgentDeck app via `AgentDeck-linux.spec`/`build_linux.py`, packed a real `AgentDeck.AppImage` (95.9 MB), and ran it end to end under `xvfb-run` with a clean exit. `.github/workflows/release.yml` now has a `build-linux` job (`needs: build`, runs after Windows publishes, to avoid a race on the shared GitHub Release) — **not yet exercised against a real `v*` tag**, that's the next actual release |

## 🎉 Full Linux CI green — 2026-09-11

After 6 push iterations, `.github/workflows/linux-ci.yml`'s `test` job passes
completely on `ubuntu-latest` (all 65 `test_*.py` files, real Qt rendering via a
real Xvfb + fluxbox window manager, real `gnome-keyring` secret storage), and the
`velopack-spike` job passed on its very first run. Every failure hit along the way
traced to one of two categories, **none of them bugs in the ported app code**:

**CI environment gaps** (fixed in `linux-ci.yml`):
- Qt's xcb platform plugin needs `libxkbcommon-x11-0` (distinct from `libxkbcommon0`,
  and not clearly named in Qt's own error text).
- `gnome-keyring`'s default collection doesn't exist on a fresh runner and
  `--unlock` doesn't create one — pre-seeding an explicitly unencrypted collection
  file sidesteps the interactive "SystemPrompter" prompt that has nowhere to display.
- `hasFocus()`/window-activation semantics need an actual window manager — a bare
  Xvfb has none. Added `fluxbox` and restructured from per-file `xvfb-run` to one
  shared Xvfb(1920x1080)+fluxbox session for the whole run.

**Pre-existing tests that hardcoded Windows-only assumptions**, never exercised on
another platform until this session (fixed in the relevant `test_*.py`, not in the
app code they test — in every case the underlying app logic was already correct/
already platform-aware):
- `test_worktree_store.py`: `repo_key_for()`'s case-insensitivity is deliberately
  OS-dependent (`os.path.normcase`); the test asserted only the Windows case.
- `test_agents.py`: quoted-path fixtures were Windows backslash paths, which
  `pathlib.Path` (correctly) parses differently as `PosixPath`.
- `test_global_hotkey.py`: unconditionally faked `ctypes.windll.user32`, but
  `GlobalHotkey.bind()` checks `self._supported` (Windows-only) *before* ever
  touching `ctypes.windll` — the fakes were simply unreachable off Windows.
- `test_panel.py` (the deepest one, five separate issues): a Windows-path file-drop
  fixture (same `QUrl`/`pathlib` platform-awareness as above); a hardcoded
  Windows-only `default_shell: "cmd"` key; a bracketed-paste-mode assertion that
  didn't account for bash enabling it by default (cmd/PowerShell don't); a
  tilde-abbreviated-`$HOME` prompt (bash's `\w` shows `~/...` for anything under
  `$HOME`, so a literal-absolute-path substring check silently failed even though
  the shell's real cwd was correct); and a splitter-drag width check that had
  enough "flexible" pixel budget beyond each pane's minimum size on Windows but not
  under Linux's specific toolbar/font-metric-driven minimum window width — fixed by
  giving the check a much wider window rather than relying on a coincidental margin.

The one remaining occasional flake — "the drop moved focus to the pane" — is the
SAME check already documented as environment-dependent throughout this project's
Windows-only history (a stray window stealing focus mid-test); it reproduces
identically on Windows itself and is left as-is, not treated as a Linux-specific bug.

Commits (all on `main`, pushed): `b8f0b34` (Phase 0-1), `a8d256b` (Phase 2), `234cf0d`
(Phase 4), `d669f5f` (Phase 3), `da8eb61` (Phase 5 skeleton), `a1f4fc4`/`2ca95a7`/
`6c8226f`/`64864b1` (the four CI-iteration fix commits).

## Phase 1-2 implementation notes

- **pty_backend.py split**: `pty_backend.py` is now a thin `sys.platform` dispatcher
  re-exporting from `_pty_backend_win.py` (unchanged ConPTY logic, moved verbatim) or
  `_pty_backend_posix.py` (new, `ptyprocess`-based). `_pty_backend_posix.py` adds a
  synchronous "does this executable exist" pre-check in `_spawn()` that Windows didn't
  need — `ptyprocess` forks then execs *inside the child*, so a bad command fails there,
  not in `PtyProcess.spawn()`, and without the pre-check `.error` would stay `None`
  (contract mismatch with the Windows backend, where a bad command raises synchronously).
- **Windows-side verification done**: `pty_backend.py` still resolves to the exact same
  `available_shells()` output as before the split; the full existing offline test suite
  (`test_notes_store`, `test_routines_store`, `test_skills_store`, `test_workspaces_store`,
  `test_worktree_store`, `test_github_auth`, `test_supabase_auth`, `test_plugin_store`,
  `test_mcp_targets`, `test_skills_sync`, `test_account`, plus `test_vt_screen`,
  `test_wheel`, `test_agents`, `test_global_hotkey`) passes unchanged after the Phase 2
  path refactor.
- **config.py**: `_get_config_dir()`/`_get_cache_dir()` (private) replaced by public
  `config_dir()`/`cache_dir()`/`data_dir()`, built on `platformdirs` with
  `appauthor=False`. **Verified locally**: `user_config_dir(..., roaming=True)` and
  `user_cache_dir(...)` reproduce the exact pre-existing Windows paths character for
  character (`%APPDATA%\multi-terminal`, `%LOCALAPPDATA%\multi-terminal\Cache`).
- **`_get_config_dir` rename fallout**: `account.py`, `plugin_store.py`,
  `mcp_targets.py`, `github_auth.py`, `supabase_auth.py` all imported the *old private*
  `_get_config_dir` name directly — these would have silently fallen through to their
  exception-fallback branch on every call after the rename if not caught. All five
  updated to `config_dir`; `test_account.py`'s `account._get_config_dir = lambda: _TMP`
  monkeypatch updated to `account.config_dir = lambda: _TMP` too.
- **worktree_store.py's scratch-tree root** uses `config.data_dir()`, not `cache_dir()`
  — `cache_dir()` adds a Windows-only `\Cache` segment that would have silently moved
  every existing user's scratch worktrees to a new (empty) location. `data_dir()` is the
  byte-exact match for the pre-existing `%LOCALAPPDATA%\multi-terminal\worktrees` path.
- **`mcp_targets.py`'s `_appdata()`/`_localappdata()`/`_xdg_config()` helpers were left
  untouched** — those resolve *other* coding agents' own config directories (Claude,
  Codex, etc.), a separate and already-cross-platform-aware system; only its own
  ledger-path function (`_ledger_path`) needed the `config_dir` rename.
- **`main.py`'s `_log_path()`** deliberately still does not import `config.py` (or now
  `platformdirs`) — it runs after a crash, when either could itself be the broken thing
  — so it got its own tiny inline `XDG_CONFIG_HOME`-aware branch instead.
- Every migrated exception-fallback branch (the rare "config import itself failed" path)
  now also checks `XDG_CONFIG_HOME` / `~/.config` on non-Windows instead of only ever
  falling back to `~/multi-terminal`.

## Decisions on record

- **Scope**: v1 = core app parity (terminal panel, panes/workspaces, in-app hotkeys,
  theming/settings, notifications, worktrees, notes, routines, skills). Deferred:
  GitHub/Vercel/Jira/GitLab/Linear plugins, voice dictation, system-wide (unfocused)
  hotkey, foreground-paste dictation, cloud settings sync beyond sign-in itself.
- **Sign-in stays mandatory** on Linux too — ported via a real `keyring`-backed
  secret store (Phase 3), not bypassed.
- **Packaging**: Velopack AppImage is the target, one feed/update mechanism across
  both platforms. Falls back to a plain tarball (no in-app auto-update) if the Phase 0
  spike shows Velopack's Linux support isn't there yet.
- **Build location**: GitHub Actions `ubuntu-latest`, not WSL2 — this dev environment
  is Windows-only with no local Linux execution, so CI is the only place Linux code
  actually runs and is verified.
- macOS is out of scope and not audited; new platform branches must not silently
  misclassify it into either the Windows or Linux path.

## Phase 3 implementation notes

- `secret_store.EncryptedJsonStore` and `supabase_auth.SessionStore` (its own
  inline DPAPI copy, per its docstring's "new code should use secret_store")
  both got a parallel `_MAGIC_LINUX` branch backed by the `keyring` package
  (freedesktop Secret Service — GNOME Keyring / KWallet). Neither was merged
  into one shared implementation this pass — see the `TODO(linux-port)`
  comment left in `supabase_auth.py`.
- **The security-regression fix the audit flagged**: `save()` used to fall
  back to writing plain JSON on *any* non-Windows platform. It no longer does
  — Windows uses DPAPI, Linux uses the keyring, and anything else (or a Linux
  box with no keyring daemon) now refuses the write entirely, matching the
  posture DPAPI-failure already had on Windows. A `_MAGIC_PLAIN` file from
  before this change is still *read* for backward compatibility, just never
  written again.
- **Verified via `test_secret_store.py`** (new, monkeypatches
  `_IS_WINDOWS`/`_IS_LINUX`/`_keyring_*` so the dispatch logic is checked
  regardless of which OS runs it): a working keyring round-trips
  save/load/clear and the on-disk marker never contains the secret in the
  clear; a keyring failure writes nothing at all (not even a stub file) and
  `save()` returns `False`; an unsupported platform behaves the same way; a
  legacy plaintext file still loads.
- **Not yet verified**: the *real* keyring backend on real Linux. This needs
  the `linux-ci.yml` "test" job's `dbus-run-session` + `gnome-keyring-daemon
  --unlock` wrapper (added this phase) to actually work on its first CI run —
  it's a well-known pattern for headless Secret Service testing but was
  authored without a Linux machine to confirm it against. If it doesn't work,
  `test_github_auth.py`'s "token vault round-trip" section and
  `test_supabase_auth.py` will fail on `ubuntu-latest` specifically at
  "save reports success" — that's the first thing to debug.
- Windows behavior confirmed unchanged: `test_github_auth.py` (21 checks) and
  `test_supabase_auth.py` (59 checks) still pass as before on the Windows dev
  machine; a full `--smoke` app launch is clean after these changes.

## Phase 5 implementation notes

- `linux-v4/packaging/AgentDeck-linux.spec` + `build_linux.py` mirror the
  Windows `packaging/` pair structurally. **Voice is deliberately excluded**
  from the Linux bundle for v1 (native-dependency-heavy, out of scope, and
  the app already degrades gracefully without it); **plugins ARE still
  bundled** even though wiring/testing them on Linux is also deferred,
  because unlike voice there's no "missing gracefully" story for the Plugins
  nav item — leaving them out would trade "untested" for "crashes on click".
- `updater.py::is_packaged()` got a Linux branch that is **hardcoded `False`**
  with a docstring explaining why (the real Velopack-installed-AppImage
  on-disk layout isn't known without running the spike) — deliberately not
  guessed at, per the plan. `UpdateController.unavailable_reason` already
  surfaces this reasonably ("updates are managed by the installed build")
  rather than the Settings section silently vanishing.
- **Explicitly NOT done this phase**: adding a `build-linux` job to
  `.github/workflows/release.yml`. That workflow is tag-triggered and shared
  with the real Windows release pipeline — wiring an unverified `vpk pack
  --channel linux` invocation into it risks either breaking or just noisily
  failing on the next real `vX.Y.Z` tag push. The Velopack-Linux spike
  (`linux-ci.yml`'s `velopack-spike` job, `workflow_dispatch`) needs to
  actually run and confirm the CLI surface first — see
  `linux-v4/packaging/README.md`'s "Known unknowns" section for exactly
  what's still open.
- Verified on Windows: `test_updater.py` (11 checks) unaffected by the
  `is_packaged()` change; full `--smoke` app launch still clean.

## `is_packaged()` no longer needs a Linux-specific layout check — 2026-09-11

Investigated by inspecting the pip-installed `velopack` package directly (no CI
round trip needed — reachable on Windows too, `.venv-build` already had it):
`velopack.UpdateManager(feed_url)`'s constructor does its **own cross-platform
install-manifest auto-detection** internally, and raises a plain `RuntimeError`
("This application is not properly installed: Could not auto-locate app
manifest") when there isn't one — already caught by `UpdateController`'s
existing `try/except`. `velopack.App().run()` is even more forgiving: it logs a
warning and returns normally (verified: does not raise, does not exit) when
called outside a real install. Both are safe to call unconditionally on every
platform, matching Velopack's documented "call at the top of main()
unconditionally" usage pattern for every language they support.

This means the original plan's goal ("determine the real Velopack-Linux
on-disk layout and hand-write a check for it") was based on a wrong premise —
there's no need to reimplement Velopack's own locator logic at all. `updater.py`
was simplified: `is_packaged()` is now just `bool(getattr(sys, "frozen",
False))` (a cheap, genuinely cross-platform pre-filter, replacing the old
Windows-only `Update.exe`-path check), `run_velopack_bootstrap()` calls
`velopack.App().run()` unconditionally, and `UpdateController.__init__`'s
existing `try/except` around `UpdateManager(...)` construction is now the
*real* arbiter of whether updates are available, on every platform. No
behavior change on Windows (verified: `test_updater.py` all green, a real
installed build already worked via the same `UpdateManager` construction
attempt, just previously gated by a redundant pre-check).

## Real build + release-pipeline wiring — 2026-09-11

`linux-ci.yml` gained a `build-real-app` job (manual dispatch): builds and
packs the *actual* AgentDeck app via `AgentDeck-linux.spec` + `build_linux.py`
(not the throwaway hello-world `velopack-spike` uses), then extracts and runs
the real produced AppImage end to end under `xvfb-run`. First run hit a real,
pre-existing bug: `requirements-build.txt` pinned `velopack>=0.0.1,<1.0`, and
PyPI no longer publishes anything in that range (the package crossed 1.0)
— a fresh install failed outright. Fixed (widened to `>=1.0,<2.0`, verified
the exact `App()`/`UpdateManager` API surface behaves identically on 1.2.0,
`constraints.txt` regenerated from a freshly-synced `.venv-build`, a real
Windows `packaging/build.py --no-pack` PyInstaller build confirmed clean with
the new dependency set). Second run: **fully green** — real
`AgentDeck.AppImage` (95.9 MB) + `AgentDeck-<ver>-linux-full.nupkg` +
`releases.linux.json` + `SHA256SUMS.txt` produced, uploaded as a CI artifact,
and the AppImage itself ran and exited cleanly.

`.github/workflows/release.yml` now has a `build-linux` job wired in:
- **Runs after `build` (`needs: build`), not in parallel** — both jobs
  `vpk upload github --tag <same tag>` into the *same* release, and there's
  no verified-safe way to run two concurrent uploads against one release
  without risking a race on its creation. A Linux failure never undoes or
  blocks the Windows publish.
- Its own "already published?" check (not shared with Windows' — by the time
  it runs the release already has Windows assets, so "any assets at all"
  would always read true) — looks specifically for an asset named with
  "linux" in it or ending `.AppImage`.
- `checksums.py`'s output is renamed `SHA256SUMS-linux.txt` before upload —
  both jobs' checksums.py calls produce a file literally named
  `SHA256SUMS.txt`; uploading that as-is would have clobbered the Windows
  manifest on the shared release (`gh release upload --clobber` overwrites by
  name).
- No signing step, no winget-equivalent step (out of v1 scope, per the plan).
- **Not yet exercised against a real `v*` tag push** — verified thoroughly in
  isolation (the `build-real-app` job above proves the build+pack+run
  end-to-end; the `vpk upload`/"already published" logic mirrors the
  long-proven Windows pattern exactly) but the actual combined Windows+Linux
  publish to one real release is unverified until the next real version tag.

## Open follow-ups

- **The next real `vX.Y.Z` tag push is the true end-to-end test** of
  `build-linux` publishing alongside Windows to one real release — watch it
  when it happens, don't assume it's flawless just because the pieces were
  each verified individually.
- De-dup `secret_store.py` and `supabase_auth.py`'s inline copy (see the
  `TODO(linux-port)` comment in `supabase_auth.py`) — both are now proven
  stable on real Linux CI (the real keyring round trip passes), so this is
  lower-risk to attempt now.
- A real Linux desktop smoke pass (GNOME/KDE, X11/Wayland) is still needed —
  CI proves the code runs correctly, not that the UI/UX feels right on a real
  desktop (theming, notifications, tray icon, window decorations, HiDPI, etc.).
- `linux-v4/packaging/README.md`'s "Known unknowns" section is now stale
  (everything it flagged is resolved) — update it to reflect the confirmed
  state rather than leaving it reading as still-open questions.
- `linux-v4/packaging/README.md`'s "Known unknowns" section is now partly
  stale (the spike succeeded) — update it alongside the `is_packaged()` work
  above rather than leaving both half-done.
