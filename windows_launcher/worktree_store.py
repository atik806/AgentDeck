"""Local storage for the Worktrees feature -- one isolated git worktree per pane.

One JSON file, ``%APPDATA%\\multi-terminal\\worktrees.json`` (beside
``config.json`` / ``notes.json`` / ``routines.json``)::

    {
      "version": 1,
      "worktrees": [
        {"id": "wt_ab12cd34",
         "repo_root": "E:\\\\code\\\\myapp", "repo_key": "9f3c1a20b4e8",
         "branch": "agentdeck/api-work/p1-260907-1430-a1b2",
         "path": "C:\\\\Users\\\\me\\\\AppData\\\\Local\\\\multi-terminal\\\\worktrees\\\\9f3c1a20b4e8\\\\...",
         "base_branch": "main", "base_sha_at_create": "abc123…",
         "workspace_name": "API work", "pane_id": "p_1a2b3c4d",
         "agent_key": "claude", "status": "active",
         "created": 1725690000.0, "updated": 1725690000.0,
         "last_merge_status": "", "pr_url": ""}
      ]
    }

Qt-free (same rule as :mod:`routines_store`): unit-testable offline, importable
from anywhere. Every mutation writes the whole file back atomically -- there are
only ever a handful of worktrees.

**Machine-local, not cloud-synced:** the paths are per-machine, and a worktree
only means something while this checkout exists on this disk.

The scratch worktrees themselves live under ``%LOCALAPPDATA%`` (not roaming) --
they are large and machine-specific -- at
``…\\multi-terminal\\worktrees\\<repo_key>\\<branch>``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

__all__ = [
    "WorktreeRecord",
    "WorktreeStore",
    "default_worktrees_path",
    "default_worktrees_root",
    "repo_key_for",
    "branch_name",
    "worktree_dir",
    "slugify",
    "ACTIVE_STATUSES",
]

STORE_VERSION = 1

#: Records the Review panel lists (everything else is history / cleanup state).
ACTIVE_STATUSES = ("active", "detached", "orphaned")

#: Every status a record can hold.
#:  active        -- a live pane is (or was) working here
#:  detached      -- pane closed, branch + dir deliberately kept
#:  merged        -- merged back to base; dir may still be on disk
#:  discarded     -- dir + branch removed on purpose
#:  orphaned      -- the dir vanished (crash / manual delete) but the row remains
#:  pending_delete-- removal failed (dir locked); retry on next launch
_ALL_STATUSES = frozenset(
    {"active", "detached", "merged", "discarded", "orphaned", "pending_delete"}
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _canon(path: str) -> str:
    """Case-folded real path for reliable comparison (8.3 names, symlinks).

    Empty in, empty out -- ``os.path.realpath("")`` resolves to the process cwd,
    which would let an empty ``path`` collide with a real worktree.
    """
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.realpath(str(path)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.normpath(str(path)))


def slugify(text: str, *, fallback: str = "ws", maxlen: int = 24) -> str:
    """Lowercase kebab slug safe for a git branch component."""
    s = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    s = s[:maxlen].strip("-")
    return s or fallback


def default_worktrees_path() -> Path:
    """``worktrees.json`` beside the app's ``config.json``."""
    try:
        from config import CONFIG_DIR

        return Path(CONFIG_DIR) / "worktrees.json"
    except Exception:  # noqa: BLE001
        base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") \
            or str(Path.home() / ".config")
        return Path(base) / "multi-terminal" / "worktrees.json"


def default_worktrees_root() -> Path:
    """Directory that holds the scratch worktree trees.

    Windows: ``%LOCALAPPDATA%\\multi-terminal\\worktrees`` (not roaming -- these
    are large, machine-local scratch checkouts, not settings to sync). Linux:
    ``~/.local/share/multi-terminal/worktrees`` (``config.data_dir()`` -- not
    ``cache_dir()``, which adds a Windows-only ``Cache`` segment that would
    change the existing Windows path). Overridable with ``ADK_WORKTREES_ROOT``
    for tests.
    """
    override = os.environ.get("ADK_WORKTREES_ROOT")
    if override:
        return Path(override)
    try:
        from config import data_dir

        return data_dir() / "worktrees"
    except Exception:  # noqa: BLE001
        base = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.environ.get("XDG_DATA_HOME")
            or str(Path.home() / ".local" / "share")
        )
        return Path(base) / "multi-terminal" / "worktrees"


def repo_key_for(repo_root: str | os.PathLike) -> str:
    """A short, stable, case-insensitive key for a repository path."""
    norm = os.path.normcase(os.path.abspath(str(repo_root)))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def branch_name(
    workspace_name: str, pane_index: int, *, when: Optional[datetime] = None
) -> str:
    """``agentdeck/<ws-slug>/p<N>-<yymmdd-HHMM>-<hex>`` -- unique per pane per run."""
    when = when or datetime.now()
    return (
        f"agentdeck/{slugify(workspace_name)}/"
        f"p{int(pane_index) + 1}-{when:%y%m%d-%H%M}-{secrets.token_hex(2)}"
    )


