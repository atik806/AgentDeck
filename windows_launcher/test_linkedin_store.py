"""Offline tests for linkedin_store.py -- the job pipeline.

    .venv\\Scripts\\python.exe test_linkedin_store.py
"""

import sys
import tempfile
from pathlib import Path

from linkedin_store import STATUSES, LinkedInStore

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


def _store(tmp, name="jobs.json"):
    return LinkedInStore(Path(tmp) / name)


_POSTINGS = [
    {"id": "1001", "title": "Senior Python Engineer", "company": "Acme",
     "location": "Remote (EU)", "url": "https://www.linkedin.com/jobs/view/1001/",
     "posted": "2026-09-18", "source": "apify"},
    {"id": "1002", "title": "Backend Engineer", "company": "Globex",
     "url": "https://www.linkedin.com/jobs/view/1002/", "source": "apify"},
]


with tempfile.TemporaryDirectory() as tmp:
    # -----------------------------------------------------------------------
    print("[1] empty store")
    s = _store(tmp)
    check("no jobs", s.all() == [])
    check("unknown id is None", s.get("nope") is None)
    check("counts are zeroed", s.counts() == {k: 0 for k in STATUSES})
    check("known_ids empty", s.known_ids() == set())

    # -----------------------------------------------------------------------
    print("[2] mark_seen -- the dedupe the automation rests on")
    fresh = s.mark_seen(_POSTINGS)
    check("both postings are new the first time", [j["id"] for j in fresh] == ["1001", "1002"])
    check("both stored", s.known_ids() == {"1001", "1002"})
    check("stored as 'seen'", all(j.status == "seen" for j in s.all()))
    check("descriptive fields kept", s.get("1001")["company"] == "Acme")
    check("history seeded", s.get("1001")["history"][0][0] == "seen")

    again = s.mark_seen(_POSTINGS)
    check("a re-run reports NOTHING new (REGRESSION)", again == [])
    check("still two rows", len(s.all()) == 2)

    mixed = s.mark_seen(_POSTINGS + [{"id": "1003", "title": "Platform Engineer"}])
    check("only the genuinely new one comes back", [j["id"] for j in mixed] == ["1003"])

    check("junk without an id is dropped", s.mark_seen([{"title": "no id"}]) == [])
    check("non-dicts are ignored", s.mark_seen(["nope", None, 7]) == [])
    # An id is all the *store* requires: enforcing titles is the provider
    # layer's job (linkedin_jobs._normalise), and a store that silently dropped
    # rows would lose pipeline state rather than filter noise.
    check("a titleless posting still stores", len(s.mark_seen([{"id": "9"}])) == 1)
    check("four rows now", len(s.all()) == 4)

    # a refresh updates the description but not the pipeline
    s.shortlist("1001", 80, "async + postgres")
    s.mark_seen([{"id": "1001", "title": "Staff Python Engineer", "company": "Acme Inc"}])
    check("refresh updates title", s.get("1001")["title"] == "Staff Python Engineer")
    check("refresh does NOT reset status (REGRESSION)", s.get("1001").status == "shortlisted")

    # -----------------------------------------------------------------------
    print("[3] shortlist")
    job = s.shortlist("1002", 91, "exact stack match")
    check("status moved", job.status == "shortlisted")
    check("score stored", job["score"] == 91)
    check("reason stored", job["why"] == "exact stack match")
    check("score is clamped high", s.shortlist("1003", 500)["score"] == 100)
    check("score is clamped low", s.shortlist("1003", -5)["score"] == 0)
    check("a junk score is 0, not a crash", s.shortlist("1003", "banana")["score"] == 0)
    check("unknown id -> None", s.shortlist("nope", 50) is None)

    # -----------------------------------------------------------------------
    print("[4] set_status -- forward only, closed from anywhere")
    check("forward move works", s.set_status("1002", "drafted").status == "drafted")
    check("backwards is ignored, not an error (REGRESSION)",
          s.set_status("1002", "seen").status == "drafted")
    check("forward again", s.set_status("1002", "applied").status == "applied")
    check("closed is reachable from anywhere", s.set_status("1001", "closed").status == "closed")
    check("unknown status -> None", s.set_status("1002", "banana") is None)
    check("unknown id -> None", s.set_status("nope", "applied") is None)
    check("history records the moves",
          [h[0] for h in s.get("1002")["history"]] == ["seen", "shortlisted", "drafted", "applied"])

    # -----------------------------------------------------------------------
    print("[5] queries")
    check("by_status filters", [j["id"] for j in s.by_status("applied")] == ["1002"])
    check("by_status of nothing is empty", s.by_status("replied") == [])
    counts = s.counts()
    check("counts add up", sum(counts.values()) == 4)
    check("counts are per status", counts["applied"] == 1 and counts["closed"] == 1)

    # -----------------------------------------------------------------------
    print("[6] durability")
    reopened = _store(tmp)
    check("a second store reads the same file", len(reopened.all()) == 4)
    check("forget removes one", reopened.forget("1003") and len(reopened.all()) == 3)
    check("forget of an unknown id is fine", reopened.forget("nope"))
    reopened.clear()
    check("clear empties it", reopened.all() == [])

    # -----------------------------------------------------------------------
    print("[7] a corrupt or foreign file reads as empty, never raises")
    bad = Path(tmp) / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    check("corrupt -> empty", LinkedInStore(bad).all() == [])
    bad.write_text('["a list, not an object"]', encoding="utf-8")
    check("foreign shape -> empty", LinkedInStore(bad).all() == [])
    missing = LinkedInStore(Path(tmp) / "nested" / "deep" / "jobs.json")
    check("a missing file -> empty", missing.all() == [])
    check("...and writing creates the tree", missing.mark_seen(_POSTINGS) != [])


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
