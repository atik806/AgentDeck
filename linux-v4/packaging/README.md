# Packaging & releasing AgentDeck for Linux

Structural sibling of [`../../packaging/README.md`](../../packaging/README.md)
(the Windows doc) -- read that first for the overall PyInstaller + Velopack
model. This doc covers only what's Linux-specific.

**Status: verified end-to-end on real CI, not yet exercised against a real
release tag.** `.github/workflows/linux-ci.yml`'s `velopack-spike` job
confirmed Velopack's Linux/AppImage support works at all; its `build-real-app`
job (manual dispatch) builds and packs the *actual* AgentDeck app via
`AgentDeck-linux.spec` + `build_linux.py` and runs the produced AppImage end
to end -- both green. `.github/workflows/release.yml` now has a `build-linux`
job wired in (runs after the Windows `build` job, so it never races it for the
shared GitHub Release). The one thing still unverified is the real combined
publish -- Windows + Linux assets landing on the same real tagged release --
since that only happens on an actual `v*` tag push. See `linux-v4/context.md`
for the full history.

## What's here

- `AgentDeck-linux.spec` -- PyInstaller onedir spec, sibling to
  `../../packaging/AgentDeck.spec`. Bundles `ptyprocess` (not `pywinpty`) and
  the plugins subsystem; **does not bundle voice** (sounddevice/webrtcvad/
  pywhispercpp/voice_capture) -- voice dictation is out of scope for the
  Linux port's first milestone, and the app already degrades gracefully
  without those deps installed.
- `build_linux.py` -- sibling to `../../packaging/build.py`. Freezes the app,
  sanity-checks the bundle, smoke-launches it (under `xvfb-run` if there's no
  real `$DISPLAY`), then `vpk pack --channel linux` into `Releases/`.
- `Releases/` -- build output, git-ignored the same way `../../packaging/
  Releases/` is (not yet -- add to `.gitignore` once this directory actually
  starts collecting build artifacts locally).

## One-time build-machine setup (Linux)

1. Python 3.11+, a venv:
   ```
   cd /path/to/AgentDeck
   python3.11 -m venv .venv-build-linux
   .venv-build-linux/bin/pip install "pip==24.3.1"
   .venv-build-linux/bin/pip install -c windows_launcher/constraints.txt -r windows_launcher/requirements.txt
   .venv-build-linux/bin/pip install -c windows_launcher/constraints.txt -r windows_launcher/requirements-build.txt
   ```
   (Do **not** `pip install ./voice_capture` for this build -- see the spec's
   docstring on why voice is left out for v1. `constraints.txt` is the
   Windows-build-machine's frozen closure, but reusing it with `-c` here
   keeps Linux on the exact same `pyinstaller`/`velopack` versions rather
   than silently drifting -- this is what `release.yml`'s `build-linux` job
   does.)
2. Qt6/xcb runtime libraries (see `.github/workflows/linux-ci.yml` for the
   exact `apt-get install` list, including `libxkbcommon-x11-0` -- easy to
   miss, not obvious from Qt's own "xcb-cursor0" error text) and, for the
   secret-storage tests, a Secret Service provider (`gnome-keyring` or
   similar) -- not needed for the build itself, only for running
   `windows_launcher/test_*.py`.
3. .NET SDK 8 + the Velopack CLI: `dotnet tool install -g vpk`, and
   `~/.dotnet/tools` on `PATH`.
4. `gh auth login` (or a `GITHUB_TOKEN`) for `vpk upload github`.

## Build

```
.venv-build-linux/bin/python linux-v4/packaging/build_linux.py
#   -> dist/AgentDeck/                        (the frozen app)
#   -> linux-v4/packaging/Releases/           (AgentDeck.AppImage,
#                                               AgentDeck-<ver>-linux-full.nupkg,
#                                               releases.linux.json,
#                                               assets.linux.json,
#                                               SHA256SUMS.txt)
```

## Publish

```
vpk upload github --repoUrl https://github.com/atik806/AgentDeck \
    --outputDir linux-v4/packaging/Releases --publish true \
    --releaseName "AgentDeck 0.x.y" --tag v0.x.y

gh release upload v0.x.y linux-v4/packaging/Releases/SHA256SUMS.txt --clobber
```

Note: unlike the build step, `vpk upload` does **not** take `--channel` --
it infers the channel from what's actually in `--outputDir` (mirrors the
Windows job, which never passes `--channel` to `upload` either). If a
Windows release for this tag already exists (the normal case -- `release.yml`
runs `build-linux` after `build`), rename the checksum file first
(`SHA256SUMS-linux.txt`) before the `gh release upload` step -- both
platforms' `checksums.py` runs produce a file literally named
`SHA256SUMS.txt`, and uploading that verbatim would clobber the Windows one
on the shared release.

Same "pick one release path" rule as Windows: either publish locally (which
also creates the tag) or push the tag and let CI publish, not both.

## Resolved (previously "known unknowns")

- **`vpk pack`'s Linux CLI surface**: confirmed via `velopack-spike` +
  `build-real-app`. `--icon` needs a PNG (not `.ico`); `--channel linux`
  works and produces `releases.linux.json`/`assets.linux.json`; `vpk pack`
  auto-generates the AppImage/AppDir from a plain `--packDir`, no
  hand-authored `.desktop` file needed.
- **`updater.py::is_packaged()`**: turned out not to need a Linux-specific
  on-disk layout check at all -- `velopack.App().run()` and
  `velopack.UpdateManager(...)` both do their own cross-platform
  auto-detection internally and are safe to call unconditionally. See
  `linux-v4/context.md`'s "`is_packaged()` no longer needs a Linux-specific
  layout check" section for the full investigation.
- **Fallback path**: not needed -- the AppImage path works.

## Still open

- A real `v*` tag push exercising `release.yml`'s `build-linux` job for real,
  publishing alongside Windows to one actual release.