def worktree_dir(root: Path, repo_key: str, branch: str) -> Path:
    """Where the worktree for ``branch`` of repo ``repo_key`` lives on disk.

    The branch's own slashes are flattened so the leaf is a single directory.
    """
    leaf = branch.replace("/", "__")
    return Path(root) / repo_key / leaf


@dataclass
class WorktreeRecord:
    id: str
    repo_root: str = ""
    repo_key: str = ""
    branch: str = ""
    path: str = ""
    base_branch: str = ""
    base_sha_at_create: str = ""
    workspace_name: str = ""
    pane_id: str = ""
    agent_key: str = ""
    status: str = "active"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    last_merge_status: str = ""
    pr_url: str = ""

    @property
    def short_branch(self) -> str:
        """The last path component of the branch, for compact display."""
        return self.branch.rsplit("/", 1)[-1] if self.branch else ""

    @property
    def dir_exists(self) -> bool:
        return bool(self.path) and os.path.isdir(self.path)

    @classmethod
    def from_dict(cls, data: dict) -> "WorktreeRecord":
        now = time.time()
        wid = str(data.get("id") or "").strip() or _new_id()

        def _f(key: str, default: float) -> float:
            try:
                return float(data.get(key, default))
            except (TypeError, ValueError):
                return default

        created = _f("created", now)
        status = str(data.get("status") or "active")
        if status not in _ALL_STATUSES:
            status = "active"
        return cls(
            id=wid,
            repo_root=str(data.get("repo_root") or ""),
            repo_key=str(data.get("repo_key") or ""),
            branch=str(data.get("branch") or ""),
            path=str(data.get("path") or ""),
            base_branch=str(data.get("base_branch") or ""),
            base_sha_at_create=str(data.get("base_sha_at_create") or ""),
            workspace_name=str(data.get("workspace_name") or ""),
            pane_id=str(data.get("pane_id") or ""),
            agent_key=str(data.get("agent_key") or ""),
            status=status,
            created=created,
            updated=_f("updated", created),
            last_merge_status=str(data.get("last_merge_status") or ""),
            pr_url=str(data.get("pr_url") or ""),
        )


def _new_id() -> str:
    return "wt_" + secrets.token_hex(4)


#: Fields callers may hand to :meth:`WorktreeStore.update`.
_EDITABLE_FIELDS = {
    "repo_root", "repo_key", "branch", "path", "base_branch",
    "base_sha_at_create", "workspace_name", "pane_id", "agent_key",
    "status", "last_merge_status", "pr_url",
}


