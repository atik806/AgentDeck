"""LinkedIn's *official*, self-serve REST surface -- tier 1 of the plugin.

Exactly two products are granted to any developer without a partner review, and
this module is both of them:

* **Sign In with LinkedIn using OpenID Connect** -- ``GET /v2/userinfo`` with the
  ``openid profile email`` scopes. Name, headline-less basic profile, picture,
  email.
* **Share on LinkedIn** -- ``POST /rest/posts`` with ``w_member_social``, which
  publishes as the authenticated member.

Everything else a job hunt might want from LinkedIn itself -- job search,
Recruiter, Sales Navigator, company analytics -- is partner-gated and is *not*
here. Tier 2 (``linkedin_jobs``) is where job data actually comes from. Do not
"fix" that by adding a voyager endpoint to this module: this one is the part
that is unambiguously sanctioned, and keeping it that way is the point.

Qt-free, ``requests``-based, and every call raises :class:`LinkedInError` with a
sentence fit to show a user rather than a stack trace.

See docs/PLUGINS.md 19.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

import requests

__all__ = [
    "LinkedInError",
    "API_BASE",
    "USERINFO_URL",
    "POSTS_URL",
    "REST_VERSION",
    "VISIBILITIES",
    "me",
    "create_post",
]

#: LinkedIn's versioned REST host. ``/v2/userinfo`` lives on the same host but
#: is an unversioned OIDC endpoint, hence the two constants.
API_BASE = "https://api.linkedin.com"
USERINFO_URL = f"{API_BASE}/v2/userinfo"
POSTS_URL = f"{API_BASE}/rest/posts"

#: ``LinkedIn-Version`` is mandatory on ``/rest/*`` and is a YYYYMM string.
#: LinkedIn keeps a version usable for about a year, so this is a maintenance
#: item, not a constant to forget: a 426 response means bump it.
REST_VERSION = "202601"

#: What ``create_post`` accepts. LinkedIn also has CONTAINER (group) visibility,
#: which needs a group id we have no way to pick, so it is left out.
VISIBILITIES = ("PUBLIC", "CONNECTIONS")

_TIMEOUT = 20


class LinkedInError(RuntimeError):
    """Something LinkedIn said no to, phrased for a person."""


def _headers(token: str, *, rest: bool = False) -> Dict[str, str]:
    head = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if rest:
        head["LinkedIn-Version"] = REST_VERSION
        head["Content-Type"] = "application/json"
    return head


def _check(resp: "requests.Response", what: str) -> None:
    if resp.ok:
        return
    detail = ""
    try:
        body = resp.json()
        detail = str(body.get("message") or body.get("error_description") or "").strip()
    except ValueError:
        detail = (resp.text or "").strip()[:200]
    if resp.status_code == 401:
        raise LinkedInError(
            f"{what} failed: LinkedIn rejected the access token. It may have "
            "expired -- reconnect the plugin from the Plugins page."
        )
    if resp.status_code == 403:
        raise LinkedInError(
            f"{what} failed: your LinkedIn app is missing a product. Add "
            "\"Sign In with LinkedIn using OpenID Connect\" and \"Share on "
            "LinkedIn\" on the app's Products tab, then reconnect."
        )
    if resp.status_code == 426:
        raise LinkedInError(
            f"{what} failed: LinkedIn retired API version {REST_VERSION}. "
            "AgentDeck needs updating (linkedin_api.REST_VERSION)."
        )
    if resp.status_code == 429:
        raise LinkedInError(f"{what} failed: LinkedIn is rate-limiting this app. Try later.")
    raise LinkedInError(f"{what} failed ({resp.status_code}){': ' + detail if detail else ''}.")


def _post_json(url: str, token: str, payload: dict, what: str) -> "requests.Response":
    try:
        resp = requests.post(url, headers=_headers(token, rest=True), json=payload,
                             timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise LinkedInError(f"Couldn't reach LinkedIn to {what.lower()}: {exc}") from exc
    _check(resp, what)
    return resp


# ---------------------------------------------------------------------------
# The two calls
# ---------------------------------------------------------------------------

def me(token: str) -> dict:
    """The signed-in member, from the OIDC ``userinfo`` endpoint.

    ``sub`` is the member's stable id -- and the thing ``create_post`` needs for
    the ``author`` URN, which is why posting starts with a call to here.
    """
    if not (token or "").strip():
        raise LinkedInError("Not connected to LinkedIn yet.")
    try:
        resp = requests.get(USERINFO_URL, headers=_headers(token), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise LinkedInError(f"Couldn't reach LinkedIn: {exc}") from exc
    _check(resp, "Reading your LinkedIn profile")
    try:
        data = resp.json()
    except ValueError as exc:
        raise LinkedInError("LinkedIn returned an unreadable profile response.") from exc
    return {
        "id": str(data.get("sub") or ""),
        "name": str(data.get("name") or ""),
        "given_name": str(data.get("given_name") or ""),
        "family_name": str(data.get("family_name") or ""),
        "email": str(data.get("email") or ""),
        "email_verified": bool(data.get("email_verified")),
        "picture": str(data.get("picture") or ""),
        "locale": str(data.get("locale") or "") if isinstance(data.get("locale"), str) else "",
    }


def create_post(token: str, text: str, *, visibility: str = "CONNECTIONS",
                author_id: Optional[str] = None) -> dict:
    """Publish ``text`` as the authenticated member.

    Defaults to ``CONNECTIONS`` rather than ``PUBLIC`` deliberately: this is a
    tool an agent can call, and the quieter default is the recoverable one.
    """
    body = (text or "").strip()
    if not body:
        raise LinkedInError("Nothing to post -- the text was empty.")
    if len(body) > 3000:
        raise LinkedInError(
            f"That post is {len(body)} characters; LinkedIn's limit is 3000."
        )
    vis = (visibility or "").strip().upper() or "CONNECTIONS"
    if vis not in VISIBILITIES:
        raise LinkedInError(f"Visibility must be one of {', '.join(VISIBILITIES)}.")

    author = (author_id or "").strip() or me(token)["id"]
    if not author:
        raise LinkedInError("Couldn't work out which member to post as.")

    payload = {
        "author": f"urn:li:person:{author}",
        "commentary": body,
        "visibility": vis,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    resp = _post_json(POSTS_URL, token, payload, "Posting to LinkedIn")
    urn = resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id") or ""
    return {
        "posted": True,
        "id": urn,
        "url": f"https://www.linkedin.com/feed/update/{urn}/" if urn else "",
        "visibility": vis,
        "characters": len(body),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
