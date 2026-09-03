"""Light clean-up of a finished whisper.cpp utterance before it is typed.

whisper.cpp returns text shaped for prose: a leading capital and a trailing
period on every clip. Dropped straight into a shell prompt that trailing "."
corrupts the command, and mid-dictation capitalisation of a continuation is
wrong. This module fixes only what is unambiguous; anything richer (spoken
punctuation, command-word fixups) is opt-in and added in a later phase.

Pure functions, no Qt, no I/O -- safe to unit-test in isolation.
"""

from __future__ import annotations

import re

__all__ = ["apply"]

_WS = re.compile(r"[ \t ]+")
# "one short phrase" = no internal sentence punctuation. If whisper split the
# utterance into multiple sentences we leave its punctuation alone.
_INTERNAL_SENTENCE_END = re.compile(r"[.!?]\s+\S")


def _strip_trailing_period(text: str) -> str:
    stripped = text.rstrip()
    if stripped.endswith(".") and not stripped.endswith(".."):
        if not _INTERNAL_SENTENCE_END.search(stripped):
            return stripped[:-1].rstrip()
    return text


def _capitalize_first(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            if ch.islower():
                return text[:i] + ch.upper() + text[i + 1:]
            return text
        if ch.isalnum():  # a leading digit -> nothing to capitalise
            return text
    return text


def apply(text: str, config: dict | None = None) -> str:
    """Return ``text`` cleaned up, or unchanged if post-processing is off."""
    if text is None:
        return ""
    cfg = config or {}
    if not cfg.get("voice_post_processing", True):
        return text.strip()

    out = _WS.sub(" ", text).strip()
    if not out:
        return ""
    out = _strip_trailing_period(out)
    out = _capitalize_first(out)
    return out
