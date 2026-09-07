"""Local storage for the Skills feature -- reusable agent instruction docs.

One JSON file, ``%APPDATA%\\multi-terminal\\skills.json`` (sits beside
``config.json`` / ``notes.json`` / ``routines.json``)::

    {
      "version": 1,
      "skills": [
        {"id": "sk_ab12cd34", "slug": "pr-review-checklist",
         "name": "pr-review-checklist",
         "description": "Use when reviewing a pull request or a diff.",
         "body": "1. Check error handling ...",
         "enabled": true, "source": "upload",
         "created": 1725700000.0, "updated": 1725700000.0,
         "last_reviewed_at": null, "last_reviewed_by": ""}
      ]
    }

A **skill** is a Markdown document in Claude-Code ``SKILL.md`` shape -- optional
YAML frontmatter (``name`` / ``description``) then instructions the agent should
follow when the skill applies. Enabled skills are *materialized* into the
places each agent discovers instructions (see :mod:`skills_sync`).

Qt-free on purpose (same rule as ``notes_store`` / ``routines_store``):
unit-testable offline, importable from anywhere. Every mutation writes the whole
file back atomically -- there are only ever a handful of skills. The body lives
inline here (the JSON is the single source of truth); :mod:`skills_sync` is what
puts a real ``.md`` file on disk for an agent to read or rewrite.

Skills are **per-account**: the local file is the offline copy, and (on Pro)
:mod:`skills_cloud` mirrors them to ``public.skills`` so the same library
follows the user to their other machines. Nothing here is machine-specific, so
unlike routines they *are* synced.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "Skill",
    "SkillsStore",
    "default_skills_path",
    "slugify",
    "parse_frontmatter",
    "render_skill_markdown",
    "STORE_VERSION",
]

#: Bumped only if the on-disk shape changes meaning.
STORE_VERSION = 1

#: Slugs longer than this are truncated -- they become a directory name under
#: ``~/.claude/skills/`` and a file name, so keep them sane.
_MAX_SLUG = 48

#: Fields the panel / importer may hand to :meth:`SkillsStore.update`.
_EDITABLE_FIELDS = {
    "name", "description", "body", "enabled", "source",
    "last_reviewed_at", "last_reviewed_by",
}


def default_skills_path() -> Path:
    """``skills.json`` beside the app's ``config.json``.

    Imports :mod:`config` lazily so this module stays import-cheap and tests can
    point :class:`SkillsStore` at a temp file without APPDATA in the picture.
    """
    override = os.environ.get("ADK_SKILLS_JSON")  # tests redirect the store here
    if override:
        return Path(override)
    try:
        from config import CONFIG_DIR

        return Path(CONFIG_DIR) / "skills.json"
    except Exception:  # noqa: BLE001 - fall back to a sane per-user location
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "multi-terminal" / "skills.json"


def slugify(name: str) -> str:
    """``"PR Review Checklist!"`` -> ``"pr-review-checklist"``.

    Lowercase, non-alphanumeric runs collapsed to a single ``-``, trimmed, and
    truncated. Empty / all-punctuation input falls back to ``"skill"``.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    slug = slug[:_MAX_SLUG].strip("-")
    return slug or "skill"


# ---------------------------------------------------------------------------
# Frontmatter -- a deliberately tiny YAML subset (key: value scalars only)
# ---------------------------------------------------------------------------

_FM_LINE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")