class WorktreeStore:
    """The worktree list, in creation order. Mirrors :class:`routines_store.RoutinesStore`."""

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self._path = Path(path) if path is not None else default_worktrees_path()
        self._items: list[WorktreeRecord] = []
        self._loaded = False

    # -- io ----------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[WorktreeRecord]:
        """(Re)read the file, tolerant of a missing or corrupt one."""
        self._loaded = True
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._items = []
            return self._items
        try:
            data = json.loads(raw)
        except ValueError:
            self._items = []
            return self._items
        items = data.get("worktrees") if isinstance(data, dict) else None
        if not isinstance(items, list):
            self._items = []
            return self._items
        recs = [WorktreeRecord.from_dict(it) for it in items if isinstance(it, dict)]
        self._items = sorted(recs, key=lambda r: r.created)
        return self._items

    def save(self) -> None:
        """Write the whole list back atomically (temp file + ``os.replace``)."""
        payload = {
            "version": STORE_VERSION,
            "worktrees": [asdict(r) for r in self._items],
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
            pass

    # -- queries ---------------------------------------------------------

    def _ensure(self) -> None:
        if not self._loaded:
            self.load()

    def all(self) -> list[WorktreeRecord]:
        self._ensure()
        return list(self._items)

    def active(self) -> list[WorktreeRecord]:
        """Records worth showing in the Review panel."""
        self._ensure()
        return [r for r in self._items if r.status in ACTIVE_STATUSES]

    def with_status(self, *statuses: str) -> list[WorktreeRecord]:
        self._ensure()
        want = set(statuses)
        return [r for r in self._items if r.status in want]

    def get(self, wid: str) -> Optional[WorktreeRecord]:
        self._ensure()
        return next((r for r in self._items if r.id == wid), None)

    def by_pane(self, pane_id: str) -> Optional[WorktreeRecord]:
        self._ensure()
        if not pane_id:
            return None
        return next((r for r in self._items if r.pane_id == pane_id), None)

    def by_path(self, path: str) -> Optional[WorktreeRecord]:
        self._ensure()
        target = _canon(path)
        if not target:
            return None
        return next((r for r in self._items if _canon(r.path) == target), None)

    def for_repo(self, repo_root: str) -> list[WorktreeRecord]:
        self._ensure()
        key = repo_key_for(repo_root)
        return [r for r in self._items if r.repo_key == key]

    def repo_roots(self) -> list[str]:
        """Distinct repo roots referenced by any record (for startup reconcile)."""
        self._ensure()
        seen: dict[str, str] = {}
        for r in self._items:
            if r.repo_root and r.repo_key not in seen:
                seen[r.repo_key] = r.repo_root
        return list(seen.values())

    def __len__(self) -> int:  # noqa: D105
        self._ensure()
        return len(self._items)

    # -- mutations -----------------------------------------------------

    def create(self, **fields) -> WorktreeRecord:
        self._ensure()
        kwargs = {k: v for k, v in fields.items() if k in _EDITABLE_FIELDS}
        rec = WorktreeRecord(id=_new_id(), **kwargs)
        if rec.repo_root and not rec.repo_key:
            rec.repo_key = repo_key_for(rec.repo_root)
        self._items.append(rec)
        self._items = sorted(self._items, key=lambda r: r.created)
        self.save()
        return rec

    def update(self, wid: str, **fields) -> Optional[WorktreeRecord]:
        self._ensure()
        rec = self.get(wid)
        if rec is None:
            return None
        changed = False
        for key, value in fields.items():
            if key not in _EDITABLE_FIELDS:
                continue
            if key == "status" and value not in _ALL_STATUSES:
                continue
            if getattr(rec, key) != value:
                setattr(rec, key, value)
                changed = True
        if changed:
            rec.updated = time.time()
            self.save()
        return rec

    def set_status(self, wid: str, status: str) -> Optional[WorktreeRecord]:
        return self.update(wid, status=status)

    def delete(self, wid: str) -> bool:
        self._ensure()
        before = len(self._items)
        self._items = [r for r in self._items if r.id != wid]
        if len(self._items) != before:
            self.save()
            return True
        return False

    # -- reconcile -----------------------------------------------------

    def reconcile(
        self,
        live_by_repo: "dict[str, list[dict]]",
        scanned: "set[str] | list[str] | None" = None,
    ) -> list[WorktreeRecord]:
        """Bring the store back in step with what's actually on disk.

        ``live_by_repo`` maps a repo root to the parsed
        ``git_worktree.list_worktrees`` output for it. ``scanned`` (optional) is
        the set of repo roots whose ``git`` query actually **succeeded** -- a
        record whose repo wasn't successfully scanned is left alone rather than
        orphaned on the strength of a transient ``git`` failure. ``None`` means
        "trust every key of ``live_by_repo``" (back-compat).

        * A record whose directory is gone (repo was scanned) -> ``orphaned``.
        * A record git no longer registers (repo was scanned) -> ``orphaned``.
        * An ``orphaned`` record that is back on disk + registered -> ``detached``.
        * An on-disk ``agentdeck/*`` worktree with no record -> a synthesised
          ``detached`` record so the user can still find and clean it up.

        Returns the records that changed (or were created), for a one-line
        startup notice. Does not delete anything.
        """
        self._ensure()
        changed: list[WorktreeRecord] = []

        scanned_keys: "set[str] | None" = None
        if scanned is not None:
            scanned_keys = {repo_key_for(r) for r in scanned}

        live_paths: dict[str, dict] = {}
        for entries in live_by_repo.values():
            for e in entries or []:
                canon = _canon(e.get("path", ""))
                if canon:
                    live_paths[canon] = e

        known_paths = set()
        for rec in self._items:
            known_paths.add(_canon(rec.path))
            if rec.status in ("discarded", "merged", "pending_delete"):
                continue
            repo_scanned = scanned_keys is None or rec.repo_key in scanned_keys
            if not repo_scanned:
                continue
            norm = _canon(rec.path)
            on_disk = bool(rec.path) and os.path.isdir(rec.path)
            registered = bool(norm) and norm in live_paths
            if (not on_disk or not registered) and rec.status != "orphaned":
                rec.status = "orphaned"
                rec.updated = time.time()
                changed.append(rec)
            elif on_disk and registered and rec.status == "orphaned":
                rec.status = "detached"  # it came back -- likely a flaky scan
                rec.updated = time.time()
                changed.append(rec)

        # Adopt stray agentdeck/* worktrees that no record covers.
        for root, entries in live_by_repo.items():
            if scanned_keys is not None and repo_key_for(root) not in scanned_keys:
                continue
            for e in entries or []:
                branch = e.get("branch", "")
                norm = _canon(e.get("path", ""))
                if not branch.startswith("agentdeck/") or not norm or norm in known_paths:
                    continue
                rec = WorktreeRecord(
                    id=_new_id(),
                    repo_root=_repo_root_for_entry(live_by_repo, e),
                    branch=branch,
                    path=os.path.normpath(e.get("path", "")),
                    base_branch="",
                    workspace_name="(imported)",
                    status="detached",
                )
                if rec.repo_root:
                    rec.repo_key = repo_key_for(rec.repo_root)
                self._items.append(rec)
                changed.append(rec)

        if changed:
            self._items = sorted(self._items, key=lambda r: r.created)
            self.save()
        return changed


def _repo_root_for_entry(live_by_repo: "dict[str, list[dict]]", entry: dict) -> str:
    for root, entries in live_by_repo.items():
        if entry in (entries or []):
            return root
    return ""
