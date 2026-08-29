"""Coding-agent discovery -- the CLI the setup wizard runs in each terminal.

Same shape as ``pty_backend.available_shells`` / ``resolve_shell``: probe PATH
for the agents we know about and only offer the ones that are actually
installed. Deliberately Qt-free so it can be unit-tested on its own.

An "agent" here is just a command typed at the shell once it comes up (see
``TerminalView`` / ``startup_command``) -- ``claude``, ``codex``, ``opencode``,
… -- plus two always-present pseudo-entries: "Plain shell" (run nothing) and
"Custom command…" (run whatever the user types).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = [
    "available_agents",
    "known_agents",
    "all_agents",
    "is_installed",
    "install_hint",
    "refresh_path",
    "resolve_agent",
    "agent_label",
    "is_claude_command",
    "pretrust_folder",
    "PLAIN_KEY",
    "CUSTOM_KEY",
]

PLAIN_KEY = "none"
CUSTOM_KEY = "custom"

#: (key, label, command). ``key`` is what we persist; ``command`` is what gets
#: typed at the prompt. Ordered best-known first.
_KNOWN: List[Tuple[str, str, str]] = [
    ("claude", "Claude Code", "claude"),
    ("codex", "Codex", "codex"),
    ("copilot", "GitHub Copilot CLI", "copilot"),
    ("gemini", "Gemini CLI", "gemini"),
    ("cursor-agent", "Cursor Agent", "cursor-agent"),
    ("opencode", "opencode", "opencode"),
    ("amp", "Amp", "amp"),
    ("antigravity", "Antigravity CLI", "agy"),
    ("qwen", "Qwen Code", "qwen"),
    ("crush", "Crush", "crush"),
    ("aider", "Aider", "aider"),
    ("goose", "Goose", "goose"),
]

_LABELS = {key: label for key, label, _cmd in _KNOWN}
_LABELS[PLAIN_KEY] = "Plain shell"
_LABELS[CUSTOM_KEY] = "Custom command"

#: How to install each agent, for the "you don't have this yet" panel.
#: ``docs`` is the source of truth -- install commands drift, so the panel shows
#: both and leans on "Open guide". ``note`` is an optional extra caveat.
_INSTALL: Dict[str, Dict[str, str]] = {
    "claude": {
        "command": "npm install -g @anthropic-ai/claude-code",
        "docs": "https://docs.claude.com/en/docs/claude-code/setup",
    },
    "codex": {
        "command": "npm install -g @openai/codex",
        "docs": "https://developers.openai.com/codex/cli",
    },
    "copilot": {
        "command": "npm install -g @github/copilot",
        "docs": "https://docs.github.com/copilot/how-tos/set-up/install-copilot-cli",
        "note": "Needs Node.js 22+ and a GitHub Copilot subscription.",
    },
    "gemini": {
        "command": "npm install -g @google/gemini-cli",
        "docs": "https://github.com/google-gemini/gemini-cli",
    },
    "cursor-agent": {
        "command": 'powershell -c "irm https://cursor.com/install?win32=true | iex"',
        "docs": "https://cursor.com/docs/cli/installation",
        "note": "Adds itself to PATH -- click Re-check, or restart AgentDeck.",
    },
    "opencode": {
        "command": "npm install -g opencode-ai",
        "docs": "https://opencode.ai/docs",
    },
    "amp": {
        "command": "npm install -g @ampcode/cli",
        "docs": "https://ampcode.com/manual",
    },
    "antigravity": {
        "command": 'powershell -c "irm https://antigravity.google/cli/install.ps1 | iex"',
        "docs": "https://antigravity.google/docs/cli/install",
        "note": "Installs 'agy' to %LOCALAPPDATA%\\agy\\bin -- click Re-check, "
                "or restart AgentDeck.",
    },
    "qwen": {
        "command": "npm install -g @qwen-code/qwen-code",
        "docs": "https://github.com/QwenLM/qwen-code",
        "note": "Needs Node.js 22+.",
    },
    "crush": {
        "command": "npm install -g @charmland/crush",
        "docs": "https://github.com/charmbracelet/crush",
    },
    "aider": {
        "command": "python -m pip install aider-install && aider-install",
        "docs": "https://aider.chat/docs/install.html",
    },
    "goose": {
        "command": 'powershell -c "irm https://raw.githubusercontent.com/block/'
                   'goose/main/download_cli.ps1 | iex"',
        "docs": "https://block.github.io/goose/docs/getting-started/installation/",
        "note": "On Windows, Goose works best from Git Bash.",
    },
}


def known_agents() -> List[Tuple[str, str, str]]:
    """Every agent this build knows about, as ``(key, label, command)``."""
    return list(_KNOWN)


def all_agents() -> List[Tuple[str, str, str, bool]]:
    """Every known agent as ``(key, label, command, installed)``, best first.

    Unlike :func:`available_agents` this keeps agents that are not on PATH -- the
    picker shows them too, with install instructions.
    """
    return [(k, lbl, cmd, bool(shutil.which(cmd))) for k, lbl, cmd in _KNOWN]


def available_agents() -> List[Tuple[str, str, str]]:
    """Installed agents as ``(key, label, command)``, best first."""
    return [(k, lbl, cmd) for k, lbl, cmd, ok in all_agents() if ok]


def is_installed(key: str) -> bool:
    """Whether agent ``key`` is on PATH right now."""
    for candidate_key, _label, command in _KNOWN:
        if candidate_key == key:
            return bool(shutil.which(command))
    return False


def refresh_path() -> None:
    """Re-read the user + machine PATH from the registry into ``os.environ``.

    ``os.environ['PATH']`` is snapshotted when the process starts, so an agent
    installed *while AgentDeck is open* -- especially one whose installer adds a
    new PATH entry (cursor-agent, antigravity) -- is invisible to
    :func:`shutil.which` until a restart. Calling this first lets the picker's
    "Re-check" button find it. Windows only; best-effort, never raises.
    """
    if os.name != "nt":
        return
    try:
        import winreg  # noqa: PLC0415 - platform-specific
    except Exception:  # noqa: BLE001
        return

    parts: List[str] = []
    for root, sub in (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ):
        try:
            with winreg.OpenKey(root, sub) as key:
                value, _kind = winreg.QueryValueEx(key, "Path")
            expanded = os.path.expandvars(value)
            parts += [p for p in expanded.split(os.pathsep) if p]
        except OSError:
            continue

    # Keep anything the current process already had that the registry doesn't
    # know about (venv Scripts dir, etc.), appended after the registry entries.
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p and p not in parts:
            parts.append(p)

    if parts:
        os.environ["PATH"] = os.pathsep.join(parts)


def install_hint(key: str) -> Optional[Dict[str, str]]:
    """``{"command", "docs"[, "note"]}`` for installing agent ``key``, or ``None``."""
    hint = _INSTALL.get(key)
    return dict(hint) if hint else None


def resolve_agent(key: str, custom: str = "") -> str:
    """The command to run for ``key``.

    - ``"none"`` (or unknown / not installed) → ``""`` (plain shell)
    - ``"custom"`` → ``custom`` trimmed
    - a known key → its command, but only if still on PATH
    """
    if not key or key == PLAIN_KEY:
        return ""
    if key == CUSTOM_KEY:
        return custom.strip()
    for candidate_key, _label, command in _KNOWN:
        if candidate_key == key:
            return command if shutil.which(command) else ""
    return ""


def agent_label(key: str) -> str:
    """A human name for a persisted key (falls back to the key itself)."""
    return _LABELS.get(key, key or "Plain shell")


# ---------------------------------------------------------------------------
# Claude Code: pre-accept the folder-trust prompt
# ---------------------------------------------------------------------------
#
# Claude Code asks "Is this a project you trust?" the first time it opens a
# folder. Auto-launching it in every pane would leave that prompt sitting in
# each one. The answer lives in ~/.claude.json under
# projects[<path>].hasTrustDialogAccepted -- so pre-set it for the folder the
# user just picked in the wizard, and Claude opens straight into the session.

_CLAUDE_CONFIG = Path.home() / ".claude.json"


def is_claude_command(command: str) -> bool:
    """True if ``command`` runs the Claude Code CLI (``claude`` / ``claude …``)."""
    if not command or not command.strip():
        return False
    first = command.strip().split()[0].strip('"').strip("'")
    return Path(first).stem.lower() == "claude"


def _path_forms(folder: str) -> set[str]:
    """The key spellings Claude Code might store a project under on this OS."""
    folder = folder.strip().rstrip("\\/")
    if not folder:
        return set()
    forms = {folder, folder.replace("\\", "/")}
    try:
        forms.add(str(Path(folder)))
        forms.add(str(Path(folder).resolve()))
        forms.add(str(Path(folder)).replace("\\", "/"))
    except Exception:
        pass
    return {f for f in forms if f}


def pretrust_folder(command: str, folder: str) -> bool:
    """Best-effort: mark ``folder`` trusted in ``~/.claude.json`` so an
    auto-launched ``claude`` skips its folder-trust prompt.

    A no-op unless ``command`` is a Claude Code command. Never raises; returns
    ``True`` only if it actually changed the file.
    """
    if not is_claude_command(command):
        return False
    forms = _path_forms(folder)
    if not forms:
        return False

    # Never pre-trust a folder that ships its own Claude Code / MCP config: those
    # files can carry hooks and pre-approved tools, so the trust prompt is
    # exactly the check the user should get to make by hand.
    try:
        root = Path(folder)
        risky = (
            root / ".claude" / "settings.json",
            root / ".claude" / "settings.local.json",
            root / ".mcp.json",
            root / ".claude.json",
        )
        if any(p.exists() for p in risky):
            return False
    except OSError:
        return False

    try:
        cfg: dict = {}
        if _CLAUDE_CONFIG.exists():
            text = _CLAUDE_CONFIG.read_text(encoding="utf-8")
            cfg = json.loads(text) if text.strip() else {}
        if not isinstance(cfg, dict):
            return False

        projects = cfg.setdefault("projects", {})
        if not isinstance(projects, dict):
            return False

        changed = False
        for key in forms:
            entry = projects.get(key)
            if not isinstance(entry, dict):
                entry = {}
                projects[key] = entry
            if entry.get("hasTrustDialogAccepted") is not True:
                entry["hasTrustDialogAccepted"] = True
                changed = True
            entry.setdefault("allowedTools", [])

        if changed:
            tmp = _CLAUDE_CONFIG.with_name(_CLAUDE_CONFIG.name + ".tmp")
            tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            os.replace(tmp, _CLAUDE_CONFIG)
        return changed
    except Exception:
        return False
