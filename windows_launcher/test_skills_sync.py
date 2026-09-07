"""Offline tests for skills_sync (Qt-free).

Every filesystem path is redirected into a temp sandbox via ADK_AGENT_HOME_DIR
(the ~/.claude base), ADK_SKILLS_DIR (working copies) and ADK_SKILLS_STATE
(the ledger), so nothing touches the real ~/.claude.

    .venv\\Scripts\\python.exe test_skills_sync.py
"""

import sys
import tempfile
from pathlib import Path

sandbox = Path(tempfile.mkdtemp())
home = sandbox / "home"
work = sandbox / "work"
state = sandbox / "skills_state.json"
import os

os.environ["ADK_AGENT_HOME_DIR"] = str(home)
os.environ["ADK_SKILLS_DIR"] = str(work)
os.environ["ADK_SKILLS_STATE"] = str(state)

import skills_sync  # noqa: E402
from skills_store import SkillsStore  # noqa: E402

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


claude = home / ".claude" / "skills"
store = SkillsStore(path=sandbox / "skills.json")
a = store.create("Alpha Skill", "use for A", "body A", enabled=True)
b = store.create("Beta Skill", "use for B", "body B", enabled=True)
c = store.create("Disabled One", "never", "body C", enabled=False)

folder = sandbox / "proj"
folder.mkdir()
(folder / ".git" / "info").mkdir(parents=True)  # make it look like a repo
(folder / "AGENTS.md").write_text("# Project notes\n\nExisting user content.\n", encoding="utf-8")


print("[1] materialize -- claude dirs + working copies")
skills_sync.materialize(store.all(), folders=[str(folder)])
check("working copy for enabled", (work / "alpha-skill.md").exists())
check("working copy for disabled too", (work / "disabled-one.md").exists())
check("claude SKILL.md for enabled a", (claude / "alpha-skill" / "SKILL.md").exists())
check("claude marker written", (claude / "alpha-skill" / ".agentdeck-managed").exists())
check("claude SKILL.md for enabled b", (claude / "beta-skill" / "SKILL.md").exists())
check("no claude dir for disabled", not (claude / "disabled-one").exists())
text = (claude / "alpha-skill" / "SKILL.md").read_text(encoding="utf-8")
check("SKILL.md has frontmatter", text.startswith("---\nname: Alpha Skill\n"))


print("[2] AGENTS.md managed block")
am = (folder / "AGENTS.md").read_text(encoding="utf-8")
check("user content preserved", "Existing user content." in am)
check("start marker", skills_sync.AGENTS_MD_START in am)
check("end marker", skills_sync.AGENTS_MD_END in am)
check("lists enabled skill", "Alpha Skill" in am and "beta-skill.md" in am)
check("does not list disabled", "Disabled One" not in am)
check("folder skill file", (folder / ".agentdeck" / "skills" / "alpha-skill.md").exists())
excl = (folder / ".git" / "info" / "exclude").read_text(encoding="utf-8")
check("git-excluded .agentdeck/", ".agentdeck/" in excl.split())


print("[3] prune -- disable a skill, re-materialize")
store.set_enabled(b.id, False)
skills_sync.materialize(store.all(), folders=[str(folder)])
check("claude dir for now-disabled b removed", not (claude / "beta-skill").exists())
check("claude dir for a still there", (claude / "alpha-skill").exists())
am = (folder / "AGENTS.md").read_text(encoding="utf-8")
check("AGENTS.md no longer lists b", "beta-skill.md" not in am)
check("folder file for b removed", not (folder / ".agentdeck" / "skills" / "beta-skill.md").exists())


print("[4] never clobber the user's own skill dir")
mine = claude / "hand-made"
mine.mkdir(parents=True)
(mine / "SKILL.md").write_text("MY OWN SKILL", encoding="utf-8")
store.create("Hand Made", "clash", "agentdeck body", enabled=True)  # slug 'hand-made'
skills_sync.materialize(store.all(), folders=[str(folder)])
check("user's SKILL.md untouched", (mine / "SKILL.md").read_text(encoding="utf-8") == "MY OWN SKILL")
check("no marker added to user's dir", not (mine / ".agentdeck-managed").exists())


print("[5] write_agents_md=False -> block removed, claude still wired")
skills_sync.materialize(store.all(), folders=[str(folder)], write_agents_md=False)
am = (folder / "AGENTS.md").read_text(encoding="utf-8")
check("managed block gone", skills_sync.AGENTS_MD_START not in am)
check("user content still there", "Existing user content." in am)
check("claude alpha still wired", (claude / "alpha-skill" / "SKILL.md").exists())


print("[6] remove_all -- undo everything we own, keep the user's")
skills_sync.materialize(store.all(), folders=[str(folder)])  # re-add the block
skills_sync.remove_all()
check("our claude dir gone", not (claude / "alpha-skill").exists())
check("user's claude dir kept", (mine / "SKILL.md").exists())
am = (folder / "AGENTS.md").read_text(encoding="utf-8")
check("AGENTS.md block gone", skills_sync.AGENTS_MD_START not in am)
check("AGENTS.md user content kept", "Existing user content." in am)


print("[7] agent_review_target")
sk = store.get(a.id)
p_claude = skills_sync.agent_review_target(sk, str(folder), "claude")
check("claude target is its skills dir", str(p_claude).endswith(str(Path("alpha-skill") / "SKILL.md")))
check("claude target written", Path(p_claude).exists())
p_other = skills_sync.agent_review_target(sk, str(folder), "codex")
check("non-claude target in .agentdeck", ".agentdeck" in str(p_other) and str(p_other).endswith("alpha-skill.md"))
check("non-claude target written", Path(p_other).exists())
parsed = skills_sync.read_markdown_skill(p_other)
check("read_markdown_skill round-trips", parsed is not None and parsed[0] == "Alpha Skill")
p_none = skills_sync.agent_review_target(sk, None, "codex")
check("no folder -> working copy", str(p_none).endswith("alpha-skill.md") and Path(p_none).exists())


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
