#!/usr/bin/env python3
"""One command: freeze AgentDeck for Linux, sanity-check the bundle, and
`vpk pack` an AppImage.

    python linux-v4/packaging/build_linux.py [--no-pack] [--no-smoke]

Structural sibling of ../../packaging/build.py -- see that file's docstring
for the overall flow; differences here are Linux-specific:

  1. no MS-Store-Python guard (meaningless off Windows)
  2. parse the version from windows_launcher/version.py (same regex)
  3. clean build/ and dist/
  4. PyInstaller linux-v4/packaging/AgentDeck-linux.spec  ->  dist/AgentDeck/
  5. assert the fragile bits landed (the ELF binary, the Qt xcb platform
     plugin; and that WebEngine did NOT come along) -- no winpty-equivalent
     check since ptyprocess is pure Python
  6. smoke-launch dist/AgentDeck/AgentDeck --no-wizard --no-splash --no-login
     --smoke (under xvfb-run if there's no real DISPLAY, e.g. in CI)
  7. vpk pack --channel linux  ->  linux-v4/packaging/Releases/ (an AppImage +
     *-full.nupkg + delta + releases.linux.json) -- UNVERIFIED until the
     Phase 0 Velopack-Linux spike (see linux-v4/context.md) confirms vpk's
     actual Linux CLI surface; this call may need adjusting once that's run
  8. write linux-v4/packaging/Releases/SHA256SUMS.txt (reuses
     packaging/checksums.py unchanged -- it's OS-agnostic)
  9. print the `vpk upload github` command (never uploads)

Prerequisites: windows_launcher/requirements.txt + requirements-build.txt,
installed into a venv on the Linux build machine (a fresh ubuntu-latest CI
checkout in practice -- see .github/workflows/release.yml's build-linux job,
not yet added pending the spike, and .github/workflows/linux-ci.yml's
velopack-spike job for the spike itself).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
LAUNCHER = REPO / "windows_launcher"
DIST_APP = REPO / "dist" / "AgentDeck"
INTERNAL = DIST_APP / "_internal"
RELEASES = REPO / "linux-v4" / "packaging" / "Releases"
SPEC = REPO / "linux-v4" / "packaging" / "AgentDeck-linux.spec"
ICON_PNG = LAUNCHER / "assets" / "icon-256.png"


def fail(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"\n[build-linux] ERROR: {msg}\n", file=sys.stderr)
    raise SystemExit(1)


def guard_python() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        fail("PyInstaller not installed — pip install -r windows_launcher/requirements-build.txt")


def read_version() -> str:
    src = (LAUNCHER / "version.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', src)
    if not m:
        fail("could not parse __version__ from windows_launcher/version.py")
    return m.group(1)


def clean() -> None:
    for d in (REPO / "build", REPO / "dist"):
        if d.exists():
            shutil.rmtree(d)
    # Same reasoning as the Windows build.py: start Releases/ empty so `vpk
    # pack` doesn't refuse to re-pack an already-present version, and a real
    # release pipeline would `vpk download github` here first for deltas --
    # this local/CI build ships full packages.
    if RELEASES.exists():
        for f in RELEASES.iterdir():
            if f.is_file():
                f.unlink()


def run_pyinstaller() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"],
        cwd=REPO, check=True,
    )


def assert_bundle() -> None:
    must_exist = [
        DIST_APP / "AgentDeck",
        INTERNAL / "assets" / "icon.ico",
        INTERNAL / "PySide6" / "Qt" / "plugins" / "platforms" / "libqxcb.so",
    ]
    missing = [p for p in must_exist if not p.exists()]
    if missing:
        # PySide6's exact on-disk layout for Qt plugins has moved between
        # versions (plugins/ vs. Qt/plugins/) -- fall back to a glob so this
        # check doesn't spuriously fail over a path shape this repo hasn't
        # pinned down yet. Still fails loudly if libqxcb.so is nowhere at all.
        if not any(INTERNAL.rglob("libqxcb.so")):
            fail(f"missing from the bundle: {missing[0].relative_to(DIST_APP)}")

    strays = list(INTERNAL.rglob("libQt6WebEngineCore.so"))
    if strays:
        fail("libQt6WebEngineCore.so got bundled — the excludes list is not taking effect")

    print("[build-linux] bundle checks passed")


def smoke() -> None:
    exe = DIST_APP / "AgentDeck"
    os.chmod(exe, 0o755)
    argv = [str(exe), "--no-wizard", "--no-splash", "--no-login", "--smoke"]
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        argv = ["xvfb-run", "-a", *argv]
    try:
        r = subprocess.run(argv, cwd=DIST_APP, timeout=90)
    except subprocess.TimeoutExpired:
        fail("smoke launch timed out (window never came up / pane never spawned)")
    if r.returncode != 0:
        fail(f"smoke launch exited {r.returncode}")
    print("[build-linux] smoke launch ok")


def vpk_pack(version: str) -> None:
    if shutil.which("vpk") is None:
        fail("vpk not on PATH — dotnet tool install -g vpk")
    if not ICON_PNG.is_file():
        fail(f"Linux icon PNG missing: {ICON_PNG} (Velopack's --icon needs PNG, not .ico)")
    RELEASES.mkdir(parents=True, exist_ok=True)
    # --channel linux: explicit rather than relying on vpk's platform default,
    # so the feed this produces (releases.linux.json) is unambiguous whatever
    # vpk version ends up on the build machine. UNVERIFIED flags -- see the
    # module docstring; adjust once the Phase 0 spike's findings land in
    # linux-v4/context.md.
    subprocess.run(
        ["vpk", "pack",
         "--packId", "AgentDeck",
         "--packVersion", version,
         "--packDir", str(DIST_APP),
         "--mainExe", "AgentDeck",
         "--packTitle", "AgentDeck",
         "--icon", str(ICON_PNG),
         "--channel", "linux",
         "--outputDir", str(RELEASES)],
        cwd=REPO, check=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pack", action="store_true", help="stop after the bundle checks")
    ap.add_argument("--no-smoke", action="store_true", help="skip the smoke launch")
    args = ap.parse_args()

    guard_python()
    version = read_version()
    print(f"[build-linux] AgentDeck {version}")

    clean()
    run_pyinstaller()
    assert_bundle()
    if not args.no_smoke:
        smoke()

    if args.no_pack:
        print("[build-linux] done (bundle only). dist/AgentDeck/ is ready.")
        return 0

    vpk_pack(version)
    subprocess.run([sys.executable, str(REPO / "packaging" / "checksums.py"),
                    str(RELEASES)], check=True)
    print(f"\n[build-linux] done. Releases in {RELEASES}\n")
    print("Publish with (needs gh auth / a GITHUB_TOKEN):")
    print(f'  vpk upload github --repoUrl https://github.com/atik806/AgentDeck '
          f'--outputDir linux-v4/packaging/Releases --publish true '
          f'--releaseName "AgentDeck {version}" --tag v{version} --channel linux')
    print(f'  gh release upload v{version} '
          f'"{RELEASES / "SHA256SUMS.txt"}" --clobber')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
