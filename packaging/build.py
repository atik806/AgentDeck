#!/usr/bin/env python3
"""One command: freeze AgentDeck, sanity-check the bundle, and `vpk pack`.

    .venv-build\\Scripts\\python packaging\\build.py [--no-pack] [--no-smoke]

Steps:
  1. guard: refuse to run on MS Store Python or outside a .venv-build
  2. parse the version from windows_launcher/version.py (regex, no import)
  3. clean build/ and dist/
  4. PyInstaller packaging/AgentDeck.spec  ->  dist/AgentDeck/
  5. assert the fragile native bits landed (winpty ConPTY, whisper DLLs, icon,
     Qt platform plugin; and that WebEngine did NOT come along)
  6. smoke-launch dist/AgentDeck/AgentDeck.exe --no-wizard --no-splash --smoke
  7. vpk pack  ->  packaging/Releases/  (Setup.exe + *-full.nupkg + delta + feed)
  8. print the `vpk upload github` command (never auto-uploads)

Prerequisites: see windows_launcher/requirements-build.txt.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "windows_launcher"
DIST_APP = REPO / "dist" / "AgentDeck"
INTERNAL = DIST_APP / "_internal"
RELEASES = REPO / "packaging" / "Releases"


def fail(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"\n[build] ERROR: {msg}\n", file=sys.stderr)
    raise SystemExit(1)


def guard_python() -> None:
    base = sys.base_prefix.lower()
    if "windowsapps" in base or "microsoft.python" in base:
        fail("running on Microsoft Store Python — build with a python.org 3.11 venv")
    if "voice_capture" in sys.prefix.replace("\\", "/"):
        fail("this looks like the voice_capture venv — use windows_launcher/.venv-build")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        fail("PyInstaller not installed — pip install -r windows_launcher/requirements-build.txt")
    try:
        import voice_capture  # noqa: F401
    except Exception:
        fail("voice_capture not importable — pip install ./voice_capture into the build venv")


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
    # Start Releases/ empty. `vpk pack` refuses to re-pack a version that is
    # already sitting there, and stale artifacts from an aborted run confuse it.
    # NOTE: delta packages are built against whatever *older* releases are in
    # this dir, so a real release pipeline should `vpk download github` here
    # first to pull the published history. This local build ships full packages.
    if RELEASES.exists():
        for f in RELEASES.iterdir():
            if f.is_file():
                f.unlink()


def run_pyinstaller() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller",
         str(REPO / "packaging" / "AgentDeck.spec"), "--noconfirm", "--clean"],
        cwd=REPO, check=True,
    )


def assert_bundle() -> None:
    must_exist = [
        DIST_APP / "AgentDeck.exe",
        INTERNAL / "assets" / "icon.ico",
        INTERNAL / "winpty" / "winpty.dll",
        INTERNAL / "winpty" / "winpty-agent.exe",
        INTERNAL / "winpty" / "conpty.dll",
        INTERNAL / "winpty" / "OpenConsole.exe",
        INTERNAL / "PySide6" / "plugins" / "platforms" / "qwindows.dll",
    ]
    for p in must_exist:
        if not p.exists():
            fail(f"missing from the bundle: {p.relative_to(DIST_APP)}")

    globs = {
        "a whisper.cpp DLL (ggml*/whisper*)": ["ggml*.dll", "whisper*.dll"],
        "the portaudio DLL": ["**/libportaudio*.dll", "**/portaudio*.dll"],
    }
    for label, patterns in globs.items():
        if not any(next(INTERNAL.glob(p), None) for p in patterns):
            fail(f"missing from the bundle: {label}")

    strays = list(INTERNAL.glob("Qt6WebEngineCore.dll"))
    if strays:
        fail("Qt6WebEngineCore.dll got bundled — the excludes list is not taking effect")

    print("[build] bundle checks passed")


def smoke() -> None:
    exe = DIST_APP / "AgentDeck.exe"
    try:
        r = subprocess.run(
            [str(exe), "--no-wizard", "--no-splash", "--no-login", "--smoke"],
            cwd=DIST_APP, timeout=90,
        )
    except subprocess.TimeoutExpired:
        fail("smoke launch timed out (window never came up / pane never spawned)")
    if r.returncode != 0:
        fail(f"smoke launch exited {r.returncode}")
    print("[build] smoke launch ok")


def vpk_pack(version: str) -> None:
    if shutil.which("vpk") is None:
        fail("vpk not on PATH — dotnet tool install -g vpk")
    RELEASES.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["vpk", "pack",
         "--packId", "AgentDeck",
         "--packVersion", version,
         "--packDir", str(DIST_APP),
         "--mainExe", "AgentDeck.exe",
         "--packTitle", "AgentDeck",
         "--icon", str(LAUNCHER / "assets" / "icon.ico"),
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
    print(f"[build] AgentDeck {version}")

    clean()
    run_pyinstaller()
    assert_bundle()
    if not args.no_smoke:
        smoke()

    if args.no_pack:
        print("[build] done (bundle only). dist/AgentDeck/ is ready.")
        return 0

    vpk_pack(version)
    print(f"\n[build] done. Releases in {RELEASES}\n")
    print("Publish with (needs gh auth / a GITHUB_TOKEN):")
    print(f'  vpk upload github --repoUrl https://github.com/atik806/AgentDeck '
          f'--outputDir packaging/Releases --publish true '
          f'--releaseName "AgentDeck {version}" --tag v{version}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
