"""Put enabled skills where each coding agent will actually find them.

:mod:`skills_store` owns *what* a skill is (name / description / body, in one
JSON file). This module owns *where it goes on disk*:

* **Claude Code** discovers personal skills natively -- one dir per skill under
  ``~/.claude/skills/<slug>/SKILL.md`` (user scope, so every project sees it).
* **Every other agent** (codex, gemini, opencode, amp, ...) reads ``AGENTS.md``
  from the working folder. So for each wired workspace folder we drop the skill
  bodies at ``<folder>/.agentdeck/skills/<slug>.md`` and maintain a
  marker-delimited block in ``<folder>/AGENTS.md`` pointing at them.

Plus a set of **working copies** at ``<config>/skills/<slug>.md`` -- a stable
``SKILL.md`` on disk for the panel's "Open file location" and for the
"Improve with agent" review loop to hand an agent and then re-read.

Safety: a ``~/.claude/skills/<slug>/`` that exists **without** our
``.agentdeck-managed`` marker is the user's own -- we never touch it. A small
ledger (``<config>/skills_state.json``) records exactly what we wrote so
disable / delete / plan-lapse removes only that.

Qt-free. Best-effort throughout -- a read-only disk or a missing ``~/.claude``
must never raise into the app.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional

import mcp_io  # for locked() -- serialise ledger + AGENTS.md writes

__all__ = [
    "claude_skills_dir",
    "working_dir",
    "working_copy_path",
    "write_working_copies",
    "read_markdown_skill",
    "materialize",
    "remove_all",
    "agent_review_target",
    "SkillsLedger",
    "AGENTS_MD_START",
    "AGENTS_MD_END",
]

AGENTS_MD_START = "<!-- agentdeck:skills:start -->"
AGENTS_MD_END = "<!-- agentdeck:skills:end -->"

#: Dropped inside each skill dir we create under ~/.claude/skills, so a later
#: run knows the dir is ours to update or prune (vs. one the user made).
_MARKER = ".agentdeck-managed"

_AGENTDECK_DIR = ".agentdeck"


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def _home() -> Path:
    """User home. ``ADK_AGENT_HOME_DIR`` redirects it (shared with
    ``agent_sessions`` so a test can sandbox every agent path at once)."""
    override = os.environ.get("ADK_AGENT_HOME_DIR")
    return Path(override) if override else Path.home()


def claude_skills_dir() -> Path:
    """``~/.claude/skills`` (not created here)."""
    return _home() / ".claude" / "skills"


def _config_dir() -> Path:
    try:
        from config import CONFIG_DIR

        return Path(CONFIG_DIR)
    except Exception:  # noqa: BLE001
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "multi-terminal"


def working_dir() -> Path:
    """Where the ``<slug>.md`` working copies live. ``ADK_SKILLS_DIR`` overrides
    (tests point it at a temp dir)."""
    override = os.environ.get("ADK_SKILLS_DIR")
    return Path(override) if override else _config_dir() / "skills"


def working_copy_path(slug: str) -> Path:
    return working_dir() / f"{slug}.md"


def _ledger_path() -> Path:
    override = os.environ.get("ADK_SKILLS_STATE")
    return Path(override) if override else _config_dir() / "skills_state.json"


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

class SkillsLedger:
    """``skills_state.json``:
    ``{"claude": ["slug", ...], "folders": {"<abs folder>": ["slug", ...]}}``.

    Best-effort, never raises. The record of what :func:`remove_all` /
    :func:`materialize`'s prune step should undo.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else _ledger_path()

    def _read(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
            data = json.loads(text) if text.strip() else {}
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        data.setdefault("claude", [])
        data.setdefault("folders", {})
        if not isinstance(data["claude"], list):
            data["claude"] = []
        if not isinstance(data["folders"], dict):
            data["folders"] = {}
        return data

    def _write(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.adk{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def claude_slugs(self) -> "list[str]":
        return list(self._read().get("claude", []))

    def folder_slugs(self, folder: str) -> "list[str]":
        return list(self._read().get("folders", {}).get(str(folder), []))

    def all_folders(self) -> "list[str]":
        return list(self._read().get("folders", {}).keys())

    def set_claude(self, slugs: Iterable[str]) -> None:
        with mcp_io.locked(self.path):
            data = self._read()
            data["claude"] = sorted(set(slugs))
            self._write(data)

    def set_folder(self, folder: str, slugs: Iterable[str]) -> None:
        with mcp_io.locked(self.path):
            data = self._read()
            slugs = sorted(set(slugs))
            if slugs:
                data["folders"][str(folder)] = slugs
            else:
                data["folders"].pop(str(folder), None)
            self._write(data)

    def clear(self) -> None:
        with mcp_io.locked(self.path):
            self._write({"claude": [], "folders": {}})


# ---------------------------------------------------------------------------
# Working copies  (<config>/skills/<slug>.md)
# ---------------------------------------------------------------------------

def write_working_copies(skills) -> None:
    """Sync ``<config>/skills/`` to ``skills`` -- one ``<slug>.md`` per skill,
    stale files removed. Best-effort."""
    d = working_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    want = {}
    for s in skills:
        want[f"{s.slug}.md"] = s.to_markdown()
    for name, text in want.items():
        _atomic_write(d / name, text)
    try:
        for existing in d.glob("*.md"):
            if existing.name not in want:
                existing.unlink(missing_ok=True)
    except OSError:
        pass


def read_markdown_skill(path: str | Path) -> "Optional[tuple[str, str, str]]":
    """Parse a ``SKILL.md`` file -> ``(name, description, body)``. ``None`` if
    the file can't be read. Used by the "Improve with agent" re-import."""
    from skills_store import parse_frontmatter

    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    meta, body = parse_frontmatter(text)
    return meta.get("name", "").strip(), meta.get("description", "").strip(), body.strip()


# ---------------------------------------------------------------------------
# Materialize / prune
# ---------------------------------------------------------------------------

def materialize(
    skills,
    *,
    folders: Iterable[str] = (),
    write_agents_md: bool = True,
    ledger: Optional[SkillsLedger] = None,
) -> None:
    """Put the enabled skills where agents will find them, and prune anything
    we previously wrote that is no longer enabled. Never raises.

    ``skills`` is the whole library (enabled + disabled); ``folders`` are the
    workspace folders to wire the ``AGENTS.md`` fallback into.
    """
    ledger = ledger or SkillsLedger()
    skills = list(skills)
    enabled = [s for s in skills if s.enabled]
    enabled_slugs = {s.slug for s in enabled}

    # Working copies mirror the *whole* library (so a disabled skill's file is
    # still there to edit).
    write_working_copies(skills)

    _materialize_claude(enabled, enabled_slugs, ledger)

    for folder in folders:
        if folder:
            _materialize_folder(folder, enabled, enabled_slugs, write_agents_md, ledger)


def _materialize_claude(enabled, enabled_slugs, ledger: SkillsLedger) -> None:
    base = claude_skills_dir()
    prev = set(ledger.claude_slugs())
    written: set[str] = set()
    for s in enabled:
        dest = base / s.slug
        marker = dest / _MARKER
        if dest.exists() and not marker.exists():
            # The user's own skill of the same name -- leave it alone.
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            _atomic_write(dest / "SKILL.md", s.to_markdown())
            marker.write_text("managed by AgentDeck\n", encoding="utf-8")
            written.add(s.slug)
        except OSError:
            continue
    # Prune skills we used to own that are gone / disabled.
    for slug in prev - enabled_slugs:
        _remove_claude_skill(base / slug)
    ledger.set_claude(written | (prev & enabled_slugs))


def _remove_claude_skill(dest: Path) -> None:
    if not (dest / _MARKER).exists():
        return
    try:
        for child in dest.iterdir():
            child.unlink(missing_ok=True)
        dest.rmdir()
    except OSError:
        pass


def _materialize_folder(
    folder: str, enabled, enabled_slugs, write_agents_md: bool, ledger: SkillsLedger
) -> None:
    root = Path(folder)
    if not root.is_dir():
        return
    skills_dir = root / _AGENTDECK_DIR / "skills"
    prev = set(ledger.folder_slugs(folder))
    written: set[str] = set()
    try:
        skills_dir.mkdir(parents=True, exist_ok=True)
        for s in enabled:
            _atomic_write(skills_dir / f"{s.slug}.md", s.to_markdown())
            written.add(s.slug)
        for slug in prev - enabled_slugs:
            (skills_dir / f"{slug}.md").unlink(missing_ok=True)
    except OSError:
        pass

    _git_exclude(root, f"{_AGENTDECK_DIR}/")

    # write_agents_md False -> also strip any block we wrote before (the user
    # turned the setting off). Only ever touches text between our markers.
    block = _agents_block(enabled) if (write_agents_md and enabled) else None
    _rewrite_agents_block(root / "AGENTS.md", block)
    ledger.set_folder(folder, written)


def _agents_block(enabled) -> str:
    lines = [
        AGENTS_MD_START,
        "## Skills (managed by AgentDeck)",
        "",
        "Read the linked file and follow it when the task matches its description.",
        "",
    ]
    for s in enabled:
        desc = (s.description or "").strip() or "(no description)"
        lines.append(f"- **{s.display_name}** — {desc}")
        lines.append(f"  → `{_AGENTDECK_DIR}/skills/{s.slug}.md`")
    lines.append(AGENTS_MD_END)
    return "\n".join(lines)


def _rewrite_agents_block(path: Path, block: Optional[str]) -> None:
    """Insert / replace / remove the managed block in ``AGENTS.md``. Only ever
    touches text between the markers; the rest of the file is the user's."""
    with mcp_io.locked(path):
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
        except (OSError, UnicodeDecodeError):
            return
        start = text.find(AGENTS_MD_START)
        end = text.find(AGENTS_MD_END)
        has_block = start != -1 and end != -1 and end > start

        if has_block:
            end_full = end + len(AGENTS_MD_END)
            before, after = text[:start], text[end_full:]
            if block is None:
                # Drop the block and collapse the blank lines it leaves behind.
                new_text = before.rstrip("\n") + ("\n" + after.lstrip("\n") if after.strip() else "\n")
                if not before.strip():
                    new_text = after.lstrip("\n")
            else:
                new_text = before + block + after
        else:
            if block is None:
                return
            sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
            new_text = text + sep + block + "\n"

        _atomic_write(path, new_text)


def remove_all(ledger: Optional[SkillsLedger] = None) -> None:
    """Undo every materialization -- the ~/.claude/skills dirs we own and the
    AGENTS.md blocks / .agentdeck/skills files in every folder we wired. The
    local store and working copies are left alone. Called on a plan lapse or
    when the feature is switched off. Never raises.
    """
    ledger = ledger or SkillsLedger()
    base = claude_skills_dir()
    for slug in ledger.claude_slugs():
        _remove_claude_skill(base / slug)
    for folder in ledger.all_folders():
        root = Path(folder)
        _rewrite_agents_block(root / "AGENTS.md", None)
        skills_dir = root / _AGENTDECK_DIR / "skills"
        for slug in ledger.folder_slugs(folder):
            try:
                (skills_dir / f"{slug}.md").unlink(missing_ok=True)
            except OSError:
                pass
        try:
            if skills_dir.is_dir() and not any(skills_dir.iterdir()):
                skills_dir.rmdir()
        except OSError:
            pass
    ledger.clear()


def agent_review_target(skill, folder: Optional[str], agent_key: str) -> Path:
    """The file path to hand an agent for "Improve with agent", and to watch
    for its edits afterwards.

    Claude reads its own skills dir, so point it there. Everything else gets the
    working folder's ``.agentdeck/skills/<slug>.md`` (guaranteed readable by a
    plain shell -- see the handoff-doc note in ``agent_sessions``). Falls back to
    the ``<config>/skills`` working copy when there is no folder.
    """
    slug = skill.slug
    if (agent_key or "").strip().lower() == "claude":
        d = claude_skills_dir() / slug
        try:
            d.mkdir(parents=True, exist_ok=True)
            _atomic_write(d / "SKILL.md", skill.to_markdown())
            (d / _MARKER).write_text("managed by AgentDeck\n", encoding="utf-8")
        except OSError:
            pass
        return Path(os.path.abspath(d / "SKILL.md"))
    if folder and Path(folder).is_dir():
        d = Path(folder) / _AGENTDECK_DIR / "skills"
        try:
            d.mkdir(parents=True, exist_ok=True)
            _atomic_write(d / f"{slug}.md", skill.to_markdown())
            _git_exclude(Path(folder), f"{_AGENTDECK_DIR}/")
        except OSError:
            pass
        return Path(os.path.abspath(d / f"{slug}.md"))
    p = working_copy_path(slug)
    _atomic_write(p, skill.to_markdown())
    return Path(os.path.abspath(p))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.adk{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _git_exclude(folder: Path, pattern: str) -> None:
    """Add ``pattern`` to ``.git/info/exclude`` if the folder is a git repo and
    it isn't already there. Copy of ``agent_sessions._git_exclude``."""
    exclude = folder / ".git" / "info" / "exclude"
    try:
        if not exclude.parent.is_dir():
            return
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if pattern in existing.split():
            return
        with exclude.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"{pattern}\n")
    except OSError:
        pass
