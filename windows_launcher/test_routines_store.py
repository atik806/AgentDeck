"""Offline tests for routines_store (Qt-free).

    .venv\\Scripts\\python.exe test_routines_store.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

from routines_store import NEW_WORKSPACE, Routine, RoutinesStore, format_time_12h, schedule_summary

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


tmp = Path(tempfile.mkdtemp()) / "routines.json"


print("[1] format_time_12h / schedule_summary")
check("morning", format_time_12h("08:00") == "8:00 AM")
check("midnight", format_time_12h("00:00") == "12:00 AM")
check("noon", format_time_12h("12:00") == "12:00 PM")
check("afternoon", format_time_12h("17:30") == "5:30 PM")
check("garbage passes through", format_time_12h("nope") == "nope")
check("no days -> Daily", schedule_summary([], "08:00") == "Daily 8:00 AM")
check("all 7 days -> Daily", schedule_summary([0, 1, 2, 3, 4, 5, 6], "08:00") == "Daily 8:00 AM")
check(
    "some days -> abbreviations, Mon-first order",
    schedule_summary([4, 0, 2], "08:00") == "Mon, Wed, Fri 8:00 AM",
)


print("[2] create / persist / reload")
s = RoutinesStore(path=tmp)
check("starts empty", len(s) == 0)
r1 = s.create(name="Morning check", prompt="check PRs", agent_key="claude", time="08:00")
r2 = s.create(name="Evening wrap-up", prompt="summarize the day", time="18:00")
check("two routines", len(s) == 2)
check("file written", tmp.exists())
check("defaults filled in", r2.workspace_target == NEW_WORKSPACE and r2.enabled is True)

s2 = RoutinesStore(path=tmp)
loaded = s2.all()
check("reload sees both", len(loaded) == 2)
check("creation order preserved", loaded[0].id == r1.id and loaded[1].id == r2.id)
check("prompt round-trips", s2.get(r1.id).prompt == "check PRs")
check("display_name falls back", Routine(id="x").display_name == "Untitled routine")


print("[3] update -- editor writes")
time.sleep(0.01)
s2.update(r1.id, name="Morning check-in", days=[0, 2, 4], enabled=False)
reloaded = RoutinesStore(path=tmp).get(r1.id)
check("name persisted", reloaded.name == "Morning check-in")
check("days persisted", reloaded.days == [0, 2, 4])
check("enabled persisted", reloaded.enabled is False)
check("stays at creation position (not reordered)", RoutinesStore(path=tmp).all()[0].id == r1.id)
same = s2.get(r1.id).updated
s2.update(r1.id, name="Morning check-in")  # identical -> no-op
check("no-op update doesn't bump timestamp", s2.get(r1.id).updated == same)
s2.update(r1.id, bogus_field="ignored")
check("unknown field is ignored, doesn't raise", s2.get(r1.id) is not None)


print("[4] mark_run -- scheduler writes, doesn't touch 'updated'")
before_updated = s2.get(r2.id).updated
s2.mark_run(r2.id, "ok")
after = s2.get(r2.id)
check("last_run_at set", after.last_run_at is not None)
check("last_run_status set", after.last_run_status == "ok")
check("mark_run does not bump 'updated'", after.updated == before_updated)
check("persisted", RoutinesStore(path=tmp).get(r2.id).last_run_status == "ok")


print("[5] delete")
check("delete returns True", s2.delete(r1.id) is True)
check("gone from store", s2.get(r1.id) is None)
check("delete missing returns False", s2.delete("nope") is False)
check("persisted", len(RoutinesStore(path=tmp)) == 1)


print("[6] corrupt / missing files are tolerated")
missing = Path(tempfile.mkdtemp()) / "sub" / "routines.json"
check("missing file -> empty", RoutinesStore(path=missing).all() == [])
bad = Path(tempfile.mkdtemp()) / "routines.json"
bad.write_text("{not json", encoding="utf-8")
check("bad json -> empty", RoutinesStore(path=bad).all() == [])
bad.write_text(json.dumps({"routines": "nope"}), encoding="utf-8")
check("wrong shape -> empty", RoutinesStore(path=bad).all() == [])
bad.write_text(json.dumps({"routines": [{"name": "no id here"}]}), encoding="utf-8")
recovered = RoutinesStore(path=bad).all()
check("row without id gets one", len(recovered) == 1 and recovered[0].id.startswith("r_"))
bad.write_text(
    json.dumps({"routines": [{"id": "r_1", "days": [1, "x", 9, 2, 1], "time": "bad"}]}),
    encoding="utf-8",
)
recovered = RoutinesStore(path=bad).all()
check("garbage days filtered + deduped + sorted", recovered[0].days == [1, 2])
check("garbage time falls back to default", recovered[0].time == "08:00")


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
