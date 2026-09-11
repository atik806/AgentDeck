"""Local storage for the Routines feature -- scheduled agent prompts.

One JSON file, ``%APPDATA%\\multi-terminal\\routines.json`` (sits beside
``config.json`` and ``notes.json``)::

    {
      "version": 1,
      "routines": [
        {"id": "r_ab12cd34", "name": "Morning check-in",
         "prompt": "Check for new PRs and summarize them",
         "agent_key": "claude", "agent_custom": "",
         "workspace_target": "new", "days": [0, 2, 4], "time": "08:00",
         "enabled": true, "created": 1725200000.0, "updated": 1725200000.0,
         "last_run_at": null, "last_run_status": ""}
      ]
    }

Qt-free on purpose (same rule as ``notes_store.py``): unit-testable offline,
importable from anywhere. Every mutation writes the whole file back
(atomically) -- there are only ever a handful of routines. Routines are
**machine-local** (not cloud-synced): they reference workspaces and agents
that only make sense on this machine, in this running session -- see
``routine_scheduler.py`` for why they only fire while AgentDeck is open.

``days`` holds Python ``datetime.weekday()`` values (Monday = 0 .. Sunday =
6); an empty list means "every day".
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "Routine",
    "RoutinesStore",
    "default_routines_path",
    "format_time_12h",
    "schedule_summary",
    "DAY_ABBR",
]

#: Bumped only if the on-disk shape changes meaning.
STORE_VERSION = 1

#: Monday-first, matching ``datetime.weekday()``.
DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

#: Sentinel meaning "open a new workspace" rather than an existing one's name.
NEW_WORKSPACE = "new"


def default_routines_path() -> Path:
    """``routines.json`` beside the app's ``config.json``.

    Imports :mod:`config` lazily so this module stays import-cheap and tests
    can point :class:`RoutinesStore` at a temp file without APPDATA in the
    picture.
    """
    try:
        from config import CONFIG_DIR

        return Path(CONFIG_DIR) / "routines.json"
    except Exception:  # noqa: BLE001 - fall back to a sane per-user location
        base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") \
            or str(Path.home() / ".config")
        return Path(base) / "multi-terminal" / "routines.json"


def format_time_12h(hhmm: str) -> str:
    """``"08:00"`` -> ``"8:00 AM"``. Anything unparseable is returned as-is."""
    try:
        h_str, m_str = str(hhmm or "").split(":")
        hour, minute = int(h_str), int(m_str)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        return str(hhmm or "")
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def schedule_summary(days: "list[int]", time_str: str) -> str:
    """A compact human line for a routine's schedule, e.g. ``"Mon, Wed, Fri
    8:00 AM"`` or ``"Daily 8:00 AM"`` (empty/every-day ``days``)."""
    clock = format_time_12h(time_str)
    ordered = sorted({int(d) for d in (days or []) if 0 <= int(d) <= 6})
    if not ordered or len(ordered) == 7:
        return f"Daily {clock}"
    return ", ".join(DAY_ABBR[d] for d in ordered) + f" {clock}"


@dataclass
class Routine:
    """One scheduled routine."""

    id: str
    name: str = ""
    prompt: str = ""
    agent_key: str = "none"
    agent_custom: str = ""
    workspace_target: str = NEW_WORKSPACE
    #: Name to give the workspace this routine opens, when
    #: ``workspace_target`` is :data:`NEW_WORKSPACE`. Empty = auto ("Workspace N").
    new_workspace_name: str = ""
    days: "list[int]" = field(default_factory=list)
    enabled: bool = True
    # NB: field order matters here -- `created`/`updated`'s default factory
    # references the module-level `time` function. A dataclass body assigns
    # its fields sequentially into the class namespace, so a `time` field
    # declared *before* these would shadow the module and break them.
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    time: str = "08:00"
    last_run_at: Optional[float] = None
    last_run_status: str = ""

    @property
    def display_name(self) -> str:
        return self.name.strip() or "Untitled routine"

    @classmethod
    def from_dict(cls, data: dict) -> "Routine":
        now = time.time()
        rid = str(data.get("id") or "").strip() or _new_id()
        try:
            created = float(data.get("created", now))
        except (TypeError, ValueError):
            created = now
        try:
            updated = float(data.get("updated", created))
        except (TypeError, ValueError):
            updated = created
        last_run_at = data.get("last_run_at")
        try:
            last_run_at = float(last_run_at) if last_run_at is not None else None
        except (TypeError, ValueError):
            last_run_at = None
        days_raw = data.get("days") or []
        days = sorted({int(d) for d in days_raw if isinstance(d, (int, float)) and 0 <= int(d) <= 6}) \
            if isinstance(days_raw, list) else []
        time_str = str(data.get("time") or "08:00")
        if len(time_str) != 5 or time_str[2] != ":":
            time_str = "08:00"
        return cls(
            id=rid,
            name=str(data.get("name") or ""),
            prompt=str(data.get("prompt") or ""),
            agent_key=str(data.get("agent_key") or "none"),
            agent_custom=str(data.get("agent_custom") or ""),
            workspace_target=str(data.get("workspace_target") or NEW_WORKSPACE),
            new_workspace_name=str(data.get("new_workspace_name") or ""),
            days=days,
            time=time_str,
            enabled=bool(data.get("enabled", True)),
            created=created,
            updated=updated,
            last_run_at=last_run_at,
            last_run_status=str(data.get("last_run_status") or ""),
        )


