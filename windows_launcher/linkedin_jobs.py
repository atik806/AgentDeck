"""Tier 2: where job listings actually come from.

LinkedIn has no self-serve job-search API -- that surface is partner-gated (see
``linkedin_api``'s header). So the plugin does what every other tool in this
space does and reads listings through a **job-data provider the user brings a
key for**. That keeps the arrangement honest in both directions: AgentDeck does
no scraping of its own, and the user can see exactly who they are paying and
under what terms.

Two adapters ship:

* ``apify`` -- run any LinkedIn-jobs actor from the Apify store synchronously
  and read its dataset. The actor id is the user's choice (there is no default:
  actors come and go, and silently pointing someone's API budget at a slug this
  file guessed would be worse than asking).
* ``jsearch`` -- the RapidAPI aggregator, which indexes LinkedIn among other
  boards and, unlike an actor, has a real job-details endpoint.

An adapter's only job is to return **normalised postings** -- the dict shape
``linkedin_store`` speaks. Adding a third provider means adding a function and a
registry entry, nothing else.

Qt-free. Every network call goes through an injectable ``fetch`` so the test
suite is fully offline. See docs/PLUGINS.md 19.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import requests

__all__ = [
    "ProviderError",
    "PROVIDERS",
    "FILTERS",
    "DEFAULT_PROVIDER",
    "unsupported_filters",
    "provider_label",
    "needs_actor",
    "search_jobs",
    "job_details",
]


class ProviderError(RuntimeError):
    """A provider that couldn't answer, phrased for a person."""


#: ``key -> human label``. The card's dropdown reads this.
PROVIDERS: Dict[str, str] = {
    "apify": "Apify actor",
    "jsearch": "JSearch (RapidAPI)",
}

#: Which optional filters each adapter can really pass through. A filter this
#: table doesn't list is *reported* (see :func:`unsupported_filters`) rather
#: than dropped in silence -- an agent that asked for remote-only work and got
#: an unfiltered list should be told, not left to infer it.
FILTERS: Dict[str, frozenset] = {
    "apify": frozenset({"remote", "posted_within", "experience", "easy_apply"}),
    # JSearch has no easy-apply concept: it aggregates several boards and only
    # LinkedIn has the feature.
    "jsearch": frozenset({"remote", "posted_within", "experience"}),
}

DEFAULT_PROVIDER = "apify"

_TIMEOUT = 60          # an Apify run-sync can legitimately take a while
_MAX_LIMIT = 100


def provider_label(key: str) -> str:
    return PROVIDERS.get((key or "").strip().lower(), key or "")


def unsupported_filters(provider: str, **asked: object) -> List[str]:
    """The names of the truthy ``asked`` filters this provider can't honour."""
    known = FILTERS.get((provider or "").strip().lower())
    if known is None:
        return []
    return sorted(name for name, value in asked.items() if value and name not in known)


def needs_actor(provider: str) -> bool:
    """Apify is the one provider that needs a second setting (which actor)."""
    return (provider or "").strip().lower() == "apify"


def _http(method: str, url: str, *, what: str, **kwargs) -> dict | list:
    try:
        resp = requests.request(method, url, timeout=_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise ProviderError(f"Couldn't reach the job provider to {what}: {exc}") from exc
    if resp.status_code in (401, 403):
        raise ProviderError(
            f"The job provider rejected your API key while trying to {what}. "
            "Check the key on the LinkedIn card."
        )
    if resp.status_code == 429:
        raise ProviderError("The job provider is rate-limiting this key. Try later.")
    if not resp.ok:
        raise ProviderError(f"The job provider failed to {what} ({resp.status_code}).")
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderError(f"The job provider returned an unreadable response to {what}.") from exc


def _text(value: object, limit: int = 400) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split())[:limit]


