"""
Configuration management for Windows Multi-Terminal Launcher.

Config location: %APPDATA%\multi-terminal\config.json
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _get_config_dir() -> Path:
    """Get Windows config directory."""
    return Path(os.environ.get("APPDATA", Path.home())) / "multi-terminal"

def _get_cache_dir() -> Path:
    """Get Windows cache directory."""
    localappdata = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", Path.home()))
    return Path(localappdata) / "multi-terminal" / "Cache"

CONFIG_DIR = _get_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = _get_cache_dir()

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

#: 1 = the original launcher-only config. 2 = added the embedded terminal panel,
#: which changed what window_width/window_height size. 3 = voice_model gained an
#: "auto" value; an untouched "tiny.en" (the old hard default) migrates to it.
CONFIG_VERSION = 3

DEFAULT_CONFIG: Dict[str, Any] = {
    # Bumped when a key changes meaning; see _migrate.
    "config_version": CONFIG_VERSION,

    # --- Terminal Settings ---
    "default_terminal": "Windows Terminal",
    "terminal_executable": None,

    # --- Embedded terminal panes ---
    # Shell used for new panes: "auto", "pwsh", "powershell", "cmd" or "bash".
    # "auto" picks the best one installed.
    "default_shell": "auto",
    "font_size": 11,
    # Lines kept above the visible screen, per pane.
    "scrollback": 5000,
    # How panes are arranged: "grid", "columns" or "rows".
    "layout": "grid",

    # --- Layout Settings ---
    "default_count": 4,
    "auto_tile": True,
    "window_padding": 10,
    "margin": 50,

    # --- Setup wizard (shown by main.py before the panel) ---
    # Folder the terminals start in. "" = the user's home directory.
    "working_folder": "",
    # Most-recently-used folders, newest first (the wizard's quick list).
    "recent_folders": [],
    # Coding agent auto-run in every terminal: an agents.py key
    # ("none", "claude", "codex", …, or "custom").
    "agent": "none",
    # The command for agent == "custom".
    "agent_command": "",
    # Skip the wizard and open straight from saved config (also: --no-wizard).
    "skip_wizard": False,
    # Play the AgentDeck launch animation before the wizard (also: --no-splash).
    "show_splash": True,
    # Pre-accept Claude Code's "trust this folder?" prompt for the working
    # folder, so an auto-launched `claude` opens straight into the session.
    # OFF by default: it suppresses a security prompt, so it is opt-in. Even
    # when enabled, agents.pretrust_folder() refuses any folder that carries its
    # own .claude/ or .mcp.json config (those can run hooks / auto-approve
    # tools -- the prompt is exactly the check the user should get to make).
    "pretrust_agent_folder": False,

    # --- Plugins (see docs/PLUGINS.md) ---
    # When a plugin (GitHub / Vercel / Jira) is connected, write its MCP server
    # into every *installed* coding agent's user-scope config -- not just the one
    # the current workspace runs. Matches how the user thinks about it ("I
    # connected GitHub" -> it works in whatever agent I open). Only ever touches
    # agents found on PATH; disconnecting removes exactly what was written. Set
    # False to scope wiring to the active workspace's agent only.
    "plugins_wire_all_agents": True,

    # --- Conversation handoff (Pro; the handoff button on a pane header) ---
    # Pass `--fork-session` / `--fork` on a same-agent resume so the source
    # pane's session is left untouched. The handoff dialog can override per use.
    "handoff_fork_session": True,
    # Include the agent's own thinking/reasoning blocks in a cross-agent
    # transcript. Off by default -- it bloats the handoff and is rarely useful
    # to the receiving agent.
    "handoff_include_thinking": False,
    # Character budget for a cross-agent transcript before it is trimmed to a
    # head + the most-recent tail. This becomes another agent's opening context,
    # so it is kept modest on purpose.
    "handoff_max_transcript_chars": 60_000,

    # --- Appearance ---
    "theme": "system",
    "window_width": 1400,
    "window_height": 880,

    # --- Behavior ---
    "start_maximized": False,
    "close_terminals_on_exit": True,
    "remember_sessions": True,

    # --- Voice input (the floating voice-to-text overlay) ---
    # Whether the overlay is shown when the panel opens (it starts idle either
    # way -- listening only begins on Ctrl+Shift+X).
    "voice_overlay_visible": True,
    # Saved top-left of the overlay inside the terminal area. -1 = auto-place
    # (bottom-right). Clamped to the current window size on load.
    "voice_overlay_x": -1,
    "voice_overlay_y": -1,
    # whisper.cpp model for transcription; downloaded on first use. "auto" picks
    # one to suit the machine (see voice_models.recommend_model); otherwise a
    # registry name like "base.en" / "small.en" / "small".
    "voice_model": "auto",
    # Microphone: null = system default, int = device id, str = name substring.
    "voice_mic_device": None,
    # Transcription language: "auto" (needs a multilingual model) or "en".
    "voice_language": "auto",
    # whisper.cpp CPU threads; 0 = let pywhispercpp decide.
    "voice_n_threads": 0,
    # Beam-search width. 1 = fast greedy decode; higher = slower, more accurate.
    "voice_beam_size": 1,
    # Decoder vocabulary priming; "" = voice_models.DEFAULT_PROMPT.
    "voice_initial_prompt": "",
    # WebRTC VAD mode 0-3. Higher rejects more non-speech (and more quiet speech).
    "voice_vad_aggressiveness": 2,
    # Trailing silence (ms) that ends an utterance.
    "voice_silence_ms": 300,
    # Minimum speech (ms) for a segment to be worth transcribing.
    "voice_min_speech_ms": 120,
    # Audio (ms) kept before speech onset so the first phoneme isn't clipped.
    "voice_preroll_ms": 300,
    # Clean up a finished utterance (capitalise, drop whisper's trailing period).
    "voice_post_processing": True,
    # On a mid-session mic failure, retry once on the system-default device.
    "voice_mic_autofallback": True,
    # Master switch for the whole voice feature (overlay + engine). Machine-local.
    "voice_input_enabled": True,
    # One-shot: the "Ctrl+Shift+X to dictate" tip has been shown. Machine-local.
    "voice_hint_seen": False,

    # --- Account (Supabase; see docs/ACCOUNTS.md) ---
    # Signing in is mandatory -- there is no config knob to skip it. The window
    # is shown at launch until a session is stored (--no-login is a build-only
    # flag used by packaging/build.py's smoke test).
    # Mirror the cloud-synced settings keys to the signed-in account.
    "account_cloud_sync": True,
    # Last signed-in email, for the account chip before the session loads.
    "account_email": "",
    # Send crash reports and non-fatal error reports to the signed-in account
    # (table public.app_errors). Off = nothing leaves the machine; crashes still
    # go to %APPDATA%\multi-terminal\last-error.log either way.
    "error_reporting": True,
    # Epoch-day (int(time()/86400)) the free-trial countdown banner was last
    # dismissed, so it comes back once per day. Machine-local, not cloud-synced.
    "trial_banner_dismissed_on": 0,

    # --- Updates (see updater.py; only active in a Velopack-installed build) ---
    # Check GitHub for a newer AgentDeck shortly after launch.
    "auto_check_updates": True,
    # Release track: "stable" follows tagged releases, "beta" also takes
    # pre-releases.
    "update_channel": "stable",
    # Opt in to pre-release builds regardless of channel.
    "update_prerelease": False,
    # Epoch seconds of the last successful check (0 = never). Bookkeeping only.
    "last_update_check": 0,
}

CONFIG_SCHEMA: Dict[str, type] = {
    "config_version": int,
    "default_terminal": str,
    "terminal_executable": (str, type(None)),
    "default_shell": str,
    "font_size": int,
    "scrollback": int,
    "layout": str,
    "default_count": int,
    "auto_tile": bool,
    "window_padding": int,
    "margin": int,
    "working_folder": str,
    "recent_folders": list,
    "agent": str,
    "agent_command": str,
    "skip_wizard": bool,
    "show_splash": bool,
    "pretrust_agent_folder": bool,
    "plugins_wire_all_agents": bool,
    "handoff_fork_session": bool,
    "handoff_include_thinking": bool,
    "handoff_max_transcript_chars": int,
    "theme": str,
    "window_width": int,
    "window_height": int,
    "start_maximized": bool,
    "close_terminals_on_exit": bool,
    "remember_sessions": bool,
    "voice_overlay_visible": bool,
    "voice_overlay_x": int,
    "voice_overlay_y": int,
    "voice_model": str,
    "voice_mic_device": (int, str, type(None)),
    "voice_language": str,
    "voice_n_threads": int,
    "voice_beam_size": int,
    "voice_initial_prompt": str,
    "voice_vad_aggressiveness": int,
    "voice_silence_ms": int,
    "voice_min_speech_ms": int,
    "voice_preroll_ms": int,
    "voice_post_processing": bool,
    "voice_mic_autofallback": bool,
    "voice_input_enabled": bool,
    "voice_hint_seen": bool,
    "account_cloud_sync": bool,
    "account_email": str,
    "error_reporting": bool,
    "trial_banner_dismissed_on": int,
    "auto_check_updates": bool,
    "update_channel": str,
    "update_prerelease": bool,
    "last_update_check": int,
}

# Values outside these ranges are clamped rather than rejected: a config with
# font_size 400 should still open a usable window.
CONFIG_RANGES: Dict[str, tuple] = {
    "default_count": (1, 16),
    "font_size": (6, 48),
    "scrollback": (0, 200_000),
    "handoff_max_transcript_chars": (10_000, 5_000_000),
    "window_width": (480, 20_000),
    "window_height": (320, 20_000),
    "voice_n_threads": (0, 32),
    "voice_beam_size": (1, 8),
    "voice_vad_aggressiveness": (0, 3),
    "voice_silence_ms": (120, 2000),
    "voice_min_speech_ms": (0, 1000),
    "voice_preroll_ms": (0, 1000),
}

CONFIG_CHOICES: Dict[str, tuple] = {
    "layout": ("grid", "columns", "rows"),
    "default_shell": ("auto", "pwsh", "powershell", "cmd", "bash"),
    "update_channel": ("stable", "beta"),
    "theme": ("system", "light", "dark"),
    "voice_language": ("auto", "en"),
}

def ensure_dirs() -> None:
    """Create config and cache directories if they don't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _migrate(data: Dict[str, Any]) -> bool:
    """Bring an older config file forward. Returns True if anything changed.

    Only keys whose *meaning* changed are touched -- everything the user
    deliberately set stays as they set it.
    """
    version = data.get("config_version", 1)
    if not isinstance(version, int):
        version = 1

    changed = False

    # 'skip_login' was removed when a signed-in account became mandatory. Drop a
    # stale copy so it doesn't linger in the file (runs regardless of version).
    if data.pop("skip_login", None) is not None:
        changed = True

    if version < 2:
        # v1 wrote these as the size of the launcher dialog. In v2 the window is
        # the terminal panel itself, so an 800x600 carried over from v1 is a
        # stale dialog size, not a chosen terminal size.
        for key in ("window_width", "window_height"):
            if data.get(key) != DEFAULT_CONFIG[key]:
                data[key] = DEFAULT_CONFIG[key]
                changed = True
        print("[INFO] Upgraded config to v2 (window size now sizes the panel).")

    if version < 3:
        # "tiny.en" was the only value the app ever wrote for voice_model, so an
        # untouched copy is the old default, not a deliberate choice -- move it
        # to "auto" (a machine-suitable model). A user who picked tiny.en by
        # hand keeps it only if they also bump config_version, which is fine.
        if data.get("voice_model") == "tiny.en":
            data["voice_model"] = "auto"
            changed = True
        print("[INFO] Upgraded config to v3 (voice_model 'auto').")

    if version != CONFIG_VERSION:
        data["config_version"] = CONFIG_VERSION
        changed = True

    return changed