def parse_frontmatter(text: str) -> "tuple[dict[str, str], str]":
    """``("---\\nname: x\\n---\\nbody")`` -> ``({"name": "x"}, "body")``.

    Only a leading ``---`` fenced block of ``key: value`` scalar lines is
    recognised; anything fancier (nested maps, lists, block scalars) is left in
    the body untouched. No YAML dependency -- skills are prose, not config.
    A file with no frontmatter returns ``({}, text)``.
    """
    text = text or ""
    # Tolerate a UTF-8 BOM and leading blank lines before the opening fence.
    stripped = text.lstrip("﻿")
    lines = stripped.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return {}, text
    meta: "dict[str, str]" = {}
    j = i + 1
    while j < len(lines):
        if lines[j].strip() == "---":
            body = "\n".join(lines[j + 1:])
            # Drop one leading blank line so the body starts at real content.
            body = body[1:] if body.startswith("\n") else body
            return meta, body
        m = _FM_LINE.match(lines[j].strip())
        if m:
            key = m.group(1).strip().lower()
            val = m.group(2).strip().strip('"').strip("'")
            meta[key] = val
        j += 1
    # No closing fence -> not really frontmatter; hand the whole thing back.
    return {}, text


def render_skill_markdown(name: str, description: str, body: str) -> str:
    """A full ``SKILL.md`` string: ``name`` + ``description`` frontmatter then
    the body. What :mod:`skills_sync` writes for an agent to read."""
    name = (name or "").strip()
    description = (description or "").strip().replace("\n", " ")
    body = (body or "").strip()
    fm = ["---", f"name: {name}", f"description: {description}", "---", ""]
    return "\n".join(fm) + (body + "\n" if body else "")


@dataclass
class Skill:
    """One skill. ``slug`` is derived from ``name`` once at creation and never
    changes (it is a directory / file name on disk); renaming only touches
    ``name``."""

    id: str
    slug: str = ""
    name: str = ""
    description: str = ""
    body: str = ""
    enabled: bool = True
    #: How it got here -- "manual" (written in the panel), "upload" (a
    #: SKILL.md file), "agent" (an agent rewrote it via "Improve with agent").
    source: str = "manual"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    last_reviewed_at: Optional[float] = None
    last_reviewed_by: str = ""

    @property
    def display_name(self) -> str:
        return (self.name or "").strip() or self.slug or "Untitled skill"

    @property
    def sha(self) -> str:
        """SHA-256 of the rendered ``SKILL.md`` -- lets a watcher tell when an
        agent has actually changed the file on disk."""
        payload = render_skill_markdown(self.name, self.description, self.body)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_markdown(self) -> str:
        return render_skill_markdown(self.name, self.description, self.body)

    @classmethod
    def from_dict(cls, data: dict) -> "Skill":
        now = time.time()
        sid = str(data.get("id") or "").strip() or _new_id()
        name = str(data.get("name") or "")
        slug = str(data.get("slug") or "").strip().lower() or slugify(name)
        # A stored slug from an older / hand-edited file may be junk -- re-clean.
        slug = slugify(slug)
        try:
            created = float(data.get("created", now))
        except (TypeError, ValueError):
            created = now
        try:
            updated = float(data.get("updated", created))
        except (TypeError, ValueError):
            updated = created
        reviewed = data.get("last_reviewed_at")
        try:
            reviewed = float(reviewed) if reviewed is not None else None
        except (TypeError, ValueError):
            reviewed = None
        source = str(data.get("source") or "manual")
        if source not in ("manual", "upload", "agent"):
            source = "manual"
        return cls(
            id=sid,
            slug=slug,
            name=name,
            description=str(data.get("description") or "").replace("\n", " ").strip(),
            body=str(data.get("body") or ""),
            enabled=bool(data.get("enabled", True)),
            source=source,
            created=created,
            updated=updated,
            last_reviewed_at=reviewed,
            last_reviewed_by=str(data.get("last_reviewed_by") or ""),
        )


def _new_id() -> str:
    return "sk_" + secrets.token_hex(4)


