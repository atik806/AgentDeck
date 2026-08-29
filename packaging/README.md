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
   windows_launcher\.venv-build\Scripts\pip install -U pip
   windows_launcher\.venv-build\Scripts\pip install -r windows_launcher\requirements.txt
   windows_launcher\.venv-build\Scripts\pip install .\voice_capture
   windows_launcher\.venv-build\Scripts\pip install -r windows_launcher\requirements-build.txt
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
```

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

`.github/workflows/release.yml` runs the same `build.py` + `vpk upload` on any
`v*` tag pushed to GitHub. Bump `version.py`, commit, then:

```
git tag v0.1.1 && git push origin v0.1.1
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `build.py` refuses to start | MS Store Python or wrong venv — use `.venv-build` from python.org |
| every pane says "failed to start" in the frozen app | winpty DLLs missing — check `dist\AgentDeck\_internal\winpty\`; `collect_all('winpty')` in the spec |
| voice permanently unavailable in the frozen app only | pywhispercpp DLLs — see `packaging/hooks/hook-pywhispercpp.py`; confirm a `ggml*.dll` in `_internal\` |
| "Windows protected your PC" on install | unsigned build — **More info → Run anyway**. Fix later with Azure Trusted Signing (`vpk pack --azureTrustedSignFile`). |
| Update button hidden on an installed build | not launched via the Velopack shortcut, or `Update.exe` missing beside `current\` |
| bundle too big | the spec already trims unused Qt; a voice-less `--lite` channel is possible with no code change (voice degrades gracefully) |
