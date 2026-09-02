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


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
