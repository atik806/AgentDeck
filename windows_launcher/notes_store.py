"""Local notebook storage for the Notes panel.

One JSON file, ``%APPDATA%\\multi-terminal\\notes.json`` (sits next to
``config.json``)::

    {
      "version": 1,
      "notes": [
        {"id": "n_ab12cd34", "title": "Deploy checklist",
         "body": "1. bump version.py\\n2. push tag",
         "created": 1725200000.0, "updated": 1725200450.0}
      ]
    }

Qt-free on purpose: it can be unit-tested offline and imported from anywhere.
Every mutation writes the whole file back (atomically) -- there are only ever a
handful of notes, and losing a keystroke to a crash is worse than the write.
Notes are **machine-local** -- they are not part of the cloud-synced settings.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

__all__ = ["Note", "NotesStore", "default_notes_path", "derive_title"]

#: Bumped only if the on-disk shape changes meaning.
STORE_VERSION = 1

#: Shown in the list when a note has no usable first line.
UNTITLED = "Untitled note"


def default_notes_path() -> Path:
    """``notes.json`` beside the app's ``config.json``.

    Imports :mod:`config` lazily so this module stays import-cheap and tests can
    point :class:`NotesStore` at a temp file without APPDATA in the picture.
    """
    try:
        from config import CONFIG_DIR

        return Path(CONFIG_DIR) / "notes.json"
    except Exception:  # noqa: BLE001 - fall back to a sane per-user location
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "multi-terminal" / "notes.json"


def derive_title(body: str, explicit: str = "") -> str:
    """A display title for a note: an explicit one wins, else its first line."""
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    for line in (body or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:80]
    return UNTITLED


@dataclass
class Note:
    """One note. ``title`` is usually derived from ``body`` but kept on disk so
    a renamed note whose first line changed keeps its chosen name."""

    id: str
    title: str = ""
    body: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @property
    def display_title(self) -> str:
        return derive_title(self.body, self.title)

    @property
    def preview(self) -> str:
        """A one-line snippet of the body, minus whatever became the title."""
        lines = [ln.strip() for ln in (self.body or "").splitlines()]
        lines = [ln for ln in lines if ln]
        if self.title.strip():
            body_lines = lines
        else:
            body_lines = lines[1:]  # first line is the title
        return (body_lines[0][:120] if body_lines else "")

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        now = time.time()
        nid = str(data.get("id") or "").strip() or _new_id()
        try:
            created = float(data.get("created", now))
        except (TypeError, ValueError):
            created = now
        try:
            updated = float(data.get("updated", created))
        except (TypeError, ValueError):
            updated = created
        return cls(
            id=nid,
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            created=created,
            updated=updated,
        )


def _new_id() -> str:
    return "n_" + secrets.token_hex(4)


class NotesStore:
    """The notebook: an ordered list of :class:`Note`, newest-updated first.

    Construct with no argument for the real file; pass ``path=`` in tests. The
    file is read once on construction (or first access) and every mutating call
    persists immediately.
    """

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self._path = Path(path) if path is not None else default_notes_path()
        self._notes: list[Note] = []
        self._loaded = False

    # -- io ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[Note]:
        """(Re)read the file. Tolerant of a missing or corrupt file -- either
        way you get a usable (possibly empty) list, never an exception."""
        self._loaded = True
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._notes = []
            return self._notes
        try:
            data = json.loads(raw)
        except ValueError:
            self._notes = []
            return self._notes
        items = data.get("notes") if isinstance(data, dict) else None
        if not isinstance(items, list):
            self._notes = []
            return self._notes
        notes = [Note.from_dict(it) for it in items if isinstance(it, dict)]
        self._notes = _sorted(notes)
        return self._notes

    def save(self) -> None:
        """Write the whole notebook back, atomically (temp file + replace)."""
        payload = {
            "version": STORE_VERSION,
            "notes": [asdict(n) for n in self._notes],
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
            # Notes are a convenience; a read-only disk shouldn't crash the app.
            pass

    # -- queries -----------------------------------------------------------

    def _ensure(self) -> None:
        if not self._loaded:
            self.load()

    def all(self) -> list[Note]:
        self._ensure()
        return list(self._notes)

    def get(self, note_id: str) -> Optional[Note]:
        self._ensure()
        return next((n for n in self._notes if n.id == note_id), None)

    def __len__(self) -> int:  # noqa: D105
        self._ensure()
        return len(self._notes)

    # -- mutations -------------------------------------------------------

    def create(self, body: str = "", title: str = "") -> Note:
        self._ensure()
        note = Note(id=_new_id(), title=title, body=body)
        self._notes.append(note)
        self._notes = _sorted(self._notes)
        self.save()
        return note

    def update(
        self,
        note_id: str,
        *,
        body: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Optional[Note]:
        self._ensure()
        note = self.get(note_id)
        if note is None:
            return None
        changed = False
        if body is not None and body != note.body:
            note.body = body
            changed = True
        if title is not None and title != note.title:
            note.title = title
            changed = True
        if changed:
            note.updated = time.time()
            self._notes = _sorted(self._notes)
            self.save()
        return note

    def delete(self, note_id: str) -> bool:
        self._ensure()
        before = len(self._notes)
        self._notes = [n for n in self._notes if n.id != note_id]
        if len(self._notes) != before:
            self.save()
            return True
        return False


def _sorted(notes: list[Note]) -> list[Note]:
    """Newest-updated first; stable for equal timestamps."""
    return sorted(notes, key=lambda n: n.updated, reverse=True)
