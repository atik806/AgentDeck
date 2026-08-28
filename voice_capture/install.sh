#!/usr/bin/env bash
# =============================================================================
# Voice Capture - Installation Script
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "\n${CYAN}==>${NC} $1"; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
log_step "Checking system requirements"

# Python version check
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER="$($cmd --version 2>&1 | grep -oP '\d+\.\d+')"
        PY_MAJOR="${PY_VER%.*}"
        PY_MINOR="${PY_VER#*.}"
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    log_error "Python >= 3.10 is required. Found: $($PYTHON --version 2>/dev/null || echo 'none')"
    log_error "Install Python 3.10+ from https://www.python.org/downloads/"
    exit 1
fi
log_info "Python: $($PYTHON --version)"

# Check for system dependencies
log_step "Checking system dependencies"

OS="$(uname -s)"
case "$OS" in
    Linux)
        # Detect distribution
        if command -v apt-get &>/dev/null; then
            log_info "Detected Debian/Ubuntu"
            PKGS_TO_INSTALL=""
            dpkg -s libportaudio2 &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL libportaudio2"
            dpkg -s portaudio19-dev &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL portaudio19-dev"
            dpkg -s libsndfile1 &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL libsndfile1"
            dpkg -s cmake &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL cmake"
            dpkg -s python3-dev &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL python3-dev"
            dpkg -s build-essential &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL build-essential"

            if [ -n "$PKGS_TO_INSTALL" ]; then
                log_warn "Missing packages, installing: $PKGS_TO_INSTALL"
                sudo apt-get update -qq
                sudo apt-get install -y -qq $PKGS_TO_INSTALL
                log_info "System dependencies installed"
            else
                log_info "All system dependencies satisfied"
            fi

        elif command -v dnf &>/dev/null; then
            log_info "Detected Fedora"
            PKGS_TO_INSTALL=""
            rpm -q portaudio &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL portaudio"
            rpm -q portaudio-devel &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL portaudio-devel"
            rpm -q libsndfile &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL libsndfile"
            rpm -q cmake &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL cmake"
            rpm -q python3-devel &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL python3-devel"
            rpm -q gcc-c++ &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL gcc-c++"

            if [ -n "$PKGS_TO_INSTALL" ]; then
                log_warn "Missing packages, installing: $PKGS_TO_INSTALL"
                sudo dnf install -y $PKGS_TO_INSTALL
                log_info "System dependencies installed"
            else
                log_info "All system dependencies satisfied"
            fi

        elif command -v pacman &>/dev/null; then
            log_info "Detected Arch Linux"
            PKGS_TO_INSTALL=""
            pacman -Qi portaudio &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL portaudio"
            pacman -Qi libsndfile &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL libsndfile"
            pacman -Qi cmake &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL cmake"
            pacman -Qi base-devel &>/dev/null || PKGS_TO_INSTALL="$PKGS_TO_INSTALL base-devel"

            if [ -n "$PKGS_TO_INSTALL" ]; then
                log_warn "Missing packages, installing: $PKGS_TO_INSTALL"
                sudo pacman -Sy --noconfirm $PKGS_TO_INSTALL
                log_info "System dependencies installed"
            else
                log_info "All system dependencies satisfied"
            fi
        else
            log_warn "Unknown Linux distribution. Install manually:"
            log_warn "  - portaudio + development headers"
            log_warn "  - libsndfile"
            log_warn "  - cmake"
            log_warn "  - python3-dev / python3-devel"
            log_warn "  - build-essential / base-devel"
        fi
        ;;

    Darwin)
        log_info "Detected macOS"
        if ! command -v brew &>/dev/null; then
            log_warn "Homebrew not found. Install from https://brew.sh/"
            log_warn "Or install manually: portaudio, libsndfile, cmake"
        else
            PKGS=""
            brew list portaudio &>/dev/null 2>&1 || PKGS="$PKGS portaudio"
            brew list libsndfile &>/dev/null 2>&1 || PKGS="$PKGS libsndfile"
            brew list cmake &>/dev/null 2>&1 || PKGS="$PKGS cmake"
            if [ -n "$PKGS" ]; then
                log_warn "Installing: $PKGS"
                brew install $PKGS
            else
                log_info "All Homebrew dependencies satisfied"
            fi
        fi
        log_info "Note: sounddevice bundles PortAudio on macOS via pip."
        ;;

    MINGW*|MSYS*)
        log_info "Detected Windows (MSYS2/MinGW)"
        log_warn "On Windows, sounddevice bundles PortAudio. Ensure you have:"
        log_warn "  - Visual Studio Build Tools or MinGW"
        log_warn "  - cmake (for pywhispercpp building from source if needed)"
        log_warn "  - Or use: pip install pywhispercpp (pre-built wheel)"
        ;;

    *)
        log_warn "Unknown OS: $OS. Install dependencies manually."
        ;;
