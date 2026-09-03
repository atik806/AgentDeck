"""Light clean-up of a finished whisper.cpp utterance before it is typed.

whisper.cpp returns text shaped for prose: a leading capital and a trailing
period on every clip. Dropped straight into a shell prompt that trailing "."
corrupts the command, and mid-dictation capitalisation of a continuation is
wrong. Always-on clean-up fixes only what is unambiguous; spoken punctuation
("period" -> ".") and command-word fixups ("get" -> "git") are opt-in.

Pure functions, no Qt, no I/O -- safe to unit-test in isolation.
"""

from __future__ import annotations

import re

__all__ = ["apply"]

# Spoken punctuation. Multi-word keys before their prefixes.
_SPOKEN_PUNCT = [
    ("question mark", "?"), ("exclamation point", "!"), ("exclamation mark", "!"),
    ("open parenthesis", "("), ("open paren", "("),
    ("close parenthesis", ")"), ("close paren", ")"),
    ("new line", "\n"), ("newline", "\n"),
    ("full stop", "."), ("period", "."), ("comma", ","), ("colon", ":"),
    ("semicolon", ";"), ("dash", "-"), ("hyphen", "-"),
    ("open quote", '"'), ("close quote", '"'), ("quote", '"'),
]
_CLOSE_MARKS = set('.,:;?!)"\n')
# Conservative, line-anchored command-word repairs (experimental, default off).
_COMMAND_FIXUPS = [
    (re.compile(r"^(\s*)get\b"), r"\1git"),
    (re.compile(r"\bpseudo\b", re.I), "sudo"),
    (re.compile(r"\bg[ -]?it[ -]?hub\b", re.I), "GitHub"),
    (re.compile(r"\bNPM\b"), "npm"),
]


def _apply_spoken_punctuation(text: str) -> str:
    for word, sym in _SPOKEN_PUNCT:
        text = re.sub(rf"\s*\b{re.escape(word)}\b\s*",
                      sym if sym in _CLOSE_MARKS else sym + " ",
                      text, flags=re.I)
    text = re.sub(r"\s+([.,:;?!)])", r"\1", text)      # no space before a mark
    text = re.sub(r'([.,:;?!)])(?=[^\s.,:;?!)"])', r"\1 ", text)  # one space after
    text = re.sub(r"\(\s+", "(", text)
    return text.strip()


def _apply_command_fixups(text: str) -> str:
    for pat, repl in _COMMAND_FIXUPS:
        text = pat.sub(repl, text)
    return text

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


def apply(text: str, config: dict | None = None) -> str:
    """Return ``text`` cleaned up according to ``config``.

    Note: no auto-capitalisation -- this feeds a shell prompt, where "Echo" is
    not "echo". Only whitespace and whisper's trailing period are touched
    unconditionally.
    """
    if text is None:
        return ""
    cfg = config or {}

    spoken = cfg.get("voice_spoken_punctuation", False)
    if spoken:
        text = _apply_spoken_punctuation(text)
    if cfg.get("voice_command_fixups", False):
        text = _apply_command_fixups(text)

    if not cfg.get("voice_post_processing", True):
        return text.strip()

    # collapse runs of spaces per line but keep real newlines from "new line"
    out = "\n".join(_WS.sub(" ", ln).strip() for ln in text.split("\n")).strip()
    if not out:
        return ""
    # With spoken punctuation on, a trailing "." was asked for -- keep it.
    return out if spoken else _strip_trailing_period(out)
