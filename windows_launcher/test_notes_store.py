"""Offline tests for notes_store (Qt-free).

    .venv\\Scripts\\python.exe test_notes_store.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

from notes_store import Note, NotesStore, derive_title

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


tmp = Path(tempfile.mkdtemp()) / "notes.json"


print("[1] derive_title")
check("explicit title wins", derive_title("body text", "My Title") == "My Title")
check("falls back to first non-empty line", derive_title("\n\n  hello\nworld") == "hello")
check("strips leading markdown hashes", derive_title("## Heading") == "Heading")
check("empty -> Untitled note", derive_title("   \n  ") == "Untitled note")


print("[2] create / persist / reload")
s = NotesStore(path=tmp)
check("starts empty", len(s) == 0)
n1 = s.create(body="first note\nmore detail")
n2 = s.create(body="second")
check("two notes", len(s) == 2)
check("file written", tmp.exists())

s2 = NotesStore(path=tmp)
loaded = s2.all()
check("reload sees both", len(loaded) == 2)
check("newest-updated first", loaded[0].id == n2.id)
check("body round-trips", s2.get(n1.id).body == "first note\nmore detail")
check("display_title derived from body", s2.get(n1.id).display_title == "first note")
check("preview is the 2nd line", s2.get(n1.id).preview == "more detail")


print("[3] update bumps updated + reorders")
time.sleep(0.01)
s2.update(n1.id, body="first note edited")
check("edit persisted", NotesStore(path=tmp).get(n1.id).body == "first note edited")
check("edited note floats to top", s2.all()[0].id == n1.id)
same = s2.get(n1.id).updated
s2.update(n1.id, body="first note edited")  # identical -> no-op
check("no-op update doesn't bump timestamp", s2.get(n1.id).updated == same)
s2.update(n1.id, title="Pinned name")
check("explicit title stored", NotesStore(path=tmp).get(n1.id).title == "Pinned name")


print("[4] delete")
check("delete returns True", s2.delete(n2.id) is True)
check("gone from store", s2.get(n2.id) is None)
check("delete missing returns False", s2.delete("nope") is False)
check("persisted", len(NotesStore(path=tmp)) == 1)


print("[5] corrupt / missing files are tolerated")
missing = Path(tempfile.mkdtemp()) / "sub" / "notes.json"
check("missing file -> empty", NotesStore(path=missing).all() == [])
bad = Path(tempfile.mkdtemp()) / "notes.json"
bad.write_text("{not json", encoding="utf-8")
check("bad json -> empty", NotesStore(path=bad).all() == [])
bad.write_text(json.dumps({"notes": "nope"}), encoding="utf-8")
check("wrong shape -> empty", NotesStore(path=bad).all() == [])
bad.write_text(json.dumps({"notes": [{"body": "no id here"}]}), encoding="utf-8")
recovered = NotesStore(path=bad).all()
check("row without id gets one", len(recovered) == 1 and recovered[0].id.startswith("n_"))


print("[6] Note dataclass basics")
n = Note(id="n_x", body="line one\nline two")
check("preview skips the title line", n.preview == "line two")
check("preview handles title-only note", Note(id="n_y", body="solo").preview == "")
check("word_count", Note(id="n_z", body="one two  three\nfour").word_count == 4)
check("char_count", Note(id="n_z", body="abcd").char_count == 4)


print("[7] pin / colour ordering + persistence")
p = Path(tempfile.mkdtemp()) / "notes.json"
s = NotesStore(path=p)
a = s.create(body="alpha")
time.sleep(0.01)
b = s.create(body="beta")
check("newest first before any pin", s.all()[0].id == b.id)
s.update(a.id, pinned=True)
check("pinned note jumps to top", s.all()[0].id == a.id)
check("pin flag persists", NotesStore(path=p).get(a.id).pinned is True)
same = s.get(a.id).updated
s.update(a.id, pinned=True)  # no-op
check("re-pin is a no-op", s.get(a.id).updated == same)
s.update(a.id, pinned=False)
check("unpin drops it back below the newer note", s.all()[0].id == b.id)
check("toggling pin never bumped updated", s.get(a.id).updated == same)

s.update(b.id, color="green")
check("colour stored", NotesStore(path=p).get(b.id).color == "green")
s.update(b.id, color="bogus")
check("unknown colour ignored", s.get(b.id).color == "green")


print("[8] duplicate + search")
dup = s.duplicate(b.id)
check("duplicate returns a new note", dup is not None and dup.id != b.id)
check("duplicate copies the body", dup.body == "beta")
check("duplicate copies the colour", dup.color == "green")
check("duplicate of a missing id -> None", s.duplicate("nope") is None)

s2 = NotesStore(path=Path(tempfile.mkdtemp()) / "n.json")
s2.create(body="deploy checklist\nbump version")
s2.create(body="grocery list\nmilk", title="Shopping")
check("search matches body", [n.body for n in s2.search("bump")] == ["deploy checklist\nbump version"])
check("search matches explicit title", len(s2.search("shopping")) == 1)
check("empty search returns all", len(s2.search("")) == 2)
check("no match -> empty", s2.search("zzz") == [])


print("[9] v1 files still load")
legacy = Path(tempfile.mkdtemp()) / "notes.json"
legacy.write_text(json.dumps({"version": 1, "notes": [
    {"id": "n_old", "title": "", "body": "old note",
     "created": 1.0, "updated": 2.0}
]}), encoding="utf-8")
lo = NotesStore(path=legacy)
check("legacy note loads", lo.get("n_old").body == "old note")
check("legacy note defaults pinned False", lo.get("n_old").pinned is False)
check("legacy note defaults colour empty", lo.get("n_old").color == "")


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