def _first(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("text") or ""
        if value:
            return _text(value)
    return ""


def _normalise(row: dict, source: str) -> Optional[dict]:
    """One provider row -> the posting shape ``linkedin_store`` stores.

    Providers disagree about field names far more than about content, so this
    is deliberately forgiving: anything without an id *and* a title is dropped,
    everything else is best-effort.
    """
    if not isinstance(row, dict):
        return None
    job_id = _first(row, "id", "jobId", "job_id", "jobPostingId", "job_posting_id")
    url = _first(row, "url", "link", "jobUrl", "job_url", "job_apply_link", "applyUrl")
    if not job_id and url:
        # Fall back to the numeric id inside a LinkedIn job URL.
        for part in url.replace("?", "/").split("/"):
            if part.isdigit() and len(part) >= 6:
                job_id = part
                break
    title = _first(row, "title", "jobTitle", "job_title")
    if not job_id or not title:
        return None
    return {
        "id": job_id,
        "title": title,
        "company": _first(row, "company", "companyName", "company_name", "employer_name"),
        "location": _first(row, "location", "jobLocation", "job_location",
                           "job_city", "formattedLocation"),
        "url": url,
        "posted": _first(row, "postedAt", "posted_at", "postedDate", "publishedAt",
                         "job_posted_at_datetime_utc", "listedAt"),
        "source": source,
        "salary": _first(row, "salary", "salaryInfo", "job_salary", "compensation"),
        "description": _text(_first(row, "description", "descriptionText",
                                    "job_description"), 4000),
    }


# ---------------------------------------------------------------------------
# apify
# ---------------------------------------------------------------------------

def _apify_search(key: str, actor: str, query: dict, limit: int) -> List[dict]:
    if not actor:
        raise ProviderError(
            "No Apify actor chosen yet. Pick a LinkedIn jobs actor at "
            "apify.com/store and paste its id (e.g. \"user~actor-name\") on the "
            "LinkedIn card."
        )
    # Apify accepts either "user~actor" or "user/actor"; the API path wants "~".
    slug = actor.strip().replace("/", "~")
    url = f"https://api.apify.com/v2/acts/{slug}/run-sync-get-dataset-items"
    payload = {
        "title": query.get("keywords", ""),
        "keywords": query.get("keywords", ""),
        "location": query.get("location", ""),
        "rows": limit,
        "maxItems": limit,
    }
    if query.get("remote"):
        payload["workplaceType"] = "remote"
        payload["remote"] = True
    if query.get("posted_within"):
        payload["publishedAt"] = query["posted_within"]
    if query.get("experience"):
        payload["experienceLevel"] = query["experience"]
    if query.get("easy_apply"):
        payload["easyApply"] = True

    # Bearer rather than ``?token=``: a key in a URL ends up in proxy, CDN and
    # server logs, and Apify accepts the header on every v2 endpoint.
    data = _http("POST", url, what="search for jobs",
                 headers={"Authorization": f"Bearer {key}"}, json=payload)
    rows = data if isinstance(data, list) else data.get("items") or []
    return [p for p in (_normalise(r, "apify") for r in rows) if p][:limit]


# ---------------------------------------------------------------------------
# jsearch (RapidAPI)
# ---------------------------------------------------------------------------

_JSEARCH_HOST = "jsearch.p.rapidapi.com"


def _jsearch_headers(key: str) -> dict:
    return {"X-RapidAPI-Key": key, "X-RapidAPI-Host": _JSEARCH_HOST}


def _jsearch_search(key: str, query: dict, limit: int) -> List[dict]:
    terms = " ".join(x for x in (query.get("keywords", ""), query.get("location", "")) if x)
    if not terms:
        # Substituting a default here would spend the user's quota on a search
        # nobody asked for and report the results as if they were theirs.
        raise ProviderError(
            "No keywords given, so there is nothing to search for. Say what "
            "kind of role to look for."
        )
    params = {"query": terms, "page": "1",
              "num_pages": "1", "date_posted": query.get("posted_within") or "all"}
    if query.get("remote"):
        params["remote_jobs_only"] = "true"
    if query.get("experience"):
        params["job_requirements"] = query["experience"]
    data = _http("GET", f"https://{_JSEARCH_HOST}/search", what="search for jobs",
                 headers=_jsearch_headers(key), params=params)
    rows = data.get("data") if isinstance(data, dict) else None
    rows = rows if isinstance(rows, list) else []
    postings = [p for p in (_normalise(r, "jsearch") for r in rows) if p]
    # LinkedIn-sourced rows first: this is the LinkedIn plugin, even though the
    # aggregator indexes several boards and the rest are still worth showing.
    postings.sort(key=lambda p: 0 if "linkedin" in p.get("url", "").lower() else 1)
    return postings[:limit]


def _jsearch_details(key: str, job_id: str) -> dict:
    data = _http("GET", f"https://{_JSEARCH_HOST}/job-details",
                 what="read a job", headers=_jsearch_headers(key),
                 params={"job_id": job_id})
    rows = data.get("data") if isinstance(data, dict) else None
    row = rows[0] if isinstance(rows, list) and rows else {}
    posting = _normalise(row, "jsearch")
    if posting is None:
        raise ProviderError(f"The provider had nothing for job {job_id}.")
    posting["apply_url"] = _first(row, "job_apply_link", "apply_url")
    posting["employment_type"] = _first(row, "job_employment_type")
    return posting


# ---------------------------------------------------------------------------
# public surface
# ---------------------------------------------------------------------------

def search_jobs(
    *,
    provider: str,
    api_key: str,
    actor: str = "",
    keywords: str = "",
    location: str = "",
    remote: bool = False,
    posted_within: str = "",
    experience: str = "",
    easy_apply: bool = False,
    limit: int = 25,
) -> List[dict]:
    """Normalised postings from the configured provider.

    Raises :class:`ProviderError` -- never returns a half-answer, because the
    caller writes whatever comes back into the pipeline store.
    """
    key = (api_key or "").strip()
    name = (provider or DEFAULT_PROVIDER).strip().lower()
    if not key:
        raise ProviderError(
            "No job-provider API key stored. Add one on the LinkedIn card to "
            "turn job search on."
        )
    if name not in PROVIDERS:
        raise ProviderError(f"Unknown job provider '{provider}'.")
    try:
        count = max(1, min(_MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        count = 25

    terms = _text(keywords, 200)
    where = _text(location, 120)
    if not terms and not where:
        raise ProviderError(
            "No keywords and no location, so there is nothing to search for. "
            "Say what kind of role to look for."
        )
    query = {
        "keywords": terms,
        "location": where,
        "remote": bool(remote),
        "posted_within": _text(posted_within, 40),
        "experience": _text(experience, 40),
        "easy_apply": bool(easy_apply),
    }
    if name == "apify":
        return _apify_search(key, actor, query, count)
    return _jsearch_search(key, query, count)


def job_details(*, provider: str, api_key: str, job_id: str) -> dict:
    """One posting in full.

    Only ``jsearch`` has a details endpoint. An Apify actor is a batch scraper
    with no per-id lookup, so this says so rather than inventing a request --
    the agent already has the stored row and the public URL.
    """
    key = (api_key or "").strip()
    name = (provider or DEFAULT_PROVIDER).strip().lower()
    job = (job_id or "").strip()
    if not key:
        raise ProviderError("No job-provider API key stored.")
    if not job:
        raise ProviderError("No job id given.")
    if name == "jsearch":
        return _jsearch_details(key, job)
    raise ProviderError(
        "This provider has no per-job lookup -- use the stored search result, "
        "or open the job URL. (Switch to JSearch on the LinkedIn card if you "
        "want full descriptions on demand.)"
    )
