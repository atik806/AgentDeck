"""Tier 3: reading the member's *own* LinkedIn with their own session cookie.

**This is the risky tier, and it is off unless the user turns it on.** Read the
warning in ``docs/PLUGINS.md`` 19 before touching this file:

* LinkedIn's User Agreement 8.2 prohibits automated access. Using this can get
  the member's account restricted. The plugin therefore requires an explicit
  opt-in, says so in plain words on the card, and defaults to off.
* It is **read-only, by construction**. There is no write path in this module
  and there must never be one: applying, messaging, connecting and posting
  through a scraped session is exactly the behaviour that gets accounts killed,
  and posting already has a sanctioned route (``linkedin_api.create_post``).
* The endpoints below are LinkedIn's *internal* API. They are undocumented and
  unstable, and unlike every other module here they have **not** been verified
  against live LinkedIn. Treat a parse failure as "LinkedIn moved it", which is
  what :class:`SessionError` says, and prefer widening the forgiving parser over
  hard-coding a new response shape.

Rate limiting is deliberate and not configurable: a human reading their saved
jobs generates a request every few seconds, so this does too. It is both what
keeps the account out of trouble and the honest reading of "read-only".

Qt-free, injectable transport (offline tests). See docs/PLUGINS.md 19.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

import requests

__all__ = [
    "SessionError",
    "ENDPOINTS",
    "MIN_INTERVAL",
    "MAX_PER_HOUR",
    "saved_jobs",
    "my_applications",
    "unread_messages",
]


class SessionError(RuntimeError):
    """A session read that didn't work, phrased for a person."""


#: LinkedIn's internal (voyager) API. Undocumented, unstable, unverified -- see
#: the module header. One dict so a breakage is a one-line fix.
ENDPOINTS: Dict[str, str] = {
    "saved_jobs": "/voyager/api/voyagerJobsDashJobSeekerSavedJobs",
    "applications": "/voyager/api/voyagerJobsDashJobSeekerJobApplications",
    "conversations": "/voyager/api/voyagerMessagingDashMessengerConversations",
}

_BASE = "https://www.linkedin.com"

#: Seconds between requests, and the ceiling per process-hour. Human pace.
MIN_INTERVAL = 3.0
MAX_PER_HOUR = 60

_TIMEOUT = 20

#: ``[monotonic timestamps]`` of requests made this process.
_calls: List[float] = []


def _throttle() -> None:
    now = time.monotonic()
    global _calls
    _calls = [t for t in _calls if now - t < 3600]
    if len(_calls) >= MAX_PER_HOUR:
        raise SessionError(
            "AgentDeck has already made this hour's worth of LinkedIn session "
            "reads. This limit is deliberate -- it is what keeps the account "
            "out of trouble. Try again later."
        )
    if _calls:
        wait = MIN_INTERVAL - (now - _calls[-1])
        if wait > 0:
            time.sleep(wait)
    _calls.append(time.monotonic())


def _headers(li_at: str) -> Dict[str, str]:
    # csrf-token must equal the JSESSIONID cookie value; LinkedIn accepts any
    # matched pair, which is why both are set from one value here.
    csrf = "ajax:0000000000000000000"
    return {
        "Cookie": f"li_at={li_at}; JSESSIONID=\"{csrf}\"",
        "Csrf-Token": csrf,
        "Accept": "application/vnd.linkedin.normalized+json+2.1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "X-RestLi-Protocol-Version": "2.0.0",
    }


