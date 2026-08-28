<p align="center"><img src="assets/logo.svg" alt="Voice Capture" width="400"></p>

# Voice Capture

A Python desktop voice-to-text application using **whisper.cpp** for local,
real-time speech transcription with a **PySide6** GUI.

## Features

- **Real-time transcription** using whisper.cpp (CPU-only, no GPU required)
- **PySide6 GUI** - native-looking interface on Linux, macOS, and Windows
- **Voice Activity Detection** - intelligent, configurable speech detection
- **Multiple model sizes** - from tiny (75 MB, ~10x realtime) to large-v3 (3 GB)
- **Fully offline** - everything runs locally
- **Auto-model download** - models download on first use

## Requirements

### Python
- **Python >= 3.10, < 3.15**

### System Dependencies

| Distribution | Packages |
|---|---|
| **Ubuntu/Debian** | `libportaudio2 portaudio19-dev libsndfile1 cmake python3-dev build-essential` |
| **Fedora** | `portaudio portaudio-devel libsndfile cmake python3-devel gcc-c++` |
| **Arch Linux** | `portaudio libsndfile cmake base-devel` |
| **macOS (Homebrew)** | `portaudio libsndfile cmake` |
| **Windows** | Visual Studio Build Tools, cmake (for source builds) |

## Quick Start

### 1. Install system dependencies

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y libportaudio2 portaudio19-dev libsndfile1 cmake python3-dev build-essential

# Fedora
sudo dnf install -y portaudio portaudio-devel libsndfile cmake python3-devel gcc-c++

# Arch
sudo pacman -Sy --noconfirm portaudio libsndfile cmake base-devel

# macOS
brew install portaudio libsndfile cmake
```

### 2. Run the install script

```bash
chmod +x install.sh
./install.sh
```

This will:
- Create a Python virtual environment (`.venv/`)
- Install all pip dependencies
- Download the tiny.en Whisper model (~75 MB)
- Verify audio input devices

### 3. Run the application

```bash
source .venv/bin/activate
python -m voice_capture
```

## Manual Installation

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install core dependencies
pip install "pyside6>=6.11,<6.12" \
            "sounddevice>=0.5.5,<0.6" \
            "pywhispercpp>=1.5.0,<2.0" \
            "webrtcvad-wheels>=2.0.14,<3.0" \
            "numpy>=1.26,<2.0"

# Or install from requirements file
pip install -r requirements.txt
```

## Model Sizes

| Model | Size | Relative Speed | Quality | RAM Usage |
|---|---|---|---|---|
| tiny / tiny.en | 75 MB | ~10x | Basic | ~1 GB |
| base / base.en | 142 MB | ~7x | Okay | ~1 GB |
| small / small.en | 466 MB | ~4x | Good | ~2 GB |
| medium / medium.en | 1.5 GB | ~2x | Very Good | ~5 GB |
| large-v3 | 2.9 GB | ~1x | Best | ~10 GB |
| large-v3-turbo | 1.5 GB | ~6x | Near Best | ~6 GB |

> **Recommendation**: Start with `tiny.en` for English. It runs >10x realtime on modern CPUs.
> For better accuracy, try `base.en` or `small.en`.

## Configuration

Configuration file: `~/.config/voice_capture/config.json`

Key settings:
- `microphone_device`: null (default), device ID (int), or name (string)
- `model_size`: Whisper model to use
- `vad_backend`: "webrtc" (default) or "silero"
- `vad_aggressiveness`: 0-3 (WebRTC mode)
- `sample_rate`: 16000 (Whisper standard)
- `language`: "en" (English) or null (auto-detect)

## Project Structure

```
voice_capture/
├── pyproject.toml          # Build & dependency config
├── setup.py                # Legacy setup
├── requirements.txt        # Pinned dependencies
├── install.sh              # Bootstrap script
├── README.md               # This file
└── voice_capture/          # Python package
    ├── __init__.py
    ├── __main__.py         # python -m voice_capture entry
    ├── main.py             # Entry point
    ├── app.py              # Application class
    ├── config.py           # Configuration management
    ├── model_downloader.py # Model download utility
    ├── audio/
    │   ├── __init__.py
    │   └── capture.py      # sounddevice capture
    ├── vad/
    │   ├── __init__.py
    │   └── processor.py    # VAD (webrtc / silero)
    ├── transcription/
    │   ├── __init__.py
    │   └── engine.py       # pywhispercpp wrapper
    └── ui/
        ├── __init__.py
        └── main_window.py  # PySide6 window
```

## License

MIT
