# Packaging & releasing AgentDeck for Linux

Structural sibling of [`../../packaging/README.md`](../../packaging/README.md)
(the Windows doc) -- read that first for the overall PyInstaller + Velopack
model. This doc covers only what's Linux-specific.

**Status: unverified.** Everything here was written by following the Windows
pipeline's shape and Velopack's documented Linux support, without a Linux
machine to actually run it against. Before trusting any of this for a real
release, run `.github/workflows/linux-ci.yml`'s `velopack-spike` job
(`workflow_dispatch` from the Actions tab) and update this file +
`linux-v4/context.md` with what it finds. Do not add a tag-triggered
`build-linux` job to `.github/workflows/release.yml` before that -- it would
fire on the next real Windows version tag too.

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
   .venv-build-linux/bin/pip install -r windows_launcher/requirements.txt
   .venv-build-linux/bin/pip install -r windows_launcher/requirements-build.txt
   ```
   (Do **not** `pip install ./voice_capture` for this build -- see the spec's
   docstring on why voice is left out for v1.)
2. Qt6/xcb runtime libraries (see `.github/workflows/linux-ci.yml` for the
   exact `apt-get install` list) and, for the secret-storage tests, a Secret
   Service provider (`gnome-keyring` or similar) -- not needed for the build
   itself, only for running `windows_launcher/test_*.py`.
3. .NET SDK 8 + the Velopack CLI: `dotnet tool install -g vpk`, and
   `~/.dotnet/tools` on `PATH`.
4. `gh auth login` (or a `GITHUB_TOKEN`) for `vpk upload github`.

## Build

```
.venv-build-linux/bin/python linux-v4/packaging/build_linux.py
#   -> dist/AgentDeck/                        (the frozen app)
#   -> linux-v4/packaging/Releases/           (an AppImage, *-full.nupkg,
#                                               releases.linux.json --
#                                               PENDING SPIKE CONFIRMATION)
```

## Publish (once the spike confirms this works)

```
vpk upload github --repoUrl https://github.com/atik806/AgentDeck \
    --outputDir linux-v4/packaging/Releases --publish true \
    --releaseName "AgentDeck 0.x.y" --tag v0.x.y --channel linux

gh release upload v0.x.y linux-v4/packaging/Releases/SHA256SUMS.txt --clobber
```

Same "pick one release path" rule as Windows: either publish locally (which
also creates the tag) or push the tag and let CI publish, not both.

## Known unknowns (fill in once the spike runs)

- **`vpk pack`'s exact Linux CLI surface** -- `--categories`, whether it
  auto-generates a `.desktop`/AppDir or needs one supplied, whether
  `--channel linux` is really the default or needs to be explicit.
- **`updater.py::is_packaged()`'s Linux branch** -- currently hardcoded
  `False` (see that function's docstring) until the spike reveals what
  marks "this is a Velopack-installed AppImage" on disk.
- **Fallback path**, if the spike shows Velopack's Linux/AppImage support
  isn't there yet: ship a plain `tar czf` of `dist/AgentDeck/` instead of
  `vpk pack`, upload via `gh release upload` directly, and leave
  `is_packaged()` permanently `False` (no in-app auto-update on Linux for
  v1). Update this file if that's the path taken.
