# PyInstaller spec for AgentDeck (onedir), Linux build.
#
# Structural sibling of ../../packaging/AgentDeck.spec -- see that file's
# comments for the onedir-not-onefile rationale (Velopack's Python
# integration requires onedir; it applies identically on Linux). Differences
# from the Windows spec, and why:
#
#   * pywinpty's collect_all() is replaced with ptyprocess's -- see
#     windows_launcher/_pty_backend_posix.py.
#   * voice (sounddevice/webrtcvad/pywhispercpp/voice_capture) is left OUT of
#     this build entirely for v1: voice dictation is explicitly out of scope
#     for the Linux port's first milestone (see linux-v4/context.md), and the
#     app is already designed to degrade gracefully when those deps are
#     absent (voice_engine.py: "optional -- missing -> mic disabled, panel
#     fine"), so there's no need to carry native-dependency-heavy, untested-
#     on-Linux packages into the first build. Add them back the same way the
#     Windows spec does once voice is in scope here too.
#   * the plugin subsystem (github/vercel/jira/gitlab/linear) IS still
#     bundled even though wiring/testing it on Linux is also deferred --
#     unlike voice, nothing in that code path has a "gracefully degrade if
#     absent" story, and the Plugins nav item is still reachable in the UI.
#     Leaving its hiddenimports out would trade "untested" for "crashes on
#     click", which is strictly worse.
#   * no EXE(icon=...) -- Velopack's Linux packaging takes a PNG icon via
#     `vpk pack --icon`, not something PyInstaller embeds in the ELF binary.
#
#   Build from the repo root:
#       python -m PyInstaller linux-v4/packaging/AgentDeck-linux.spec --noconfirm
#   (linux-v4/packaging/build_linux.py does this plus the post-build sanity
#   checks + vpk pack -- see that file.)

import os

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(SPEC), "..", ".."))
LAUNCHER = os.path.join(REPO, "windows_launcher")

datas = []
binaries = []
hiddenimports = []

# --- pty backend ----------------------------------------------------------
d, b, h = collect_all("ptyprocess")
datas += d
binaries += b
hiddenimports += h

# --- accounts (plain requests, no supabase SDK) ----------------------------
datas += collect_data_files("certifi")
hiddenimports += ["requests", "certifi", "urllib3", "charset_normalizer", "idna"]

# --- Linux secret storage (keyring) ----------------------------------------
d, b, h = collect_all("keyring")
datas += d
binaries += b
hiddenimports += h
# keyring discovers its backend (SecretService/KWallet/...) via entry points,
# which PyInstaller's static analysis can miss -- name the common ones
# defensively so a frozen build doesn't silently fall back to "no backend".
hiddenimports += [
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "jeepney",
]

# --- the plugins subsystem (windows_launcher/*.py) -- see module docstring
# above for why this stays in even though Linux wiring/testing is deferred.
hiddenimports += [
    "secret_store", "plugin_store",
    "github_auth", "github_api", "github_mcp",
    "github_controller", "github_review_dialog",
    "vercel_mcp", "vercel_controller",
    "jira_mcp", "jira_controller",
    "gitlab_mcp", "gitlab_controller",
    "linear_mcp", "linear_controller",
    "mcp_io", "mcp_targets",
]

# Writing non-JSON agent config (Codex's TOML, Goose's YAML). Pure-Python but
# PyInstaller under-collects ruamel.yaml's plugin submodules; tomlkit is flat.
hiddenimports += ["tomlkit"] + collect_submodules("ruamel.yaml")

# The one asset the app loads at runtime (window/taskbar icon). Velopack's
# Linux --icon flag additionally wants a plain PNG -- see build_linux.py.
datas += [(os.path.join(LAUNCHER, "assets", "icon.ico"), "assets")]

# --- trim: Qt modules and heavy libs the app never imports (same list as the
# Windows spec, minus the Windows-only pywin32 excludes -- meaningless here) -
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets",          # splash paints text, not SVG
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtWebSockets",
    "PySide6.QtWebChannel", "PySide6.QtNfc",
    "tkinter", "pytest", "black", "ruff", "mypy",
    # voice_capture extras -- moot since voice_capture itself isn't installed
    # in this build's venv, but harmless to keep excluded defensively.
    "torch", "torchaudio", "scipy", "soundfile", "silero_vad",
]

a = Analysis(
    [os.path.join(LAUNCHER, "main.py")],
    pathex=[LAUNCHER],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(REPO, "packaging", "hooks")],  # reused, not duplicated
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AgentDeck",
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AgentDeck",       # -> dist/AgentDeck/
)