esac

# ---------------------------------------------------------------------------
# Create virtual environment
# ---------------------------------------------------------------------------
log_step "Creating Python virtual environment"

VENV_DIR="$PROJECT_ROOT/.venv"

if [ -d "$VENV_DIR" ]; then
    log_info "Virtual environment already exists at $VENV_DIR"
    log_info "Activate with: source $VENV_DIR/bin/activate"
else
    log_info "Creating virtual environment at $VENV_DIR..."
    $PYTHON -m venv "$VENV_DIR"
    log_info "Virtual environment created"
fi

# Activate the venv for the rest of the script
source "$VENV_DIR/bin/activate"
log_info "Using: $(which python)"

# ---------------------------------------------------------------------------
# Upgrade pip
# ---------------------------------------------------------------------------
log_step "Upgrading pip and build tools"
python -m pip install --upgrade pip setuptools wheel --quiet
log_info "pip upgraded"

# ---------------------------------------------------------------------------
# Install Python dependencies
# ---------------------------------------------------------------------------
log_step "Installing Python dependencies"

if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    pip install -r "$PROJECT_ROOT/requirements.txt" --quiet
    log_info "Core dependencies installed from requirements.txt"
else
    pip install "pyside6>=6.11,<6.12" "sounddevice>=0.5.5,<0.6" \
                "pywhispercpp>=1.5.0,<2.0" "webrtcvad-wheels>=2.0.14,<3.0" \
                "numpy>=1.26,<2.0" --quiet
    log_info "Core dependencies installed"
fi

# Install development extras if requested
if [ "${1:-}" = "--dev" ] || [ "${1:-}" = "-d" ]; then
    log_info "Installing development dependencies..."
    pip install "pytest>=8.0" "pytest-qt>=4.2" "black>=24.0" "ruff>=0.3" "mypy>=1.8" --quiet
fi

# Install optional extras if requested
if [ "${2:-}" = "--all" ] || [ "${1:-}" = "--all" ]; then
    log_info "Installing optional extras (audio-io, signal)..."
    pip install "soundfile>=0.12,<1.0" "scipy>=1.12,<2.0" --quiet
fi

# ---------------------------------------------------------------------------
# Download Whisper model
# ---------------------------------------------------------------------------
log_step "Downloading Whisper model (tiny.en)"

# The model will auto-download on first use by pywhispercpp,
# but we pre-download here for a smoother first-run experience.
python -c "
from pathlib import Path
import sys

# Trigger the model download by loading pywhispercpp
try:
    from pywhispercpp.model import Model
    print('[INFO]  Downloading tiny.en model (~75 MB)...')
    # This triggers automatic download to ~/.cache/pywhispercpp/
    model = Model('tiny.en', print_realtime=False, print_progress=False)
    print('[INFO]  Model downloaded and loaded successfully.')
    del model
except Exception as e:
    print(f'[WARN]  Model download skipped: {e}')
    print('[WARN]  Model will download automatically on first transcribe.')
"

# ---------------------------------------------------------------------------
# Verify audio devices
# ---------------------------------------------------------------------------
log_step "Verifying audio input devices"

python -c "
import sounddevice as sd
devices = sd.query_devices()
print('[INFO]  Available audio devices:')
for i, dev in enumerate(devices):
    if dev['max_input_channels'] > 0:
        print(f'  [{i}] {dev[\"name\"]} (inputs: {dev[\"max_input_channels\"]}, '
              f'default SR: {dev[\"default_samplerate\"]:.0f} Hz)')
default_input = sd.default.device[0]
print(f'[INFO]  Default input device: [{default_input}] {devices[default_input][\"name\"]}')
" || log_warn "Could not query audio devices. Check PortAudio installation."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
log_step "Installation complete!"

echo ""
echo -e "  ${GREEN}Voice Capture${NC} has been installed successfully!"
echo ""
echo "  To get started:"
echo "    cd \"$PROJECT_ROOT\""
echo "    source .venv/bin/activate"
echo "    python -m voice_capture.main"
echo ""
echo "  Configuration: ~/.config/voice_capture/config.json"
echo "  Models directory: ~/.cache/pywhispercpp/"
echo ""
echo "  Happy transcribing!"
echo ""
