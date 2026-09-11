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
| 0 | CI scaffolding (`linux-ci.yml`) + Velopack-Linux spike | workflow written; spike not yet run (needs a push + manual dispatch) |
| 1 | POSIX PTY backend (`ptyprocess`-based) | code + tests written; verified importable/dispatching correctly on Windows; **not yet run on real Linux/CI** |
| 2 | XDG path helper + store migration | done, verified locally on Windows (every migrated path is byte-identical to the pre-refactor path); Linux XDG paths unverified until CI runs |
| 3 | Linux secret storage (`keyring`) | done — `secret_store.py` + `supabase_auth.py`'s inline copy both branch to a `keyring`-backed store on Linux; dispatch logic verified via mock in `test_secret_store.py`; real-keyring round trip via the existing `test_github_auth.py`/`test_supabase_auth.py` checks is **not yet run on real Linux CI** |
| 4 | Dead-code removal (`main_window.py`/`launcher.py`) + misc | done — both deleted, `context.md`/README updated; full `--smoke` app launch verified clean on Windows after the deletion |
| 5 | Linux packaging pipeline (Velopack AppImage or tarball fallback) | spec + build script written (`linux-v4/packaging/`); **NOT added to `.github/workflows/release.yml`** — waiting on a real Velopack-Linux spike run before wiring a tag-triggered job that would fire on real releases; see `linux-v4/packaging/README.md` |

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

## Open follow-ups

- **Run the Velopack-Linux spike** (`workflow_dispatch` on `linux-ci.yml`'s
  `velopack-spike` job) and update `linux-v4/packaging/README.md` +
  `updater.py::is_packaged()` + `build_linux.py`'s `vpk pack` call with what
  it finds. This is the next concrete step to unblock the rest of Phase 5.
- Push this branch and let `linux-ci.yml`'s `test` job actually run on
  `ubuntu-latest` for the first time — every phase above was verified as
  thoroughly as possible on the Windows dev machine (imports resolve, the
  dispatch logic is correct, the full existing test suite + a real `--smoke`
  app launch stay green), but the Linux-specific code paths themselves
  (`_pty_backend_posix.py`'s real spawn/read/write cycle, the real keyring
  round trip, the `dbus-run-session`/`gnome-keyring-daemon --unlock` CI
  wrapper) have never actually executed anywhere yet.
- De-dup `secret_store.py` and `supabase_auth.py`'s inline copy (see the
  `TODO(linux-port)` comment in `supabase_auth.py`) once both are proven
  stable on real Linux CI.
- `windows_launcher/constraints.txt` needs regenerating from a real build venv
  (`pip freeze`) once `ptyprocess`/`platformdirs`/`keyring` should be pinned
  for a release build — not done as part of this port (constraints.txt is a
  Windows-build-machine artifact, regenerated per `packaging/README.md`).
- Once Linux CI is green and the spike is confirmed, add the `build-linux`
  job to `.github/workflows/release.yml` (see this phase's notes above for
  why it wasn't added yet).