def _get(li_at: str, path: str, params: dict,
         fetch: Optional[Callable[..., object]] = None) -> dict:
    cookie = (li_at or "").strip()
    if not cookie:
        raise SessionError(
            "LinkedIn session reading is on but no session cookie is stored. "
            "Paste a fresh li_at on the LinkedIn card, or switch the tier off."
        )
    _throttle()
    call = fetch or requests.get
    try:
        resp = call(f"{_BASE}{path}", headers=_headers(cookie), params=params,
                    timeout=_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        raise SessionError(f"Couldn't reach LinkedIn: {exc}") from exc

    status = getattr(resp, "status_code", 0)
    if status in (401, 403) or status in (301, 302, 303, 307, 308):
        raise SessionError(
            "LinkedIn didn't accept the stored session -- it has expired or been "
            "invalidated. Copy a fresh li_at cookie from your browser, or switch "
            "session reading off."
        )
    if status == 999:
        # LinkedIn's "we think you're a bot" status.
        raise SessionError(
            "LinkedIn blocked this request (status 999) -- it flagged the access "
            "as automated. Stop using session reading for a while; continuing "
            "risks the account."
        )
    if status == 429:
        raise SessionError("LinkedIn is rate-limiting this session. Stop for a while.")
    if not (200 <= status < 300):
        raise SessionError(f"LinkedIn returned {status} for this session read.")
    try:
        data = resp.json()
    except ValueError as exc:
        raise SessionError(
            "LinkedIn returned something this version can't read -- it has most "
            "likely changed its internal API (linkedin_session.ENDPOINTS)."
        ) from exc
    return data if isinstance(data, dict) else {}


def _walk(value: object, out: List[dict], depth: int = 0) -> None:
    """Collect every dict that looks like a job/message card.

    LinkedIn nests its ``elements`` differently per surface and rearranges them
    without notice, so this looks for the *content* rather than a fixed path --
    the difference between a plugin that survives a redesign and one that
    doesn't.
    """
    if depth > 6 or len(out) >= 200:
        return
    if isinstance(value, list):
        for item in value:
            _walk(item, out, depth + 1)
        return
    if not isinstance(value, dict):
        return
    keys = set(value.keys())
    if keys & {"title", "jobPostingTitle", "subject"} and keys & {
        "companyName", "primaryDescription", "secondaryDescription", "entityUrn",
        "participants", "conversationUrn",
    }:
        out.append(value)
        return
    for item in value.values():
        _walk(item, out, depth + 1)


def _text(value: object, limit: int = 300) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("name") or ""
    return " ".join(str(value or "").split())[:limit]


def _job_card(row: dict) -> dict:
    urn = _text(row.get("entityUrn") or row.get("jobPostingUrn"), 200)
    # ``urn:li:fsd_jobPosting:4012345678`` -- the id is the last numeric
    # segment. Taking the *last* one rather than the first matters for the
    # compound urns LinkedIn uses on some surfaces
    # (``urn:li:fsd_jobPostingCard:(4012345678,JOB_DETAILS)``), where an
    # earlier segment can be numeric too.
    job_id = ""
    for part in urn.replace("(", ":").replace(")", ":").replace(",", ":").split(":"):
        if part.isdigit() and len(part) >= 3:
            job_id = part
    return {
        "id": job_id or urn,
        "title": _text(row.get("title") or row.get("jobPostingTitle")),
        "company": _text(row.get("companyName") or row.get("primaryDescription")),
        "location": _text(row.get("secondaryDescription") or row.get("formattedLocation")),
        "url": f"{_BASE}/jobs/view/{job_id}/" if job_id else "",
        "source": "session",
    }


def _cards(data: dict) -> List[dict]:
    found: List[dict] = []
    _walk(data.get("elements", data), found)
    return found


# ---------------------------------------------------------------------------
# The three reads
# ---------------------------------------------------------------------------

def saved_jobs(li_at: str, *, limit: int = 20, fetch=None) -> List[dict]:
    """Jobs the member saved on LinkedIn, newest first."""
    count = max(1, min(50, int(limit or 20)))
    data = _get(li_at, ENDPOINTS["saved_jobs"],
                {"q": "savedJobs", "start": 0, "count": count}, fetch)
    return [_job_card(row) for row in _cards(data)][:count]


def my_applications(li_at: str, *, limit: int = 20, fetch=None) -> List[dict]:
    """Applications LinkedIn has a record of for this member."""
    count = max(1, min(50, int(limit or 20)))
    data = _get(li_at, ENDPOINTS["applications"],
                {"q": "appliedJobs", "start": 0, "count": count}, fetch)
    return [_job_card(row) for row in _cards(data)][:count]


def unread_messages(li_at: str, *, limit: int = 20, fetch=None) -> List[dict]:
    """Unread conversations -- a recruiter reply is the one LinkedIn signal a
    job hunt actually turns on."""
    count = max(1, min(50, int(limit or 20)))
    data = _get(li_at, ENDPOINTS["conversations"],
                {"q": "unread", "count": count}, fetch)
    out = []
    for row in _cards(data):
        out.append({
            "subject": _text(row.get("subject") or row.get("title")),
            "preview": _text(row.get("snippet") or row.get("primaryDescription"), 200),
            "url": f"{_BASE}/messaging/",
            "source": "session",
        })
    return out[:count]
