# SignPath Foundation — free code signing application

Draft answers for the application at <https://signpath.org/apply>. SignPath
Foundation issues a free OV code-signing certificate to qualifying open-source
projects and signs release artifacts through their platform (HSM-held key, no
certificate file ever touches CI). This removes the SmartScreen "Unknown
publisher" prompt on `AgentDeck-win-Setup.exe`.

Status: **not yet submitted.**

---

## Eligibility self-check (SignPath Foundation conditions)

| Condition | AgentDeck |
|---|---|
| OSI-approved license, no commercial dual-licensing of our own code | ✅ MIT (`/LICENSE`, added 2026-08-30) |
| No proprietary / non-open-source components | ✅ deps are PySide6 (LGPL-3.0), pyte (LGPL-2.1), pywinpty/winpty (MIT), pywhispercpp + whisper.cpp (MIT), sounddevice (MIT), webrtcvad (BSD-3), Velopack (MIT), requests/certifi (Apache-2.0/MPL-2.0) |
| Publicly available source | ✅ <https://github.com/atik806/AgentDeck> |
| Actively maintained | ✅ regular commits, releases v0.1.0 → v0.3.0 (Aug 2026) |
| Already released in the form to be signed | ✅ GitHub Releases, `AgentDeck-win-Setup.exe` + `AgentDeck-win-Portable.zip` + `.nupkg` |
| Functionality documented on the download page | ✅ <https://vibeflow.tech/agentdeck> and repo README |
| No malware / PUP | ✅ desktop terminal multiplexer; no bundled ads, telemetry, or unrelated payloads |

## Application form answers

**Project name:** AgentDeck

**Project website / download page:** https://vibeflow.tech/agentdeck

**Source code repository:** https://github.com/atik806/AgentDeck

**Short description:**
AgentDeck is a Windows multi-terminal panel: every shell lives in one window as a
real ConPTY pane, and each workspace runs the coding agent of its choice (Claude
Code, Codex, Gemini CLI, Copilot CLI, Aider, and others). It adds a workspace
sidebar, drag-and-drop of file paths into panes, a voice-to-text overlay
(whisper.cpp, fully offline), and in-app self-updates via Velopack.

**License:** MIT

**Programming languages:** Python (PySide6 / Qt for Python)

**CI system:** GitHub Actions — `.github/workflows/release.yml`, triggered by
`v*` tags. Build is PyInstaller (onedir) + Velopack `vpk pack`.

**Which artifacts need signing:**
- `AgentDeck.exe` (the frozen application, inside the bundle)
- `AgentDeck-win-Setup.exe` (Velopack installer)
- `AgentDeck-[version]-full.nupkg` / delta `.nupkg` (Velopack update packages)
- `Update.exe` (Velopack updater shipped in the bundle)

**Distribution channels:** GitHub Releases (primary), winget (`AtikShahriar.AgentDeck`,
submission pending), the vibeflow.tech/agentdeck download page.

**Maintainers / who authorizes releases:** 1 maintainer — Atik Shahriar
(GitHub: @atik806, email: atikrj8@gmail.com). Releases are cut manually by
bumping `windows_launcher/version.py` and pushing a `v*` tag.

**How releases are built and published:** tag push → GitHub Actions builds on
`windows-latest`, runs `packaging/build.py` (PyInstaller + bundle sanity checks +
smoke launch + `vpk pack`), generates `SHA256SUMS.txt`, then `vpk upload github`
publishes the GitHub Release. Third-party actions are pinned to commit SHAs and
the dependency closure is pinned via `windows_launcher/constraints.txt`.

**Planned signing integration:** add a
`signpath/github-action-submit-signing-request` step in `release.yml` after
`vpk pack` — upload `packaging/Releases/**` as the unsigned artifact, receive the
signed set back, then `vpk upload github`. The existing `AGENTDECK_SIGN_TEMPLATE`
hook in `packaging/build.py` stays as the fallback for a local signed build.

## After approval — checklist

- [ ] Accept the SignPath Foundation terms; note the organization + project slug
- [ ] Add repo secret `SIGNPATH_API_TOKEN`
- [ ] Add the signing-request step to `release.yml` (submit `packaging/Releases`,
      wait, download signed artifacts to the same dir before `vpk upload`)
- [ ] Configure the SignPath project signing policy (release-signing, GitHub
      Actions trusted build)
- [ ] Cut a patch release, confirm `AgentDeck-win-Setup.exe` shows publisher
      "Open Source Developer, Atik Shahriar" and no SmartScreen block
- [ ] Update `packaging/README.md` "Code signing" — remove the "deferred" note
