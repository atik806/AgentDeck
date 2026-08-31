# PyInstaller spec for AgentDeck (onedir).
#
#   Build from the repo root, in the dedicated build venv:
#       .venv-build\Scripts\pyinstaller packaging\AgentDeck.spec --noconfirm
#   (packaging\build.py does this plus the post-build sanity checks + vpk pack.)
#
# onedir, not onefile: Velopack's Python integration requires it, it starts
# faster, it delta-patches cleanly, and — with assets/ added as data — the
# app's `Path(__file__).parent / "assets"` lookups resolve unchanged inside
# _internal/.

import os

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))
LAUNCHER = os.path.join(REPO, "windows_launcher")

datas = []
binaries = []
hiddenimports = []

# --- native / data-heavy deps PyInstaller's default hooks under-collect --------
for pkg in ("winpty", "sounddevice", "pywhispercpp"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

datas += collect_data_files("_sounddevice_data")   # portaudio DLL
# webrtcvad is handled by packaging/hooks/hook-webrtcvad.py (the contrib hook
# breaks on the -wheels distribution name).

# Account sign-in + settings sync (see windows_launcher/supabase_auth.py). Plain
# `requests` over HTTPS -- no supabase SDK. certifi's CA bundle must ride along
# or TLS verification fails in the frozen app.
datas += collect_data_files("certifi")

hiddenimports += [
    "_cffi_backend", "_sounddevice",
    "_webrtcvad", "webrtcvad",
    "_pywhispercpp",
    "requests", "certifi", "urllib3", "charset_normalizer", "idna",
]

# The plugins subsystem (windows_launcher/*.py). Most are reached through
# top-level imports from terminal_panel, but a few are imported lazily inside
# methods (github_review_dialog from plugins_panel; github_api / supabase_auth
# from github_controller) -- name them so a frozen build always carries them.
hiddenimports += [
    "secret_store", "plugin_store",
    "github_auth", "github_api", "github_mcp",
    "github_controller", "github_review_dialog",
]

# The voice pipeline lives in the sibling `voice_capture` package. It must be
# `pip install`ed into the build venv for this to find anything.
hiddenimports += collect_submodules("voice_capture")

# The one asset the app loads at runtime.
datas += [(os.path.join(LAUNCHER, "assets", "icon.ico"), "assets")]

# --- trim: Qt modules and heavy libs the app never imports --------------------
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
    "win32com", "win32api", "win32con", "pythoncom", "pywintypes",   # unused pywin32
    "torch", "torchaudio", "scipy", "soundfile", "silero_vad",        # voice_capture extras
]

a = Analysis(
    [os.path.join(LAUNCHER, "main.py")],
    pathex=[LAUNCHER],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(REPO, "packaging", "hooks")],
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
    icon=os.path.join(LAUNCHER, "assets", "icon.ico"),
    console=False,          # windowed, like `pythonw main.py` today
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,              # UPX + Qt DLLs = SmartScreen / AV grief
    name="AgentDeck",       # -> dist/AgentDeck/
)
