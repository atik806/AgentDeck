"""Spoken editing commands parsed from a finished dictation utterance.

Only a *whole* utterance that is nothing but a known phrase counts as a
command -- "send" mid-sentence stays literal text. Table-driven and pure so it
unit-tests without Qt.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

__all__ = ["parse", "ACTIONS"]

ACTIONS = ("submit", "newline", "scratch", "stop")

_PHRASES = {
    "submit":  {"send", "send it", "run that", "run it", "submit", "submit that",
                "go ahead", "execute", "execute that", "enter"},
    "newline": {"new line", "newline", "line break"},
    "scratch": {"scratch that", "delete that", "undo that", "erase that",
                "cancel that"},
    "stop":    {"stop listening", "stop dictation", "stop voice", "never mind",
                "nevermind", "cancel dictation"},
}

_STRIP = re.compile(r"[.!?,\s]+$")
_LEAD = re.compile(r"^[\s]+")


def _norm(text: str) -> str:
    t = (text or "").strip().lower()
    t = _STRIP.sub("", t)
    t = _LEAD.sub("", t)
    return re.sub(r"\s+", " ", t)


def parse(text: str, config: Optional[dict] = None) -> Tuple[Optional[str], str]:
    """Return ``(action, remaining_text)``.

    ``action`` is one of :data:`ACTIONS` when the whole utterance is a command
    phrase, else ``None`` with the original text returned untouched.
    """
    cfg = config or {}
    if not cfg.get("voice_commands_enabled", True):
        return None, text
    key = _norm(text)
    if not key:
        return None, text
    for action, phrases in _PHRASES.items():
        if key in phrases:
            return action, ""
    return None, text