def _new_id() -> str:
    return "r_" + secrets.token_hex(4)


#: Fields the editor UI is allowed to write via :meth:`RoutinesStore.update`.
_EDITABLE_FIELDS = {
    "name", "prompt", "agent_key", "agent_custom", "workspace_target",
    "new_workspace_name", "days", "time", "enabled",
}


class RoutinesStore:
    """The routine list, in creation order. Construct with no argument for the
    real file; pass ``path=`` in tests."""

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self._path = Path(path) if path is not None else default_routines_path()
        self._routines: "list[Routine]" = []
        self._loaded = False

    # -- io ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> "list[Routine]":
        """(Re)read the file. Tolerant of a missing or corrupt file -- either
        way you get a usable (possibly empty) list, never an exception."""
        self._loaded = True
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._routines = []
            return self._routines
        try:
            data = json.loads(raw)
        except ValueError:
            self._routines = []
            return self._routines
        items = data.get("routines") if isinstance(data, dict) else None
        if not isinstance(items, list):
            self._routines = []
            return self._routines
        routines = [Routine.from_dict(it) for it in items if isinstance(it, dict)]
        self._routines = _sorted(routines)
        return self._routines

    def save(self) -> None:
        """Write the whole list back, atomically (temp file + replace)."""
        payload = {
            "version": STORE_VERSION,
            "routines": [asdict(r) for r in self._routines],
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
            # Routines are a convenience; a read-only disk shouldn't crash the app.
            pass

    # -- queries -----------------------------------------------------------

    def _ensure(self) -> None:
        if not self._loaded:
            self.load()

    def all(self) -> "list[Routine]":
        self._ensure()
        return list(self._routines)

    def get(self, routine_id: str) -> Optional[Routine]:
        self._ensure()
        return next((r for r in self._routines if r.id == routine_id), None)

    def __len__(self) -> int:  # noqa: D105
        self._ensure()
        return len(self._routines)

    # -- mutations -------------------------------------------------------

    def create(self, **fields) -> Routine:
        self._ensure()
        kwargs = {k: v for k, v in fields.items() if k in _EDITABLE_FIELDS}
        routine = Routine(id=_new_id(), **kwargs)
        self._routines.append(routine)
        self._routines = _sorted(self._routines)
        self.save()
        return routine

    def update(self, routine_id: str, **fields) -> Optional[Routine]:
        """Persist an editor change. Any of :data:`_EDITABLE_FIELDS` may be
        passed; unknown keys are ignored so callers can pass a whole form's
        worth of values without filtering first."""
        self._ensure()
        routine = self.get(routine_id)
        if routine is None:
            return None
        changed = False
        for key, value in fields.items():
            if key not in _EDITABLE_FIELDS:
                continue
            if getattr(routine, key) != value:
                setattr(routine, key, value)
                changed = True
        if changed:
            routine.updated = time.time()
            self.save()
        return routine

    def mark_run(self, routine_id: str, status: str) -> Optional[Routine]:
        """Record the outcome of a fire -- called by the scheduler, not the
        editor. Does not touch ``updated`` (that tracks content edits)."""
        self._ensure()
        routine = self.get(routine_id)
        if routine is None:
            return None
        routine.last_run_at = time.time()
        routine.last_run_status = status
        self.save()
        return routine

    def delete(self, routine_id: str) -> bool:
        self._ensure()
        before = len(self._routines)
        self._routines = [r for r in self._routines if r.id != routine_id]
        if len(self._routines) != before:
            self.save()
            return True
        return False


def _sorted(routines: "list[Routine]") -> "list[Routine]":
    """Creation order -- a task list shouldn't jump around every time one
    fires (which is what newest-updated-first would do)."""
    return sorted(routines, key=lambda r: r.created)
