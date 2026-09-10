"""pane_state classifier + prompt detection. Offline, no Qt."""

from __future__ import annotations

import pane_state as ps
from pane_state import PaneSignals, classify

_passed = 0
_failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


print("[1] looks_like_prompt -- positives")
for text in [
    ["Running tests...", "", "❯ 1. Yes", "  2. No"],
    ["writing file", "Do you want to proceed?"],
    ["  Overwrite existing config? (y/n)"],
    ["Press enter to continue"],
    ["deleted 3 files", "Are you sure? [Y/n]"],
    [">>> "],
    ["some long explanation that ends with an actual question?"],
]:
    check(f"prompt in {text[-1]!r}", ps.looks_like_prompt(text) is True)

print("[2] looks_like_prompt -- negatives (no nag)")
for text in [
    ["just some output", "all done", "wrote 12 files"],
    ["see https://example.com/faq?x=1 for details"],
    ["# is this the right approach? probably"],
    ["--- a/file.py", "+++ b/file.py"],
    ["@@ -1,4 +1,4 @@ def f():"],
    [""],
    [],
]:
    check(f"no prompt in {text[:1]}", ps.looks_like_prompt(text) is False)

print("[3] classify -- error / working")
check("dead shell -> error", classify(PaneSignals(alive=False)) == ps.ERROR)
check("dead beats busy", classify(PaneSignals(alive=False, busy=True)) == ps.ERROR)
check("busy -> working", classify(PaneSignals(busy=True, agent_started_at=1.0)) == ps.WORKING)
check(
    "quiet gap under threshold still working",
    classify(PaneSignals(quiet_for=3.0, agent_started_at=1.0), previous=ps.WORKING)
    == ps.WORKING,
)

print("[4] classify -- awaiting_input")
sig = PaneSignals(quiet_for=2.0, agent_started_at=1.0, screen_tail=["❯ 1. Yes", "  2. No"])
check("settled prompt -> awaiting_input", classify(sig, previous=ps.WORKING) == ps.AWAITING_INPUT)
check(
    "no agent -> never awaiting_input",
    classify(PaneSignals(quiet_for=2.0, agent_started_at=0.0, screen_tail=["(y/n)"]))
    == ps.IDLE,
)
check(
    "stale prompt decays to idle",
    classify(
        PaneSignals(quiet_for=99.0, agent_started_at=1.0, screen_tail=["(y/n)"]),
        previous=ps.AWAITING_INPUT,
    )
    == ps.IDLE,
)

print("[5] classify -- done fires once")
after = PaneSignals(
    quiet_for=ps.DONE_AFTER_QUIET_S + 1, agent_started_at=1.0,
    work_streak=ps.MIN_WORK_FOR_DONE_S + 1, screen_tail=["wrote 4 files"],
)
check("working -> done", classify(after, previous=ps.WORKING) == ps.DONE)
check("done -> idle next poll", classify(after, previous=ps.DONE) == ps.IDLE)
check(
    "fresh (previous None) never spuriously done",
    classify(after, previous=None) == ps.IDLE,
)
check(
    "awaiting -> not done (already notified)",
    classify(after, previous=ps.AWAITING_INPUT) == ps.IDLE,
)
short = PaneSignals(
    quiet_for=ps.DONE_AFTER_QUIET_S + 1, agent_started_at=1.0,
    work_streak=2.0, screen_tail=["Welcome to the agent"],
)
check(
    "short work streak (startup banner) -> idle, not done",
    classify(short, previous=ps.WORKING) == ps.IDLE,
)

print("[6] ATTENTION_STATES membership")
check("awaiting is attention", ps.AWAITING_INPUT in ps.ATTENTION_STATES)
check("done is attention", ps.DONE in ps.ATTENTION_STATES)
check("error is attention", ps.ERROR in ps.ATTENTION_STATES)
check("working is not", ps.WORKING not in ps.ATTENTION_STATES)
check("idle is not", ps.IDLE not in ps.ATTENTION_STATES)


print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