class SkillsStore:
    """The skill library, in creation order. Construct with no argument for the
    real file; pass ``path=`` in tests."""

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self._path = Path(path) if path is not None else default_skills_path()
        self._skills: "list[Skill]" = []
        self._loaded = False

    # -- io ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> "list[Skill]":
        """(Re)read the file. Tolerant of a missing or corrupt file -- either
        way you get a usable (possibly empty) list, never an exception."""
        self._loaded = True
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._skills = []
            return self._skills
        try:
            data = json.loads(raw)
        except ValueError:
            self._skills = []
            return self._skills
        items = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(items, list):
            self._skills = []
            return self._skills
        skills = [Skill.from_dict(it) for it in items if isinstance(it, dict)]
        self._skills = _dedupe_slugs(_sorted(skills))
        return self._skills

    def save(self) -> None:
        """Write the whole library back, atomically (temp file + replace)."""
        payload = {
            "version": STORE_VERSION,
            "skills": [asdict(s) for s in self._skills],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError:
            # A skill library is a convenience; a read-only disk shouldn't crash.
            pass

    # -- queries -----------------------------------------------------------

    def _ensure(self) -> None:
        if not self._loaded:
            self.load()

    def all(self) -> "list[Skill]":
        self._ensure()
        return list(self._skills)

    def enabled(self) -> "list[Skill]":
        self._ensure()
        return [s for s in self._skills if s.enabled]

    def get(self, skill_id: str) -> Optional[Skill]:
        self._ensure()
        return next((s for s in self._skills if s.id == skill_id), None)

    def get_by_slug(self, slug: str) -> Optional[Skill]:
        self._ensure()
        slug = slugify(slug)
        return next((s for s in self._skills if s.slug == slug), None)

    def __len__(self) -> int:  # noqa: D105
        self._ensure()
        return len(self._skills)

    # -- mutations -------------------------------------------------------

    def create(
        self,
        name: str = "",
        description: str = "",
        body: str = "",
        *,
        enabled: bool = True,
        source: str = "manual",
    ) -> Skill:
        self._ensure()
        base = slugify(name) if name.strip() else "skill"
        slug = self._free_slug(base)
        skill = Skill(
            id=_new_id(),
            slug=slug,
            name=name.strip() or slug,
            description=(description or "").replace("\n", " ").strip(),
            body=body or "",
            enabled=bool(enabled),
            source=source if source in ("manual", "upload", "agent") else "manual",
        )
        self._skills.append(skill)
        self._skills = _sorted(self._skills)
        self.save()
        return skill

    def import_markdown(self, text: str, *, fallback_name: str = "") -> Skill:
        """Create a skill from a ``SKILL.md`` string -- frontmatter ``name`` /
        ``description`` are used when present, else the first heading / a
        supplied fallback."""
        meta, body = parse_frontmatter(text)
        name = (meta.get("name") or "").strip()
        if not name:
            for raw in body.splitlines():
                raw = raw.strip()
                if raw.startswith("#"):
                    name = raw.lstrip("#").strip()[:_MAX_SLUG]
                    if name:
                        break
        if not name:
            name = (fallback_name or "").strip()
        description = (meta.get("description") or "").strip()
        return self.create(
            name=name or "Imported skill",
            description=description,
            body=body.strip(),
            source="upload",
        )

    def update(self, skill_id: str, **fields) -> Optional[Skill]:
        """Persist an editor / importer / review change. Any of
        :data:`_EDITABLE_FIELDS` may be passed; unknown keys are ignored so a
        whole form's worth of values can be handed in without filtering.

        A content change (name / description / body) bumps ``updated``; a bare
        ``enabled`` toggle bumps it too (so cloud last-write-wins still moves).
        Pass ``updated=<epoch>`` to set it explicitly (the cloud pull does this).
        """
        self._ensure()
        skill = self.get(skill_id)
        if skill is None:
            return None
        changed = False
        for key, value in fields.items():
            if key not in _EDITABLE_FIELDS:
                continue
            if key == "description" and isinstance(value, str):
                value = value.replace("\n", " ").strip()
            if key == "source" and value not in ("manual", "upload", "agent"):
                continue
            if getattr(skill, key) != value:
                setattr(skill, key, value)
                changed = True
        if changed:
            explicit = fields.get("updated")
            try:
                skill.updated = float(explicit) if explicit is not None else time.time()
            except (TypeError, ValueError):
                skill.updated = time.time()
            self._skills = _sorted(self._skills)
            self.save()
        return skill

    def set_enabled(self, skill_id: str, enabled: bool) -> Optional[Skill]:
        return self.update(skill_id, enabled=bool(enabled))

    def delete(self, skill_id: str) -> bool:
        self._ensure()
        before = len(self._skills)
        self._skills = [s for s in self._skills if s.id != skill_id]
        if len(self._skills) != before:
            self.save()
            return True
        return False

    # -- cloud reconcile -------------------------------------------------

    def merge_cloud(self, rows: "list[dict]") -> bool:
        """Fold ``public.skills`` rows into the local library, keyed by ``slug``.

        Last-write-wins by ``updated_at`` per slug; a row with ``deleted`` true
        removes the local copy. Returns True if anything changed locally.
        ``rows`` items look like ``{"slug", "name", "description", "body",
        "enabled", "deleted", "updated_at"}`` (``updated_at`` an ISO string or
        epoch). Never raises on a malformed row -- it is skipped.
        """
        self._ensure()
        changed = False
        by_slug = {s.slug: s for s in self._skills}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            slug = slugify(row.get("slug") or "")
            if slug == "skill" and not (row.get("slug") or "").strip():
                continue
            remote_updated = _epoch(row.get("updated_at"))
            local = by_slug.get(slug)
            if row.get("deleted"):
                if local is not None:
                    self._skills = [s for s in self._skills if s.id != local.id]
                    by_slug.pop(slug, None)
                    changed = True
                continue
            fields = dict(
                name=str(row.get("name") or slug),
                description=str(row.get("description") or ""),
                body=str(row.get("body") or ""),
                enabled=bool(row.get("enabled", True)),
                source="agent" if str(row.get("source")) == "agent" else "manual",
            )
            if local is None:
                s = Skill(id=_new_id(), slug=slug, updated=remote_updated,
                          created=remote_updated, **fields)
                self._skills.append(s)
                by_slug[slug] = s
                changed = True
            elif remote_updated > local.updated + 0.001:
                for k, v in fields.items():
                    setattr(local, k, v)
                local.updated = remote_updated
                changed = True
        if changed:
            self._skills = _sorted(self._skills)
            self.save()
        return changed

    def cloud_rows(self) -> "list[dict]":
        """Every skill as a ``public.skills`` upsert row (ISO-8601
        ``updated_at``, so it casts straight to the ``timestamptz`` column)."""
        self._ensure()
        return [
            {
                "slug": s.slug,
                "name": s.name,
                "description": s.description,
                "body": s.body,
                "enabled": s.enabled,
                "source": s.source,
                "deleted": False,
                "updated_at": _iso(s.updated),
            }
            for s in self._skills
        ]

    # -- slug helpers --------------------------------------------------

    def _free_slug(self, base: str) -> str:
        base = slugify(base)
        taken = {s.slug for s in self._skills}
        if base not in taken:
            return base
        for i in range(2, 1000):
            cand = f"{base[: _MAX_SLUG - 4]}-{i}"
            if cand not in taken:
                return cand
        return f"{base}-{secrets.token_hex(2)}"


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return datetime.now(timezone.utc).isoformat()


def _epoch(value: object) -> float:
    """Parse an ISO-8601 string or a number to epoch seconds. 0.0 on failure."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    from datetime import datetime

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _sorted(skills: "list[Skill]") -> "list[Skill]":
    """Creation order -- a library shouldn't reshuffle every time one is
    toggled or reviewed."""
    return sorted(skills, key=lambda s: s.created)


def _dedupe_slugs(skills: "list[Skill]") -> "list[Skill]":
    """A hand-edited / merged file could carry two skills with one slug; keep
    the first, re-slug the rest so on-disk materialization stays 1:1."""
    seen: set[str] = set()
    for s in skills:
        if s.slug in seen:
            base = s.slug
            i = 2
            while f"{base}-{i}" in seen:
                i += 1
            s.slug = f"{base}-{i}"
        seen.add(s.slug)
    return skills
