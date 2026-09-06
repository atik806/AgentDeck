"""Scheduling logic for Routines -- decides which routines are due right now.

Qt-free and stateless-per-call on purpose (same rule as ``agents.py`` /
``entitlements.py``): fed a list of :class:`routines_store.Routine` and a
clock reading, it returns which ones should fire, deduplicated so a caller
that polls every second (``TerminalPanel``'s existing 1 s watchdog) fires each
one exactly once for its matching minute.

**Session-local only** -- a routine fires while AgentDeck happens to be open
and the wall clock hits its scheduled time. There is no catch-up for a time
that passed while the app was closed, and no OS-level wake-up (Task
Scheduler, etc.); see the "Routines" plan this shipped from. If that's ever
wanted, it's a separate, much bigger feature.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from routines_store import Routine

__all__ = ["RoutineScheduler"]


class RoutineScheduler:
    """Call :meth:`due` roughly once a second with the live routine list."""

    def __init__(self):
        #: routine id -> "YYYY-MM-DD HH:MM" it last fired for, so a routine
        #: whose minute is polled many times (every watchdog tick) only fires
        #: once.
        self._fired: "dict[str, str]" = {}

    def due(
        self, routines: Iterable[Routine], now: Optional[datetime] = None
    ) -> "List[Routine]":
        """The routines that should fire right this instant."""
        now = now or datetime.now()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        hhmm = now.strftime("%H:%M")
        weekday = now.weekday()  # Monday = 0 .. Sunday = 6

        fired_now: "List[Routine]" = []
        for routine in routines:
            if not routine.enabled:
                continue
            if routine.time != hhmm:
                continue
            if routine.days and weekday not in routine.days:
                continue
            if self._fired.get(routine.id) == stamp:
                continue
            self._fired[routine.id] = stamp
            fired_now.append(routine)
        return fired_now

    def forget(self, routine_id: str) -> None:
        """Drop dedupe state for a routine that no longer exists. Purely
        tidy -- a stale entry is harmless, it just never matches again."""
        self._fired.pop(routine_id, None)
