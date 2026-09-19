"""The job pipeline behind the LinkedIn plugin.

One JSON file, ``%APPDATA%\\multi-terminal\\linkedin_jobs.json`` (next to
``notes.json``)::

    {
      "version": 1,
      "jobs": {
        "4012345678": {
          "id": "4012345678", "title": "Senior Python Engineer",
          "company": "Acme", "location": "Remote (EU)",
          "url": "https://www.linkedin.com/jobs/view/4012345678/",
          "posted": "2026-09-18", "source": "apify",
          "status": "shortlisted", "score": 82,
          "why": "matches the async + Postgres line of the brief",
          "first_seen": 1758240000.0, "updated": 1758243600.0,
          "history": [["seen", 1758240000.0], ["shortlisted", 1758243600.0]]
        }
      }
    }

**This file is what makes the plugin automatic rather than a search box.** A
routine that runs every morning is only useful if it can tell what it already
showed you; ``mark_seen`` returns exactly the postings that are new, so the
digest is short and the agent stops re-scoring the same twenty listings.

Qt-free, offline, and machine-local (not part of cloud-synced settings -- a job
hunt is not something to replicate onto a work laptop by accident). Every
mutation rewrites the file atomically: the pipeline is tens of rows, and losing
one to a crash is worse than the write.

See docs/PLUGINS.md 19.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

__all__ = [
    "STATUSES",
    "TERMINAL_STATUSES",
    "Job",
    "LinkedInStore",
    "default_store_path",
]

#: Bumped only if the on-disk shape changes meaning.
STORE_VERSION = 1

#: The pipeline, in order. A job only ever moves forward through this list --
#: see :meth:`LinkedInStore.set_status`, which refuses to walk one backwards so
#: a re-run of the morning routine can't demote something already applied to.
STATUSES: tuple = (
    "seen",         # returned by a search, nothing decided
    "shortlisted",  # the agent scored it above the bar
    "drafted",      # a tailored application exists
    "applied",      # the human actually applied
    "replied",      # the company came back
    "closed",       # rejected, withdrawn, or filled
)

#: Statuses that end the story. ``closed`` is reachable from anywhere (a
#: rejection can land at any point) -- it is the one exception to forward-only.
TERMINAL_STATUSES: tuple = ("closed",)

_FIELDS = ("id", "title", "company", "location", "url", "posted", "source",
           "status", "score", "why", "first_seen", "updated")


def default_store_path() -> Path:
    # Tests redirect the pipeline here (see plugin_store's ADK_PLUGIN_STORE note).
    override = os.environ.get("ADK_LINKEDIN_JOBS")
    if override:
        return Path(override)
    try:
        from config import config_dir

        return config_dir() / "linkedin_jobs.json"
    except Exception:  # noqa: BLE001
        base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") \
            or os.path.join(os.path.expanduser("~"), ".config")
        return Path(base) / "multi-terminal" / "linkedin_jobs.json"


class Job(dict):
    """A pipeline row. A plain dict so it serialises straight to MCP output."""

    @property
    def id(self) -> str:
        return str(self.get("id", ""))

    @property
    def status(self) -> str:
        return str(self.get("status", "seen"))


def _clean(value: object, limit: int = 400) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text[:limit]


class LinkedInStore:
    """Read/write ``linkedin_jobs.json``. Best-effort; never raises."""

    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path) if path is not None else default_store_path()

    # -- raw ---------------------------------------------------------------

    def _read(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(text) if text.strip() else {}
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        jobs = data.get("jobs")
        return jobs if isinstance(jobs, dict) else {}

    def _write(self, jobs: dict) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # PID-scoped temp name, matching mcp_io.dump / PluginStore._write:
            # the MCP server process and the GUI can both be writing.
            tmp = self.path.with_name(f"{self.path.name}.adk{os.getpid()}.tmp")
            tmp.write_text(
                json.dumps({"version": STORE_VERSION, "jobs": jobs},
                           indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    # -- read ------------------------------------------------------------

    def all(self) -> List[Job]:
        """Every row, newest activity first."""
        rows = [Job(v) for v in self._read().values() if isinstance(v, dict)]
        rows.sort(key=lambda j: float(j.get("updated") or 0), reverse=True)
        return rows

    def get(self, job_id: str) -> Optional[Job]:
        row = self._read().get(str(job_id))
        return Job(row) if isinstance(row, dict) else None

    def by_status(self, status: str) -> List[Job]:
        want = (status or "").strip().lower()
        return [j for j in self.all() if j.status == want]

    def known_ids(self) -> set:
        return set(self._read().keys())

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for job in self.all():
            if job.status in out:
                out[job.status] += 1
        return out

    # -- write -----------------------------------------------------------

    def mark_seen(self, postings: Iterable[dict]) -> List[Job]:
        """Record a search result set; return **only the rows that are new**.

        The dedupe that the whole automation rests on. An id already in the file
        is refreshed (title/company/url can change) but is not returned, so a
        daily routine reports what actually appeared since yesterday.
        """
        jobs = self._read()
        now = time.time()
        fresh: List[Job] = []
        for posting in postings or []:
            if not isinstance(posting, dict):
                continue
            job_id = _clean(posting.get("id") or posting.get("job_id"), 64)
            if not job_id:
                continue
            row = jobs.get(job_id)
            if isinstance(row, dict):
                # Known: refresh the descriptive fields, leave the pipeline alone.
                for key in ("title", "company", "location", "url", "posted", "source"):
                    if posting.get(key):
                        row[key] = _clean(posting.get(key))
                row["updated"] = now
                jobs[job_id] = row
                continue
            row = {
                "id": job_id,
                "title": _clean(posting.get("title")),
                "company": _clean(posting.get("company")),
                "location": _clean(posting.get("location")),
                "url": _clean(posting.get("url"), 600),
                "posted": _clean(posting.get("posted"), 40),
                "source": _clean(posting.get("source"), 40),
                "status": "seen",
                "score": 0,
                "why": "",
                "first_seen": now,
                "updated": now,
                "history": [["seen", now]],
            }
            jobs[job_id] = row
            fresh.append(Job(row))
        self._write(jobs)
        return fresh

    def shortlist(self, job_id: str, score: object = 0, why: str = "") -> Optional[Job]:
        """Score a job and move it to ``shortlisted``."""
        jobs = self._read()
        row = jobs.get(str(job_id))
        if not isinstance(row, dict):
            return None
        try:
            row["score"] = max(0, min(100, int(float(score))))
        except (TypeError, ValueError):
            row["score"] = 0
        row["why"] = _clean(why, 600)
        self._advance(row, "shortlisted")
        jobs[str(job_id)] = row
        self._write(jobs)
        return Job(row)

    def set_status(self, job_id: str, status: str) -> Optional[Job]:
        """Move a job along the pipeline. ``None`` for an unknown id or an
        unknown status; a *backwards* move is ignored (the row comes back
        unchanged) rather than rejected, so a re-run of the morning routine is
        idempotent instead of noisy."""
        want = (status or "").strip().lower()
        if want not in STATUSES:
            return None
        jobs = self._read()
        row = jobs.get(str(job_id))
        if not isinstance(row, dict):
            return None
        self._advance(row, want)
        jobs[str(job_id)] = row
        self._write(jobs)
        return Job(row)

    @staticmethod
    def _advance(row: dict, want: str) -> None:
        current = str(row.get("status") or "seen")
        try:
            here = STATUSES.index(current)
        except ValueError:
            here = 0
        there = STATUSES.index(want)
        # closed is reachable from anywhere; everything else is forward-only.
        if want not in TERMINAL_STATUSES and there <= here:
            return
        now = time.time()
        row["status"] = want
        row["updated"] = now
        history = row.get("history")
        if not isinstance(history, list):
            history = []
        history.append([want, now])
        row["history"] = history[-20:]

    def forget(self, job_id: str) -> bool:
        jobs = self._read()
        if str(job_id) in jobs:
            del jobs[str(job_id)]
            return self._write(jobs)
        return True

    def clear(self) -> None:
        self._write({})
