"""Persisted user settings.

The config lives where each platform expects it: ``%APPDATA%\\multi-terminal`` on
Windows, ``$XDG_CONFIG_HOME``/``~/.config/multi-terminal`` elsewhere.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "multi-terminal"

#: Settings whose default depends on the running platform are filled in by
#: :func:`load_config`, not stored here.
DEFAULT_CONFIG = {
    "terminal_count": 4,
    "terminal_emulator": "",
    "auto_tile": True,
    "single_window": True,
    "theme": "system",
}


def config_dir() -> Path:
    """Per-user config directory for this app."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def config_file() -> Path:
    return config_dir() / "config.json"


# Kept as module attributes because earlier versions referenced them directly.
CONFIG_DIR = config_dir()
CONFIG_FILE = config_file()


def ensure_config_dir() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)


def default_config() -> dict:
    """Defaults with the platform's preferred terminal filled in."""
    defaults = dict(DEFAULT_CONFIG)
    if not defaults["terminal_emulator"]:
        from platforms import get_backend

        defaults["terminal_emulator"] = get_backend().default_emulator
    return defaults


def migrate(data: dict) -> dict:
    """Upgrade a config written by an older version, in place-ish.

    ``use_tmux`` became ``single_window`` when Windows gained split-pane support:
    the setting means the same thing, but tmux is only one way to provide it.
    """
    data = dict(data)
    if "use_tmux" in data:
        data.setdefault("single_window", bool(data["use_tmux"]))
        del data["use_tmux"]
    return data


def load_config() -> dict:
    defaults = default_config()
    path = config_file()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, NotADirectoryError):
        return defaults
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # A corrupt config shouldn't stop the app from starting.
        return defaults
    if not isinstance(data, dict):
        return defaults
    return {**defaults, **migrate(data)}


def save_config(config: dict) -> bool:
    """Write settings atomically. False if the write failed."""
    merged = {**default_config(), **migrate(config)}
    path = config_file()
    temp = path.with_suffix(".json.tmp")
    try:
        ensure_config_dir()
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2)
        # Replace in one step so an interrupted write can't truncate the config.
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        return False
    return True
