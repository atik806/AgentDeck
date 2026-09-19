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

#: What ``_headers`` falls back to when no JSESSIONID was stored. It will not
#: work -- see :func:`_headers` -- and :func:`_get` turns the resulting 401 into
#: "paste the JSESSIONID cookie too".
_PLACEHOLDER_CSRF = "ajax:0000000000000000000"

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


def _headers(li_at: str, jsession: str = "") -> Dict[str, str]:
    # ``Csrf-Token`` must equal the *real* JSESSIONID cookie of the same
    # session: LinkedIn validates the pair against the session behind li_at, so
    # a synthetic value is rejected (401, or a 999 bot flag). The card asks for
    # both cookies for exactly this reason. The placeholder below is only a
    # last resort for a vault written by an older build -- expect it to fail,
    # and say so rather than pretending the read is broken some other way.
    csrf = (jsession or "").strip().strip('"') or _PLACEHOLDER_CSRF
    return {
        "Cookie": f"li_at={li_at}; JSESSIONID=\"{csrf}\"",
        "Csrf-Token": csrf,
        "Accept": "application/vnd.linkedin.normalized+json+2.1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "X-RestLi-Protocol-Version": "2.0.0",
    }


def _get(li_at: str, path: str, params: dict,
         fetch: Optional[Callable[..., object]] = None,
         jsession: str = "") -> dict:
    cookie = (li_at or "").strip()
    if not cookie:
        raise SessionError(
            "LinkedIn session reading is on but no session cookie is stored. "
            "Paste a fresh li_at on the LinkedIn card, or switch the tier off."
        )
    _throttle()
    call = fetch or requests.get
    try:
        resp = call(f"{_BASE}{path}", headers=_headers(cookie, jsession),
                    params=params, timeout=_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        raise SessionError(f"Couldn't reach LinkedIn: {exc}") from exc

    status = getattr(resp, "status_code", 0)
    if status in (401, 403) or status in (301, 302, 303, 307, 308):
        if not (jsession or "").strip():
            raise SessionError(
                "LinkedIn didn't accept the stored session, and no JSESSIONID "
                "cookie was stored -- its internal API checks that against "
                "li_at and rejects the request without it. Copy both cookies "
                "onto the LinkedIn card, or switch session reading off."
            )
        raise SessionError(
            "LinkedIn didn't accept the stored session -- it has expired or been "
            "invalidated. Copy a fresh li_at and JSESSIONID from your browser, "
            "or switch session reading off."
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


#: A job card is a title plus something that places it. Both halves are needed:
#: "title" on its own matches half of LinkedIn's UI metadata.
_JOB_TITLE_KEYS = frozenset({"title", "jobPostingTitle"})
_JOB_CONTEXT_KEYS = frozenset({
    "companyName", "primaryDescription", "secondaryDescription", "entityUrn",
    "jobPostingUrn", "formattedLocation",
})

#: A conversation, on the other hand, has no title at all on the current
#: messenger surface -- it is participants plus messages. Requiring a title here
#: (as this once did) meant ``unread_messages`` could never match anything, so
#: conversations get their own arm rather than sharing the job one's.
_MESSAGE_KEYS = frozenset({
    "conversationUrn", "participants", "messages", "subject", "snippet",
    "lastActivityAt", "unreadCount",
})


def _walk(value: object, out: List[tuple], depth: int = 0) -> None:
    """Collect every dict that looks like a job or conversation card.

    LinkedIn nests its ``elements`` differently per surface and rearranges them
    without notice, so this looks for the *content* rather than a fixed path --
    the difference between a plugin that survives a redesign and one that
    doesn't. Two independent shapes are recognised; see the key sets above.
    """
    if depth > 8 or len(out) >= 200:
        return
    if isinstance(value, list):
        for item in value:
            _walk(item, out, depth + 1)
        return
    if not isinstance(value, dict):
        return
    keys = set(value.keys())
    if keys & _JOB_TITLE_KEYS and keys & _JOB_CONTEXT_KEYS:
        out.append(("job", value))
        return
    if len(keys & _MESSAGE_KEYS) >= 2:
        out.append(("message", value))
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


def _cards(data: dict, want: str = "job") -> List[dict]:
    """Every card of one kind, anywhere in the response.

    Deliberately walks the *whole* document rather than ``data["elements"]``.
    The ``Accept`` header above asks for ``normalized+json``, whose shape is
    ``{"data": {"elements": [urn strings]}, "included": [...the real entities]}``
    -- so keying on ``elements`` found a list of URNs and never reached the
    cards in ``included``, which is to say it found nothing at all.
    """
    found: List[tuple] = []
    _walk(data, found)
    return [row for kind, row in found if kind == want]


# ---------------------------------------------------------------------------
# The three reads
# ---------------------------------------------------------------------------

def _name_of(value: object, depth: int = 0) -> str:
    """The first human name inside a participant blob.

    Participants are wrapped differently on every messenger revision
    (``{"name": ...}``, ``{"participantType": {"member": {"firstName": ...}}}``),
    so this digs rather than indexes.
    """
    if depth > 4:
        return ""
    if isinstance(value, str):
        return _text(value, 80)
    if isinstance(value, list):
        for item in value:
            found = _name_of(item, depth + 1)
            if found:
                return found
        return ""
    if not isinstance(value, dict):
        return ""
    # First/last before the single-key lookup: "firstName" is present on the
    # member shape too, and taking it alone would drop the surname.
    first = _text(value.get("firstName"), 40)
    last = _text(value.get("lastName"), 40)
    if first or last:
        return " ".join(x for x in (first, last) if x)
    for key in ("name", "fullName", "title"):
        if value.get(key):
            return _text(value.get(key), 80)
    for item in value.values():
        found = _name_of(item, depth + 1)
        if found:
            return found
    return ""


def _preview_of(row: dict) -> str:
    """The last message's text, wherever this revision keeps it."""
    for key in ("snippet", "preview", "primaryDescription"):
        text = _text(row.get(key), 200)
        if text:
            return text
    found: List[str] = []

    def dig(value: object, depth: int = 0) -> None:
        if found or depth > 5:
            return
        if isinstance(value, list):
            for item in value:
                dig(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        body = value.get("body")
        if body:
            text = _text(body, 200)
            if text:
                found.append(text)
                return
        for item in value.values():
            dig(item, depth + 1)

    dig(row.get("messages") or row.get("events") or {})
    return found[0] if found else ""


def saved_jobs(li_at: str, *, limit: int = 20, fetch=None,
               jsession: str = "") -> List[dict]:
    """Jobs the member saved on LinkedIn, newest first."""
    count = max(1, min(50, int(limit or 20)))
    data = _get(li_at, ENDPOINTS["saved_jobs"],
                {"q": "savedJobs", "start": 0, "count": count}, fetch,
                jsession=jsession)
    return [_job_card(row) for row in _cards(data, "job")][:count]


def my_applications(li_at: str, *, limit: int = 20, fetch=None,
                    jsession: str = "") -> List[dict]:
    """Applications LinkedIn has a record of for this member."""
    count = max(1, min(50, int(limit or 20)))
    data = _get(li_at, ENDPOINTS["applications"],
                {"q": "appliedJobs", "start": 0, "count": count}, fetch,
                jsession=jsession)
    return [_job_card(row) for row in _cards(data, "job")][:count]


def unread_messages(li_at: str, *, limit: int = 20, fetch=None,
                    jsession: str = "") -> List[dict]:
    """Unread conversations -- a recruiter reply is the one LinkedIn signal a
    job hunt actually turns on."""
    count = max(1, min(50, int(limit or 20)))
    data = _get(li_at, ENDPOINTS["conversations"],
                {"q": "unread", "start": 0, "count": count}, fetch,
                jsession=jsession)
    out = []
    for row in _cards(data, "message"):
        out.append({
            "from": _name_of(row.get("participants")),
            "subject": _text(row.get("subject") or row.get("title")),
            "preview": _preview_of(row),
            "url": f"{_BASE}/messaging/",
            "source": "session",
        })
    return out[:count]
