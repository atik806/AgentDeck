# Skills

A **skill** is a reusable `SKILL.md` instruction doc — a name, a one-line
"when to use this" description, and a Markdown body — that AgentDeck wires into
every coding agent so they work to your standards without you pasting the same
guidance into every session. An agent can also review and rewrite a skill for
you.

Skills are **Pro** (same tier as Routines / Plugins / Handoff). The nav item is
visible to Free for discoverability; creating / uploading / enabling is gated.

## Where a skill goes

There is no cross-agent "skills" standard, so AgentDeck materializes an enabled
skill two ways (`skills_sync.materialize`):

| Agent | Mechanism |
|---|---|
| **Claude Code** | Native personal skill: `~/.claude/skills/<slug>/SKILL.md` (user scope — every project sees it). Claude auto-discovers and model-invokes it. |
| **Every other agent** | `<workspace folder>/.agentdeck/skills/<slug>.md` plus a marker-delimited block in `<workspace folder>/AGENTS.md` pointing at them. Most agents read `AGENTS.md`. Turn this off with `skills_materialize_agents_md: false`. |

**Never clobbers your own files.** A `~/.claude/skills/<slug>/` that exists
without AgentDeck's `.agentdeck-managed` marker is left untouched; the panel
badges the name clash. A ledger (`%APPDATA%\multi-terminal\skills_state.json`)
records exactly what was written so disabling / deleting a skill — or a plan
lapse — removes only that.

The `AGENTS.md` edit only ever touches the text between:

```
<!-- agentdeck:skills:start -->
...
<!-- agentdeck:skills:end -->
```

## Storage & sync

* **Local (source of truth):** `%APPDATA%\multi-terminal\skills.json` (index +
  bodies), plus editable working copies at `%APPDATA%\multi-terminal\skills\<slug>.md`.
* **Cloud (cross-device, Pro):** `public.skills`, `user_id = auth.uid()` RLS.
  `skills_cloud.SkillsCloud` pulls on launch and pushes (debounced) on every
  change; merge is last-write-wins per `slug` by `updated_at`, deletes are soft
  (`deleted = true`) so they propagate. Free / offline / signed-out → local only.
  Migration: `supabase/migrations/20260908120000_skills.sql`.

There is no team / shared library — each account has its own private set. Use
**Export** / upload a `SKILL.md` to hand one to someone else.

## Improve with agent

The editor's **Improve with agent** button opens a pane where the picked agent
reviews the skill's file (`~/.claude/skills/<slug>/SKILL.md` for Claude, else the
workspace's `.agentdeck/skills/<slug>.md`) and rewrites it in place. AgentDeck
watches that file (the 1 s status watchdog, keyed on a content hash) and
re-imports the agent's edits — bumping `updated`, recording the reviewer,
re-materializing, and pushing to cloud. If the Skills panel is open on that
skill it reloads the editor (unless you have an unsaved edit going).

## Modules

| File | Role |
|---|---|
| `skills_store.py` | Qt-free JSON store (`Skill`, `SkillsStore`), frontmatter parse/render, slug rules, cloud merge. |
| `skills_sync.py` | Materialize / prune / no-clobber, the ledger, working copies, `AGENTS.md` block, `agent_review_target`. Qt-free. |
| `skills_panel.py` | The `SkillsPanel` view + `skill_icon`. |
| `skills_cloud.py` | `SkillsCloud` — the `public.skills` mirror (Qt). |
| `entitlements.skills_enabled` | Pro gate. |
| `terminal_panel.py` | Nav wiring, `_materialize_skills`, `_improve_skill_with_agent`, `_check_skill_watches`. |

Config keys (machine-local): `skills_materialize_agents_md`, `skills_improve_agent`.

Tests: `test_skills_store.py`, `test_skills_sync.py`, `test_skills_panel.py`,
`test_panel.py` §32.