def load_config() -> Dict[str, Any]:
    """Load configuration from disk, merging with defaults."""
    ensure_dirs()
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError and a UnicodeDecodeError from a
        # file that isn't valid UTF-8 (e.g. hand-edited in a legacy codepage).
        print(f"[WARN] Failed to load config ({exc}), using defaults.")
        return dict(DEFAULT_CONFIG)

    if not isinstance(data, dict):
        print("[WARN] Config file is not an object, using defaults.")
        return dict(DEFAULT_CONFIG)

    if _migrate(data):
        try:
            save_config(data)
        except (OSError, ValueError) as exc:
            print(f"[WARN] Could not write migrated config ({exc}).")

    merged = {**DEFAULT_CONFIG, **data}

    for key, expected_type in CONFIG_SCHEMA.items():
        value = merged.get(key)
        allowed = expected_type if isinstance(expected_type, tuple) else (expected_type,)
        wrong_type = not isinstance(value, expected_type)
        # bool is a subclass of int, so isinstance would let `true` through for
        # any key that accepts int (font_size, voice_mic_device, ...). Reject a
        # bool unless the key is genuinely bool-typed.
        if isinstance(value, bool) and bool not in allowed:
            wrong_type = True
        if wrong_type:
            print(f"[WARN] Config key '{key}' has wrong type. Using default.")
            merged[key] = DEFAULT_CONFIG[key]

    for key, (low, high) in CONFIG_RANGES.items():
        clamped = max(low, min(high, merged[key]))
        if clamped != merged[key]:
            print(f"[WARN] Config key '{key}' out of range; clamped to {clamped}.")
            merged[key] = clamped

    for key, choices in CONFIG_CHOICES.items():
        if merged[key] not in choices:
            print(
                f"[WARN] Config key '{key}' must be one of {', '.join(choices)}. "
                f"Using default."
            )
            merged[key] = DEFAULT_CONFIG[key]

    return merged

def save_config(config: Dict[str, Any]) -> None:
    """Save configuration to disk."""
    ensure_dirs()
    merged = {**DEFAULT_CONFIG, **config}
    # utf-8 explicitly: json.dump(ensure_ascii=False) can emit non-ASCII (a
    # folder path or workspace name with accented / CJK characters), which the
    # platform default codepage cannot always encode.
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
        f.write("\n")

def get_config_path() -> Path:
    """Get the path to the config file."""
    return CONFIG_FILE
