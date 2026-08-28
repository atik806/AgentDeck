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
    "is_installed",
    "install_hint",
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
    ("opencode", "opencode", "opencode"),
    ("gemini", "Gemini CLI", "gemini"),
    ("aider", "Aider", "aider"),
    ("cursor-agent", "Cursor Agent", "cursor-agent"),
]

_LABELS = {key: label for key, label, _cmd in _KNOWN}
_LABELS[PLAIN_KEY] = "Plain shell"
_LABELS[CUSTOM_KEY] = "Custom command"

#: How to install each agent, for the wizard's "you don't have this yet" guide.
#: The docs URL is the source of truth -- install commands drift, so the wizard
#: shows both and leans on "Open guide".
_INSTALL: Dict[str, Dict[str, str]] = {
    "claude": {
        "command": "npm install -g @anthropic-ai/claude-code",
        "docs": "https://docs.claude.com/en/docs/claude-code/setup",
    },
    "codex": {
        "command": "npm install -g @openai/codex",
        "docs": "https://developers.openai.com/codex/cli",
    },
    "opencode": {
        "command": "npm install -g opencode-ai",
        "docs": "https://opencode.ai/docs",
    },
    "gemini": {
        "command": "npm install -g @google/gemini-cli",
        "docs": "https://github.com/google-gemini/gemini-cli",
    },
    "aider": {
        "command": "python -m pip install aider-install && aider-install",
        "docs": "https://aider.chat/docs/install.html",
    },
    "cursor-agent": {
        "command": 'powershell -c "irm https://cursor.com/install.ps1 | iex"',
        "docs": "https://cursor.com/docs/cli",
    },
}


def known_agents() -> List[Tuple[str, str, str]]:
    """Every agent this build knows about, as ``(key, label, command)``."""
    return list(_KNOWN)


def available_agents() -> List[Tuple[str, str, str]]:
    """Installed agents as ``(key, label, command)``, best first.

    Only agents whose executable is on PATH are returned, so the wizard never
    offers one that would just error out in every pane.
    """
    found: List[Tuple[str, str, str]] = []
    for key, label, command in _KNOWN:
        if shutil.which(command):
            found.append((key, label, command))
    return found


def is_installed(key: str) -> bool:
    """Whether agent ``key`` is on PATH right now."""
    for candidate_key, _label, command in _KNOWN:
        if candidate_key == key:
            return bool(shutil.which(command))
    return False


def install_hint(key: str) -> Optional[Dict[str, str]]:
    """``{"command", "docs"}`` for installing agent ``key``, or ``None``."""
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
