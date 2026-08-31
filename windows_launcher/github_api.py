"""A thin ``requests`` wrapper over the bits of the GitHub REST API the Plugins
UI needs: who am I, which repos can I touch, what PRs are open.

Qt-free. Every call takes a bearer token (from ``github_auth.GitHubTokenStore``)
and raises :class:`github_auth.GitHubAuthError` on transport / auth failure so
``github_controller`` can treat it like the Supabase REST helpers in ``account``.
"""

from __future__ import annotations

from typing import List, Optional

import requests

from github_auth import GitHubAuthError

__all__ = ["whoami", "list_repos", "list_open_prs", "parse_pr_url"]

_API = "https://api.github.com"
_TIMEOUT = 20


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get(token: str, path: str, params: Optional[dict] = None) -> object:
    try:
        resp = requests.get(
            f"{_API}{path}", headers=_headers(token), params=params or {}, timeout=_TIMEOUT
        )
    except requests.RequestException as exc:
        raise GitHubAuthError(f"Couldn't reach GitHub: {exc}") from exc
    if resp.status_code in (401, 403) and "rate limit" not in resp.text.lower():
        raise GitHubAuthError("GitHub rejected the token — reconnect the plugin.")
    if not resp.ok:
        raise GitHubAuthError(f"GitHub API error {resp.status_code} on {path}")
    try:
        return resp.json()
    except ValueError:
        return {}


def whoami(token: str) -> dict:
    """``{login, name, avatar_url}`` for the connected account."""
    data = _get(token, "/user")
    if not isinstance(data, dict):
        return {}
    return {
        "login": data.get("login") or "",
        "name": data.get("name") or "",
        "avatar_url": data.get("avatar_url") or "",
    }


def list_repos(token: str, *, limit: int = 100) -> List[dict]:
    """Repos the token can see, most-recently-pushed first.

    For a GitHub App user token this is exactly the repos the App is installed
    on for this user; for a classic OAuth token it's everything they can push."""
    out: List[dict] = []
    # App installations expose repos under /user/installations/*/repositories;
    # fall back to /user/repos for the classic-OAuth path.
    try:
        inst = _get(token, "/user/installations", {"per_page": 100})
        installations = inst.get("installations", []) if isinstance(inst, dict) else []
    except GitHubAuthError:
        installations = []

    if installations:
        for install in installations:
            iid = install.get("id")
            if not iid:
                continue
            page = _get(
                token,
                f"/user/installations/{iid}/repositories",
                {"per_page": 100},
            )
            repos = page.get("repositories", []) if isinstance(page, dict) else []
            out.extend(repos)
    else:
        page = _get(token, "/user/repos", {"per_page": 100, "sort": "pushed"})
        if isinstance(page, list):
            out.extend(page)

    seen = set()
    clean: List[dict] = []
    for r in out:
        if not isinstance(r, dict):
            continue
        full = r.get("full_name")
        if not full or full in seen:
            continue
        seen.add(full)
        clean.append(
            {
                "full_name": full,
                "private": bool(r.get("private")),
                "pushed_at": r.get("pushed_at") or "",
                "default_branch": r.get("default_branch") or "main",
            }
        )
    clean.sort(key=lambda r: r["pushed_at"], reverse=True)
    return clean[:limit]


def list_open_prs(token: str, repo: str, *, limit: int = 50) -> List[dict]:
    """Open PRs for ``owner/name`` -> ``[{number, title, author, head, updated_at}]``."""
    data = _get(
        token,
        f"/repos/{repo}/pulls",
        {"state": "open", "per_page": min(100, limit), "sort": "updated", "direction": "desc"},
    )
    if not isinstance(data, list):
        return []
    out = []
    for pr in data:
        if not isinstance(pr, dict):
            continue
        out.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title") or "",
                "author": (pr.get("user") or {}).get("login") or "",
                "head": (pr.get("head") or {}).get("ref") or "",
                "updated_at": pr.get("updated_at") or "",
            }
        )
    return out[:limit]


def parse_pr_url(url: str) -> Optional[tuple[str, int]]:
    """``https://github.com/owner/name/pull/123`` -> ``("owner/name", 123)``."""
    text = (url or "").strip()
    if not text:
        return None
    import re

    m = re.search(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)", text)
    if m:
        return m.group(1), int(m.group(2))
    m = re.fullmatch(r"([^/\s]+/[^/\s]+)#(\d+)", text)
    if m:
        return m.group(1), int(m.group(2))
    return None
