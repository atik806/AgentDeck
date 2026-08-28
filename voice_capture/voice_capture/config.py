"""
Configuration management for Voice Capture.

Default config location: ~/.config/voice_capture/config.json
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _get_config_dir() -> Path:
    """Get platform-specific config directory."""
    import platform
    system = platform.system()

    if system == "Windows":
        # Windows: %APPDATA%\voice_capture
        return Path(os.environ.get("APPDATA", Path.home())) / "voice_capture"
    elif system == "Darwin":
        # macOS: ~/Library/Application Support/voice_capture
        return Path.home() / "Library" / "Application Support" / "voice_capture"
    else:
        # Linux/Unix: ~/.config/voice_capture (XDG-compliant)
        return Path.home() / ".config" / "voice_capture"


def _get_cache_dir() -> Path:
    """Get platform-specific cache directory."""
    import platform
    system = platform.system()

    if system == "Windows":
        # Windows: %LOCALAPPDATA%\voice_capture\Cache
        localappdata = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", Path.home()))
        return Path(localappdata) / "voice_capture" / "Cache"
    elif system == "Darwin":
        # macOS: ~/Library/Caches/voice_capture
        return Path.home() / "Library" / "Caches" / "voice_capture"
    else:
        # Linux/Unix: ~/.cache/voice_capture (XDG-compliant)
        return Path.home() / ".cache" / "voice_capture"


CONFIG_DIR = _get_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = _get_cache_dir()
MODELS_DIR = CACHE_DIR / "models"

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    # --- Microphone ---
    # Use None for system default input device.
    # Set to an integer device ID (from `sounddevice.query_devices()`)
    # or a device name substring to auto-select.
    "microphone_device": None,          # null = default, int = device ID, str = name match

    # --- Whisper Model ---
    # Model size: "tiny", "tiny.en", "base", "base.en", "small",
    # "small.en", "medium", "medium.en", "large-v3", "large-v3-turbo"
    # The ".en" variants are English-only and slightly more accurate for English.
    "model_size": "tiny.en",

    # Path to a local GGML model file. If set, overrides model_size.
    "model_path": None,

    # --- Audio Settings ---
    "sample_rate": 16000,               # Whisper expects 16 kHz
    "channels": 1,                      # Mono
    "blocksize": 480,                   # 30ms @ 16kHz (must match VAD frame)
    "dtype": "float32",                 # Audio data type

    # --- Voice Activity Detection ---
    # Backend: "webrtc" (default, lightweight) or "silero" (requires torch)
    "vad_backend": "webrtc",
    # VAD aggressiveness (0-3, webrtc only): 0=least aggressive, 3=most
    "vad_aggressiveness": 2,
    # Speech threshold for Silero VAD (0.0 - 1.0)
    "vad_threshold": 0.5,
    # Silence duration (in number of blocks) before transcribing
    "silence_blocks": 10,               # ~300ms @ 30ms blocks

    # --- Transcription Settings ---
    # Language hint (None = auto-detect, "en" = English)
    "language": "en",
    # Number of CPU threads for inference (None = auto)
    "n_threads": None,
    # Print progress during transcription
    "print_progress": False,
    # Print real-time transcription to stdout
    "print_realtime": True,

    # --- UI Settings ---
    "theme": "system",                  # "system", "light", "dark"
    "window_width": 800,
    "window_height": 600,
    "font_size": 12,
    "show_timestamps": True,
    "auto_scroll": True,
    "stay_on_top": False,
}

# Field types for validation
CONFIG_SCHEMA: Dict[str, type] = {
    "microphone_device": (int, str, type(None)),
    "model_size": str,
    "model_path": (str, type(None)),
    "sample_rate": int,
    "channels": int,
    "blocksize": int,
    "dtype": str,
    "vad_backend": str,
    "vad_aggressiveness": int,
    "vad_threshold": (int, float),
    "silence_blocks": int,
    "language": (str, type(None)),
    "n_threads": (int, type(None)),
    "print_progress": bool,
    "print_realtime": bool,
    "theme": str,
    "window_width": int,
    "window_height": int,
    "font_size": int,
    "show_timestamps": bool,
    "auto_scroll": bool,
    "stay_on_top": bool,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    """Create config and cache directories if they don't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    """
    Load configuration from disk, merging with defaults.

    Returns:
        A dict with all config keys populated (missing keys filled from defaults).
    """
    ensure_dirs()
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[WARN] Failed to load config ({exc}), using defaults.")
        return dict(DEFAULT_CONFIG)

    # Merge: default values are overridden by saved config
    merged = {**DEFAULT_CONFIG, **data}

    # Validate types
    for key, expected_type in CONFIG_SCHEMA.items():
        if key in merged and not isinstance(merged[key], expected_type):
            print(f"[WARN] Config key '{key}' has wrong type. Using default.")
            merged[key] = DEFAULT_CONFIG[key]

    return merged


def save_config(config: Dict[str, Any]) -> None:
    """
    Save configuration to disk.

    Args:
        config: A dict with config values (will be merged with defaults).
    """
    ensure_dirs()
    merged = {**DEFAULT_CONFIG, **config}
    with open(CONFIG_FILE, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
        f.write("\n")


def get_models_dir() -> Path:
    """
    Get the directory where GGML models are stored.

    pywhispercpp stores models in ~/.cache/pywhispercpp/ by default.
    We also maintain our own models directory for manually downloaded models.
    """
    ensure_dirs()
    return MODELS_DIR


def get_config_path() -> Path:
    """Get the path to the config file."""
    return CONFIG_FILE
