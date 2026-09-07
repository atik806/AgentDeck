"""Offline tests for skills_store (Qt-free).

    .venv\\Scripts\\python.exe test_skills_store.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

from skills_store import (
    Skill,
    SkillsStore,
    parse_frontmatter,
    render_skill_markdown,
    slugify,
)

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


tmp = Path(tempfile.mkdtemp()) / "skills.json"


print("[1] slugify")
check("basic", slugify("PR Review Checklist") == "pr-review-checklist")
check("punctuation collapses", slugify("  Deploy!! (v2) ") == "deploy-v2")
check("empty -> fallback", slugify("   ") == "skill")
check("all punctuation -> fallback", slugify("!!!") == "skill")
check("truncated", len(slugify("x" * 200)) <= 48)


print("[2] frontmatter parse / render")
meta, body = parse_frontmatter("---\nname: foo\ndescription: when to use\n---\nHello\nWorld")
check("name parsed", meta.get("name") == "foo")
check("description parsed", meta.get("description") == "when to use")
check("body after fence", body == "Hello\nWorld")
meta, body = parse_frontmatter("no frontmatter here")
check("no fence -> empty meta", meta == {})
check("no fence -> body unchanged", body == "no frontmatter here")
meta, body = parse_frontmatter("﻿---\nname: bom\n---\nbody")
check("BOM + leading fence tolerated", meta.get("name") == "bom" and body == "body")
meta, body = parse_frontmatter('---\nname: "quoted"\n---\n')
check("quotes stripped", meta.get("name") == "quoted")
rendered = render_skill_markdown("My Skill", "Use it\nsometimes", "Do the thing")
check("render has frontmatter", rendered.startswith("---\nname: My Skill\n"))
check("render flattens newlines in description", "description: Use it sometimes" in rendered)
re_meta, re_body = parse_frontmatter(rendered)
check("render round-trips", re_meta["name"] == "My Skill" and re_body.strip() == "Do the thing")


print("[3] create / persist / reload")
s = SkillsStore(path=tmp)
check("starts empty", len(s) == 0)
a = s.create("Code Review", "Use when reviewing a diff", "Check tests.")
b = s.create("Code Review", "dup name", "other")  # slug collision
check("two skills", len(s) == 2)
check("first slug", a.slug == "code-review")
check("collision slug bumped", b.slug == "code-review-2")
check("file written", tmp.exists())
check("name defaults to slug when blank", s.create("").name == "skill")

s2 = SkillsStore(path=tmp)
loaded = s2.all()
check("reload sees all three", len(loaded) == 3)
check("creation order preserved", loaded[0].id == a.id)
check("body round-trips", s2.get(a.id).body == "Check tests.")


print("[4] update -- content bumps 'updated', unknown fields ignored")
time.sleep(0.01)
before = s2.get(a.id).updated
s2.update(a.id, description="Use when reviewing a pull request", bogus="x")
after = s2.get(a.id)
check("description persisted", after.description == "Use when reviewing a pull request")
check("updated bumped", after.updated > before)
check("no-op update keeps timestamp", (
    s2.update(a.id, description="Use when reviewing a pull request").updated == after.updated
))
check("newlines flattened in description", (
    s2.update(a.id, description="a\nb").description == "a b"
))
check("persisted across reload", SkillsStore(path=tmp).get(a.id).description == "a b")


print("[5] enable toggle + delete")
s2.set_enabled(b.id, False)
check("disabled persisted", SkillsStore(path=tmp).get(b.id).enabled is False)
check("enabled() filters", all(sk.enabled for sk in s2.enabled()))
check("delete returns True", s2.delete(b.id) is True)
check("gone", s2.get(b.id) is None)
check("delete missing -> False", s2.delete("nope") is False)


print("[6] import_markdown")
s3 = SkillsStore(path=Path(tempfile.mkdtemp()) / "sk.json")
skill = s3.import_markdown(
    "---\nname: Release Steps\ndescription: Use when cutting a release\n---\n"
    "1. bump version\n2. push tag",
    fallback_name="ignored",
)
check("name from frontmatter", skill.name == "Release Steps")
check("slug from name", skill.slug == "release-steps")
check("source is upload", skill.source == "upload")
check("body kept", "push tag" in skill.body)
skill2 = s3.import_markdown("# Heading Title\n\nsome body", fallback_name="fname")
check("name falls back to first heading", skill2.name == "Heading Title")
skill3 = s3.import_markdown("plain body, no heading", fallback_name="the-file")
check("name falls back to filename", skill3.name == "the-file")


print("[7] sha changes with content")
sk = Skill(id="x", name="n", description="d", body="one")
sha1 = sk.sha
sk.body = "two"
check("sha moved with body", sk.sha != sha1)


print("[8] corrupt / missing files tolerated")
missing = Path(tempfile.mkdtemp()) / "sub" / "skills.json"
check("missing -> empty", SkillsStore(path=missing).all() == [])
bad = Path(tempfile.mkdtemp()) / "skills.json"
bad.write_text("{not json", encoding="utf-8")
check("bad json -> empty", SkillsStore(path=bad).all() == [])
bad.write_text(json.dumps({"skills": "nope"}), encoding="utf-8")
check("wrong shape -> empty", SkillsStore(path=bad).all() == [])
bad.write_text(json.dumps({"skills": [{"name": "no id"}, {"name": "no id"}]}), encoding="utf-8")
rec = SkillsStore(path=bad).all()
check("rows without id get one", len(rec) == 2 and all(r.id.startswith("sk_") for r in rec))
check("duplicate slugs de-duped on load", rec[0].slug != rec[1].slug)


print("[9] cloud merge -- last-write-wins by slug, tombstones delete")
cs = SkillsStore(path=Path(tempfile.mkdtemp()) / "c.json")
local = cs.create("Alpha", "local desc", "local body")
old = cs.get(local.id).updated
changed = cs.merge_cloud([
    {"slug": "alpha", "name": "Alpha", "description": "newer desc",
     "body": "newer body", "enabled": True, "updated_at": old + 100},
    {"slug": "beta", "name": "Beta", "description": "from cloud",
     "body": "b", "enabled": True, "updated_at": old + 5},
])
check("merge reports change", changed is True)
check("newer remote wins", cs.get_by_slug("alpha").body == "newer body")
check("new remote skill added", cs.get_by_slug("beta") is not None)
# An older remote copy must not clobber a newer local edit.
cs.update(cs.get_by_slug("alpha").id, body="local wins now")
local_now = cs.get_by_slug("alpha").updated
cs.merge_cloud([{"slug": "alpha", "name": "Alpha", "description": "stale",
                 "body": "stale remote", "enabled": True, "updated_at": local_now - 100}])
check("older remote ignored", cs.get_by_slug("alpha").body == "local wins now")
cs.merge_cloud([{"slug": "beta", "deleted": True, "updated_at": old + 999}])
check("tombstone deletes locally", cs.get_by_slug("beta") is None)
rows = cs.cloud_rows()
check("cloud_rows ISO updated_at", isinstance(rows[0]["updated_at"], str) and "T" in rows[0]["updated_at"])


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
