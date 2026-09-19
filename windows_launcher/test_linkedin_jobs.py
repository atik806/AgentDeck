"""Offline tests for linkedin_jobs.py (tier 2) and linkedin_session.py (tier 3).

Both talk to the network through ``requests``; both are exercised here with a
stub in its place, so this suite never opens a socket.

    .venv\\Scripts\\python.exe test_linkedin_jobs.py
"""

import sys

import linkedin_jobs
import linkedin_session

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


class _Resp:
    def __init__(self, payload, status=200, ok=None):
        self._payload = payload
        self.status_code = status
        self.ok = (200 <= status < 300) if ok is None else ok
        self.text = "" if isinstance(payload, (dict, list)) else str(payload)
        self.headers = {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Recorder:
    """Stands in for ``requests.request`` / ``requests.get``."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


# ---------------------------------------------------------------------------
print("[1] guard rails before any request is made")
try:
    linkedin_jobs.search_jobs(provider="apify", api_key="", keywords="python")
    check("a missing key raises", False)
except linkedin_jobs.ProviderError as exc:
    check("a missing key raises before the network", "API key" in str(exc))
try:
    linkedin_jobs.search_jobs(provider="banana", api_key="k", keywords="python")
    check("an unknown provider raises", False)
except linkedin_jobs.ProviderError as exc:
    check("an unknown provider raises", "Unknown job provider" in str(exc))
try:
    linkedin_jobs.search_jobs(provider="apify", api_key="k", actor="", keywords="python")
    check("apify with no actor raises", False)
except linkedin_jobs.ProviderError as exc:
    check("apify with no actor says where to get one", "apify.com/store" in str(exc))
check("needs_actor is apify-only",
      linkedin_jobs.needs_actor("apify") and not linkedin_jobs.needs_actor("jsearch"))
check("provider_label reads the registry",
      linkedin_jobs.provider_label("jsearch") == "JSearch (RapidAPI)")


# ---------------------------------------------------------------------------
print("[2] apify -- a dataset of rows becomes normalised postings")
rec = _Recorder(_Resp([
    {"id": "4011", "title": "Senior Python Engineer", "companyName": "Acme",
     "location": "Remote", "jobUrl": "https://www.linkedin.com/jobs/view/4011/",
     "postedAt": "2026-09-18"},
    {"jobId": "4012", "jobTitle": "Backend Engineer", "company": {"name": "Globex"},
     "link": "https://www.linkedin.com/jobs/view/4012/"},
    {"title": "no id and no url -- dropped"},
]))
_real_request = linkedin_jobs.requests.request
linkedin_jobs.requests.request = rec
try:
    out = linkedin_jobs.search_jobs(provider="apify", api_key="tok", actor="user/actor",
                                    keywords="python", location="Berlin", remote=True, limit=10)
    check("two usable rows, the junk dropped", len(out) == 2)
    check("field names are unified", out[0]["company"] == "Acme" and out[0]["id"] == "4011")
    check("...across dialects", out[1]["company"] == "Globex" and out[1]["title"] == "Backend Engineer")
    check("source is tagged", all(p["source"] == "apify" for p in out))
    args, kwargs = rec.calls[-1]
    check("the actor slug is normalised to ~", "user~actor" in args[1])
    check("the key goes in the query, not the body", kwargs["params"]["token"] == "tok")
    check("remote is passed through", kwargs["json"]["remote"] is True)
    check("the limit is passed through", kwargs["json"]["maxItems"] == 10)
finally:
    linkedin_jobs.requests.request = _real_request


# ---------------------------------------------------------------------------
print("[3] jsearch -- LinkedIn rows float to the top")
rec = _Recorder(_Resp({"data": [
    {"job_id": "5001", "job_title": "Platform Engineer", "employer_name": "Initech",
     "job_apply_link": "https://www.indeed.com/viewjob?jk=5001"},
    {"job_id": "5002", "job_title": "Python Engineer", "employer_name": "Acme",
     "job_apply_link": "https://www.linkedin.com/jobs/view/5002/"},
]}))
linkedin_jobs.requests.request = rec
try:
    out = linkedin_jobs.search_jobs(provider="jsearch", api_key="rapid-key",
                                    keywords="python", location="Berlin", limit=5)
    check("the LinkedIn row is first", out[0]["id"] == "5002")
    check("both are returned", len(out) == 2)
    args, kwargs = rec.calls[-1]
    check("the key travels as a RapidAPI header",
          kwargs["headers"]["X-RapidAPI-Key"] == "rapid-key")
    check("keywords and location are combined into the query",
          "python" in kwargs["params"]["query"] and "Berlin" in kwargs["params"]["query"])
finally:
    linkedin_jobs.requests.request = _real_request


# ---------------------------------------------------------------------------
print("[4] provider failures are sentences, not statuses")
for status, fragment in ((401, "rejected your API key"), (403, "rejected your API key"),
                         (429, "rate-limiting"), (500, "failed")):
    linkedin_jobs.requests.request = _Recorder(_Resp({}, status=status))
    try:
        linkedin_jobs.search_jobs(provider="jsearch", api_key="k", keywords="x")
        check(f"{status} raises", False)
    except linkedin_jobs.ProviderError as exc:
        check(f"{status} -> \"{fragment}\"", fragment in str(exc))
    finally:
        linkedin_jobs.requests.request = _real_request

linkedin_jobs.requests.request = _Recorder(linkedin_jobs.requests.RequestException("no dns"))
try:
    linkedin_jobs.search_jobs(provider="jsearch", api_key="k", keywords="x")
    check("a dead network raises", False)
except linkedin_jobs.ProviderError as exc:
    check("a dead network is a ProviderError, not a requests error", "Couldn't reach" in str(exc))
finally:
    linkedin_jobs.requests.request = _real_request


# ---------------------------------------------------------------------------
print("[5] job_details -- only where the provider really has one")
rec = _Recorder(_Resp({"data": [{"job_id": "5002", "job_title": "Python Engineer",
                                 "employer_name": "Acme",
                                 "job_apply_link": "https://www.linkedin.com/jobs/view/5002/",
                                 "job_description": "Lots of Python."}]}))
linkedin_jobs.requests.request = rec
try:
    got = linkedin_jobs.job_details(provider="jsearch", api_key="k", job_id="5002")
    check("jsearch returns the posting", got["id"] == "5002")
    check("...with the description", "Lots of Python" in got["description"])
finally:
    linkedin_jobs.requests.request = _real_request
try:
    linkedin_jobs.job_details(provider="apify", api_key="k", job_id="1")
    check("apify says it has no lookup", False)
except linkedin_jobs.ProviderError as exc:
    check("apify says it has no per-job lookup rather than faking one",
          "no per-job lookup" in str(exc))


# ---------------------------------------------------------------------------
print("[6] session reading (tier 3) -- read-only, and honest when blocked")
linkedin_session._calls.clear()
linkedin_session.MIN_INTERVAL = 0.0     # the throttle is tested separately below

try:
    linkedin_session.saved_jobs("")
    check("no cookie raises", False)
except linkedin_session.SessionError as exc:
    check("no cookie -> 'paste a fresh li_at'", "li_at" in str(exc))

payload = {"elements": [
    {"title": "Senior Python Engineer", "companyName": "Acme",
     "entityUrn": "urn:li:fsd_jobPosting:4011", "secondaryDescription": "Remote"},
]}
rec = _Recorder(_Resp(payload))
try:
    out = linkedin_session.saved_jobs("li_at_value", limit=5, fetch=rec)
    check("a saved job is parsed", out and out[0]["title"] == "Senior Python Engineer")
    check("the job id is dug out of the urn", out[0]["id"] == "4011")
    check("...and turned into a URL", out[0]["url"].endswith("/jobs/view/4011/"))
    args, kwargs = rec.calls[-1]
    check("the cookie travels as a Cookie header", "li_at=li_at_value" in kwargs["headers"]["Cookie"])
    check("redirects are NOT followed (a login wall must not read as success)",
          kwargs["allow_redirects"] is False)
except linkedin_session.SessionError as exc:
    check(f"saved_jobs parsed cleanly ({exc})", False)

for status, fragment in ((302, "expired"), (401, "expired"), (999, "flagged"),
                         (429, "rate-limiting")):
    try:
        linkedin_session.saved_jobs("cookie", fetch=_Recorder(_Resp({}, status=status)))
        check(f"{status} raises", False)
    except linkedin_session.SessionError as exc:
        check(f"{status} -> \"{fragment}\"", fragment in str(exc))

try:
    linkedin_session.saved_jobs("cookie", fetch=_Recorder(_Resp(ValueError("html"), status=200)))
    check("an unparseable body raises", False)
except linkedin_session.SessionError as exc:
    check("an HTML body reads as 'LinkedIn changed its internal API'",
          "internal API" in str(exc))

check("no write path exists in the session module",
      not any(word in linkedin_session.__doc__.lower().split("read-only")[0]
              for word in ("apply(", "send(")))
_src = open(linkedin_session.__file__, encoding="utf-8").read()
check("...and it never issues a POST (REGRESSION)",
      "requests.post" not in _src and '"POST"' not in _src)


# ---------------------------------------------------------------------------
print("[7] the session throttle is a hard stop, not a suggestion")
linkedin_session._calls.clear()
linkedin_session._calls.extend([__import__("time").monotonic()] * linkedin_session.MAX_PER_HOUR)
try:
    linkedin_session.saved_jobs("cookie", fetch=_Recorder(_Resp({"elements": []})))
    check("the hourly cap raises", False)
except linkedin_session.SessionError as exc:
    check("the hourly cap stops the call", "this hour" in str(exc))
linkedin_session._calls.clear()


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
