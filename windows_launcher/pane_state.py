"""Classify what a terminal pane is *doing* right now, for the sidebar badge
and the "needs attention" notifications.

With four to sixteen panes open, the user can't watch them all. The sidebar's
activity dot already answers "is something happening here"; this module answers
the more useful question -- **does this pane need me** -- by folding a handful of
cheap signals into one of five states:

    working          -- output within the last couple of seconds
    awaiting_input   -- output has settled and the tail of the screen looks like
                        a prompt waiting on a keystroke (a y/n, a menu, "Press
                        enter", Claude Code's permission box, ...)
    done             -- an agent that was working has gone quiet and *isn't*
                        waiting on anything -- it finished. Transient: the next
                        poll reports ``idle``.
    error            -- the shell exited (or never spawned)
    idle             -- a plain prompt, or nothing has happened in a long while

Qt-free on purpose (same rule as :mod:`worktree_store`): the classifier is a
pure function of a :class:`PaneSignals` snapshot, so it unit-tests offline with
no widgets. :class:`TerminalPanel` takes the snapshot each second in
``_refresh_status`` and drives both the badge and the toast from the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "WORKING",
    "AWAITING_INPUT",
    "DONE",
    "IDLE",
    "ERROR",
    "ATTENTION_STATES",
    "PaneSignals",
    "classify",
    "looks_like_prompt",
    "state_label",
]

WORKING = "working"
AWAITING_INPUT = "awaiting_input"
DONE = "done"
IDLE = "idle"
ERROR = "error"

#: States that mean "the user should look at this pane". ``done`` is here too --
#: it fires one notification then decays to ``idle``.
ATTENTION_STATES = frozenset({AWAITING_INPUT, DONE, ERROR})

#: Seconds of silence after activity before a working pane counts as ``done``.
#: Long enough that a brief pause mid-task (a tool call, a network round-trip)
#: doesn't read as "finished".
DONE_AFTER_QUIET_S = 6.0

#: A pane must have been *continuously* working at least this long for its going
#: quiet to count as ``done``. Stops an interactive agent's startup banner (a
#: second or two of output, then it sits at its prompt) from firing a spurious
#: "an agent finished" the moment it launches.
MIN_WORK_FOR_DONE_S = 8.0

#: A pane that has been silent longer than this is ``idle`` even if the tail
#: still shows an old prompt -- the user has clearly seen it.
STALE_PROMPT_S = 20.0


# --- prompt detection ------------------------------------------------------
#
# These run against the last few non-blank lines of the *visible* screen once
# output has settled. Ordered roughly most- to least-specific. Kept
# deliberately tight: a false "awaiting_input" nags, so when in doubt this
# returns False and the pane just reads as ``done`` / ``idle``.

_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Claude Code / Codex / generic agent permission + menu boxes.
    re.compile(r"❯\s*\d+\.\s"),                       # "❯ 1. Yes"
    re.compile(r"\bDo you want to (?:proceed|continue|allow)\b", re.I),
    re.compile(r"\b(?:Yes|No), and (?:don't ask|allow)\b", re.I),
    re.compile(r"\bPress\s+(?:enter|return|any key)\b", re.I),
    re.compile(r"\bWaiting for (?:your )?(?:input|approval|confirmation)\b", re.I),
    # Bare yes/no confirmations.
    re.compile(r"[\(\[]\s*[yY](?:es)?\s*/\s*[nN]o?\s*[\)\]]"),   # (y/n) [Y/n]
    re.compile(r"\b(?:continue|overwrite|proceed|are you sure)\??\s*[\(\[]?[yY]/[nN]", re.I),
    re.compile(r"\?\s*[\(\[]y/n[\)\]]\s*$", re.I),
    # An interactive REPL / pager / editor prompt sitting at the bottom.
    re.compile(r"^\s*(?:>>>|\.\.\.|In \[\d+\]:)\s*$"),           # python / ipython
    re.compile(r"^\s*:\s*$"),                                     # less / vim ex
    re.compile(r"--More--|\(END\)"),                              # pager
    # A trailing question mark on its own short line -- last resort.
    re.compile(r"^.{0,70}\?\s*$"),
)

#: Lines that look like a prompt pattern but are almost always noise -- a diff
#: hunk header, a URL, a comment. Checked first; a match here vetoes the line.
_PROMPT_VETO: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?://"),
    re.compile(r"^\s*[#/*]"),            # a comment line
    re.compile(r"^\s*[-+]{3}\s"),        # diff --- / +++
    re.compile(r"^\s*@@ "),              # diff hunk
)


def looks_like_prompt(lines: "list[str]") -> bool:
    """True if the tail of the screen looks like it's waiting on a keystroke.

    ``lines`` is the visible screen bottom-up or top-down -- order doesn't
    matter, blank lines are ignored. Only the last ~4 non-blank lines are
    considered, since that's where a prompt sits.
    """
    tail = [ln.rstrip() for ln in (lines or []) if ln and ln.strip()]
    if not tail:
        return False
    for line in tail[-4:]:
        if any(v.search(line) for v in _PROMPT_VETO):
            continue
        if any(p.search(line) for p in _PROMPT_PATTERNS):
            return True
    return False


# --- classifier -----------------------------------------------------------


@dataclass
class PaneSignals:
    """A cheap once-a-second snapshot of one pane."""

    alive: bool = True
    #: Non-empty when the shell failed to spawn / exited with a reason.
    error: str = ""
    #: ``TerminalView.is_busy()`` -- output within the busy window.
    busy: bool = False
    #: Seconds since the last byte of output (large / ``inf`` if none ever).
    quiet_for: float = float("inf")
    #: Epoch seconds the pane started an agent -- 0 for a plain shell. A plain
    #: shell never raises an ``awaiting_input`` / ``done`` notification.
    agent_started_at: float = 0.0
    #: How long the pane was *continuously* producing output, up to the moment
    #: it last went quiet -- gates ``done`` (see :data:`MIN_WORK_FOR_DONE_S`).
    work_streak: float = 0.0
    #: The visible screen's lines (or just its tail). Only read when settled.
    screen_tail: "list[str]" = field(default_factory=list)


def classify(sig: PaneSignals, previous: Optional[str] = None) -> str:
    """Fold a :class:`PaneSignals` snapshot into one state string.

    ``previous`` is this pane's last classification -- it's what lets a pane
    that *was* working report ``done`` exactly once when it goes quiet.
    """
    if not sig.alive:
        return ERROR
    if sig.busy:
        return WORKING

    has_agent = sig.agent_started_at > 0

    # A settled prompt is worth surfacing the instant output stops -- but only
    # for a little while, after which the user has plainly seen it.
    if (
        has_agent
        and sig.quiet_for < STALE_PROMPT_S
        and looks_like_prompt(sig.screen_tail)
    ):
        return AWAITING_INPUT

    # A brief gap mid-task (a tool call, a network hop) still reads as working.
    if has_agent and sig.quiet_for < DONE_AFTER_QUIET_S:
        return WORKING

    # An agent that was working and is now quiet with nothing to answer has
    # finished. Fires once: next poll ``previous`` is DONE, so this drops to
    # IDLE. Only ``working`` precedes ``done`` -- a stale ``awaiting_input``
    # decays straight to idle (the user was already told). A too-short work
    # streak (a startup banner) decays straight to idle instead of "finished".
    if (
        has_agent
        and previous == WORKING
        and sig.quiet_for >= DONE_AFTER_QUIET_S
        and sig.work_streak >= MIN_WORK_FOR_DONE_S
    ):
        return DONE

    return IDLE


def state_label(state: str) -> str:
    """Human wording for a tooltip / notification title."""
    return {
        WORKING: "Working",
        AWAITING_INPUT: "Waiting for you",
        DONE: "Finished",
        ERROR: "Shell exited",
        IDLE: "Idle",
    }.get(state, state)
