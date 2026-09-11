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
| 0 | CI scaffolding (`linux-ci.yml`) + Velopack-Linux spike | in progress |
| 1 | POSIX PTY backend (`ptyprocess`-based) | not started |
| 2 | XDG path helper + store migration | not started |
| 3 | Linux secret storage (`keyring`) | not started |
| 4 | Dead-code removal (`main_window.py`/`launcher.py`) + misc | not started |
| 5 | Linux packaging pipeline (Velopack AppImage or tarball fallback) | not started |

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
