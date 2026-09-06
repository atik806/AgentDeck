"""Offline tests for routine_scheduler (Qt-free).

    .venv\\Scripts\\python.exe test_routine_scheduler.py
"""

import sys
from datetime import datetime

from routine_scheduler import RoutineScheduler
from routines_store import Routine

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


def r(id="r_1", days=None, time="08:00", enabled=True):
    return Routine(id=id, name="test", prompt="do it", days=days or [], time=time, enabled=enabled)


# 2026-09-07 is a Monday.
MON_0800 = datetime(2026, 9, 7, 8, 0)
MON_0801 = datetime(2026, 9, 7, 8, 1)
TUE_0800 = datetime(2026, 9, 8, 8, 0)


print("[1] daily routine fires at its time")
# One shared instance per assertion -- Routine's created/updated default via
# time.time(), so two freshly-built routines are never == each other.
check("fires at 08:00", [x.id for x in RoutineScheduler().due([r()], MON_0800)] == ["r_1"])
check("doesn't fire at 08:01", RoutineScheduler().due([r()], MON_0801) == [])
check("doesn't fire at 07:59", RoutineScheduler().due([r()], datetime(2026, 9, 7, 7, 59)) == [])


print("[2] dedupe -- one fire per matching minute, across repeated polls")
s = RoutineScheduler()
routine = r(id="r_dedupe")
first = s.due([routine], MON_0800)
second = s.due([routine], MON_0800)  # same minute, polled again (1 s watchdog)
check("fires once", len(first) == 1)
check("second poll in the same minute is suppressed", second == [])
next_day = s.due([routine], TUE_0800)
check("fires again on a later day at the same time", len(next_day) == 1)


print("[3] weekday filter")
mon_wed_fri = r(id="r_mwf", days=[0, 2, 4])  # Mon, Wed, Fri
s = RoutineScheduler()
check("fires on Monday", len(s.due([mon_wed_fri], MON_0800)) == 1)
check("does not fire on Tuesday", s.due([mon_wed_fri], TUE_0800) == [])
wed = datetime(2026, 9, 9, 8, 0)
check("fires on Wednesday", len(s.due([mon_wed_fri], wed)) == 1)


print("[4] disabled routines never fire")
s = RoutineScheduler()
check("disabled -> no fire", s.due([r(enabled=False)], MON_0800) == [])


print("[5] multiple routines, independent dedupe")
s = RoutineScheduler()
a, b = r(id="a"), r(id="b")
due = s.due([a, b], MON_0800)
check("both fire", {x.id for x in due} == {"a", "b"})
check("re-polling the same minute fires neither", s.due([a, b], MON_0800) == [])
check("a later minute for just one still respects dedupe for both independently",
      s.due([a], MON_0801) == [] and s.due([b], MON_0801) == [])


print("[6] forget() clears dedupe state (no observable effect on due(), just tidy)")
s = RoutineScheduler()
routine = r(id="r_forget")
s.due([routine], MON_0800)
s.forget("r_forget")
check("forgetting a routine doesn't crash / no-ops for an unknown id", s.forget("nope") is None)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
