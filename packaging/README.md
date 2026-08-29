# Packaging & releasing AgentDeck

AgentDeck ships as a **PyInstaller onedir** build wrapped by **Velopack**, which
produces the installer, publishes each release to **GitHub Releases**, and drives
the in-app **Update** button. Installs are per-user
(`%LOCALAPPDATA%\AgentDeck\`) — no admin, which is what lets the app replace
itself.

## One-time build-machine setup

1. **python.org CPython 3.11 x64** (not the Microsoft Store build). Check:
   `py -3.11 -c "import sys; print(sys.base_prefix)"` — must not contain
   `WindowsApps`.
2. Build venv (separate from the run `.venv`):
   ```
   cd E:\Workspace\V4
   py -3.11 -m venv windows_launcher\.venv-build
   windows_launcher\.venv-build\Scripts\pip install "pip==24.3.1"
   windows_launcher\.venv-build\Scripts\pip install -c windows_launcher\constraints.txt -r windows_launcher\requirements.txt
   windows_launcher\.venv-build\Scripts\pip install -c windows_launcher\constraints.txt .\voice_capture
   windows_launcher\.venv-build\Scripts\pip install -c windows_launcher\constraints.txt -r windows_launcher\requirements-build.txt
   ```
   `constraints.txt` pins the whole dependency closure to exact versions (CI uses
   it too). After a deliberate dependency bump, regenerate it:
   ```
   windows_launcher\.venv-build\Scripts\python -m pip freeze > windows_launcher\constraints.txt
   #   then delete the `-e ...`, `voice_capture`, and `AgentDeck` lines
   ```
3. **.NET SDK 8** + the Velopack CLI:
   ```
   dotnet tool install -g vpk
   ```
   Ensure `%USERPROFILE%\.dotnet\tools` is on `PATH`.
4. `gh auth login` (or set `GITHUB_TOKEN`) for `vpk upload github`.

## Cut a release

```
# 1. bump the version
#    edit windows_launcher\version.py  ->  __version__ = "0.1.1"

# 2. build + verify + pack
windows_launcher\.venv-build\Scripts\python packaging\build.py
#    -> dist\AgentDeck\              (the frozen app)
#    -> packaging\Releases\          (AgentDeck-win-Setup.exe, *-full.nupkg,
#                                     *-delta.nupkg from 0.1.1 on, releases.win.json)

# 3. publish   (needs `gh auth login` or a GITHUB_TOKEN env var)
vpk upload github --repoUrl https://github.com/atik806/AgentDeck ^
    --outputDir packaging\Releases --publish true ^
    --releaseName "AgentDeck 0.1.1" --tag v0.1.1

# 4. attach the checksum manifest build.py wrote to packaging\Releases\
gh release upload v0.1.1 packaging\Releases\SHA256SUMS.txt --clobber
```

`build.py` runs `packaging\checksums.py` at the end, so `SHA256SUMS.txt` is
already sitting in `packaging\Releases\`. The CI path uploads it automatically.

`--outputDir packaging\Releases` is required -- that's where `build.py` puts the
packages; without it `vpk` looks in `.\Releases\` and fails with "Could not find
assets file for channel 'win'".

Velopack builds the delta against the previous release automatically, so after
the first install users download only the diff.

**Pick one path per version** -- either publish locally (step 3, which also
creates the `vX.Y.Z` tag) **or** push the tag and let CI publish. Not both:
`vpk upload` creating the tag fires the CI workflow, which then detects the
release is already published and skips its own publish step.

Users get the update by clicking **Update** in the toolbar (or automatically —
`auto_check_updates` prompts them shortly after launch).

## First release (0.1.0)

Same as above with `--tag v0.1.0`. There is no delta for the first release.
Then install `AgentDeck-win-Setup.exe` and confirm:

- installs to `%LOCALAPPDATA%\AgentDeck\` with **no UAC prompt**
- Start-menu + desktop shortcut, correct icon, taskbar groups under AgentDeck
- the wizard footer shows `v0.1.0`; the **Update** button is visible
- panes spawn shells; `Ctrl+Shift+X` voice loads (model downloads on first use)
- running `python windows_launcher\main.py` from source: **Update** button hidden

## CI

`.github/workflows/release.yml` runs the same `build.py` + `vpk upload` (+ the
`SHA256SUMS.txt` upload) on any `v*` tag pushed to GitHub. Third-party actions
are pinned to commit SHAs. Bump `version.py`, commit, then:

```
git tag v0.1.1 && git push origin v0.1.1
```

## Code signing ("Windows protected your PC")

The installer is **unsigned**, so on first run SmartScreen shows *"Windows
protected your PC … Unknown publisher"* behind a **More info → Run anyway**.
Nothing is broken — the app just has no Authenticode signature or SmartScreen
reputation yet. Every release ships `SHA256SUMS.txt` so downloaders can still
verify the assets (`sha256sum -c SHA256SUMS.txt`).

**Plan (free route):**
1. **winget** — `packaging/winget/` has the manifests for
   `AtikShahriar.AgentDeck`. Once merged into `microsoft/winget-pkgs`,
   `winget install AtikShahriar.AgentDeck` sidesteps the SmartScreen dialog
   (winget runs the installer itself). See `packaging/winget/README.md`.
2. **SignPath Foundation** — free OV code-signing cert + HSM signing for OSS
   projects. Application drafted in `docs/signpath-application.md`; not yet
   submitted. If approved, this removes "Unknown publisher" on the plain
   download too.

Paid fallbacks if both fall through: **Certum Open Source Code Signing**
(~$30/yr, cloud, individuals worldwide). Azure Trusted Signing is **not** an
option — its identity check is US/CA/EU/UK-only.

When there is a cert, the pipeline is already wired: set `AGENTDECK_SIGN_TEMPLATE`
to a signing command containing the literal `{{file}}` — locally before
`build.py`, or as CI secrets (see the `Configure code signing` step in
`release.yml`). `build.py` then passes it to `vpk pack --signTemplate`. Unset →
unsigned, as today.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `build.py` refuses to start | MS Store Python or wrong venv — use `.venv-build` from python.org |
| every pane says "failed to start" in the frozen app | winpty DLLs missing — check `dist\AgentDeck\_internal\winpty\`; `collect_all('winpty')` in the spec |
| voice permanently unavailable in the frozen app only | pywhispercpp DLLs — see `packaging/hooks/hook-pywhispercpp.py`; confirm a `ggml*.dll` in `_internal\` |
| "Windows protected your PC" on install | unsigned build — **More info → Run anyway**. Deferred; see [Code signing](#code-signing-windows-protected-your-pc). |
| Update button hidden on an installed build | not launched via the Velopack shortcut, or `Update.exe` missing beside `current\` |
| bundle too big | the spec already trims unused Qt; a voice-less `--lite` channel is possible with no code change (voice degrades gracefully) |
