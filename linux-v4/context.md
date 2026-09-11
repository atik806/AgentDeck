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
| 3 | Linux secret storage (`keyring`) | not started |
| 4 | Dead-code removal (`main_window.py`/`launcher.py`) + misc | not started |
| 5 | Linux packaging pipeline (Velopack AppImage or tarball fallback) | not started |

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

## Open follow-ups

(none yet — filled in as phases land)
