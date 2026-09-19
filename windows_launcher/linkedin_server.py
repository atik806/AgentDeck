"""The LinkedIn MCP server -- AgentDeck's own, spoken over stdio.

Every other plugin points an agent at somebody else's hosted MCP endpoint. There
isn't one for LinkedIn, so this module *is* the server: an agent launches
AgentDeck with ``--linkedin-mcp`` (see ``main.py``'s sentinel and
``linkedin_mcp.canonical_server``) and talks JSON-RPC to this process over
stdin/stdout.

**Three rules this file exists to enforce.**

1. *No credential ever leaves this process.* The vault is read here, in-process
   -- nothing is written into an agent's config, passed on a command line, or
   copied anywhere an agent can read it. That is the whole reason the server is
   ours rather than a third-party binary.
2. *Tiers are checked per call, not per launch.* The user can switch job data or
   session reading off while an agent is mid-session; the next call must respect
   that, so settings are re-read on every request (two small file reads).
3. **No tool submits a job application.** ``linkedin_draft_application`` hands
   the agent the posting and the résumé and stops. Auto-apply is what gets
   LinkedIn accounts restricted and what makes the output worthless to whoever
   reads it. ``test_linkedin_server.py`` asserts no tool path can do it; if a
   future tool needs to, it needs a design decision and a docs section, not a
   flag.

Protocol: MCP over stdio is newline-delimited JSON-RPC 2.0, so there is no
dependency here beyond the stdlib -- same call as ``supabase_auth`` making
plain HTTPS calls rather than carrying an SDK into the frozen build.

Qt-free and importable without touching the GUI. See docs/PLUGINS.md 19.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

__all__ = ["main", "handle", "tool_list", "call_tool", "SERVER_NAME"]

SERVER_NAME = "linkedin"

#: Spoken back to a client that asks for something we don't recognise.
_DEFAULT_PROTOCOL = "2024-11-05"

#: A résumé longer than this is truncated before it goes to the agent -- a
#: tailoring prompt doesn't need a book, and a 200-page PDF-dump would blow the
#: pane's context window.
_RESUME_LIMIT = 8000


# ---------------------------------------------------------------------------
# Settings (re-read per request -- see rule 2 in the header)
# ---------------------------------------------------------------------------

def _connection():
    from plugin_store import LINKEDIN, PluginStore

    return PluginStore().get(LINKEDIN)


def _vault() -> dict:
    from linkedin_secret import LinkedInSecretStore

    return LinkedInSecretStore().load()


def _tiers(conn) -> set:
    """Which tiers are switched on right now. ``official`` is implied by having
    connected at all; the other two are opt-in."""
    if conn is None:
        return set()
    raw = str(conn.settings.get("tiers") or "official")
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


class ToolError(RuntimeError):
    """Anything the agent should see as a failed tool call."""


def _require(tier: str, conn) -> None:
    if tier not in _tiers(conn):
        raise ToolError(
            f"The LinkedIn plugin's '{tier}' tier is switched off. Turn it on in "
            "AgentDeck → Plugins → LinkedIn."
        )


def _token(vault: dict) -> str:
    token = vault.get("access_token") or ""
    if not token:
        raise ToolError(
            "Not signed in to LinkedIn. Connect the plugin in AgentDeck → "
            "Plugins → LinkedIn."
        )
    try:
        expires = float(vault.get("token_expires") or 0)
    except (TypeError, ValueError):
        expires = 0.0
    if expires and expires < time.time():
        raise ToolError(
            "The LinkedIn access token has expired. LinkedIn's self-serve apps "
            "get no refresh token, so reconnect the plugin to get a new one."
        )
    return token


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _schema(properties: dict, required: Optional[List[str]] = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


_TOOL_SPECS: List[dict] = [
    # -- tier 1: LinkedIn's own API ---------------------------------------
    {
        "name": "linkedin_me",
        "tier": "official",
        "description": "The signed-in LinkedIn member: name, email, member id.",
        "inputSchema": _schema({}),
    },
    {
        "name": "linkedin_post",
        "tier": "official",
        "description": (
            "Publish a post as the signed-in member. Defaults to CONNECTIONS "
            "visibility. Max 3000 characters."
        ),
        "inputSchema": _schema({
            "text": {"type": "string", "description": "The post body."},
            "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"],
                           "description": "Who can see it. Default CONNECTIONS."},
        }, ["text"]),
    },
    # -- tier 2: job data --------------------------------------------------
    {
        "name": "linkedin_search_jobs",
        "tier": "jobs",
        "description": (
            "Search job listings through the configured provider. Results are "
            "recorded in the local pipeline; the 'new' list holds only postings "
            "not seen before, which is what a scheduled run should report on."
        ),
        "inputSchema": _schema({
            "keywords": {"type": "string"},
            "location": {"type": "string"},
            "remote": {"type": "boolean"},
            "posted_within": {"type": "string",
                              "description": "e.g. today, 3days, week, month"},
            "experience": {"type": "string",
                           "description": "e.g. internship, entry_level, mid_level, senior"},
            "easy_apply": {"type": "boolean"},
            "limit": {"type": "integer", "description": "1-100, default 25"},
        }, ["keywords"]),
    },
    {
        "name": "linkedin_job_details",
        "tier": "jobs",
        "description": "Full detail for one job id, when the provider supports it.",
        "inputSchema": _schema({"job_id": {"type": "string"}}, ["job_id"]),
    },
    # -- the pipeline (local, always available once connected) -------------
    {
        "name": "linkedin_shortlist",
        "tier": "official",
        "description": (
            "Score a job 0-100 and move it to 'shortlisted', with a one-line "
            "reason. Call this instead of keeping a shortlist in your own notes."
        ),
        "inputSchema": _schema({
            "job_id": {"type": "string"},
            "score": {"type": "integer"},
            "why": {"type": "string"},
        }, ["job_id", "score"]),
    },
    {
        "name": "linkedin_track_application",
        "tier": "official",
        "description": (
            "Move a job along the pipeline: seen → shortlisted → drafted → "
            "applied → replied → closed. Backwards moves are ignored."
        ),
        "inputSchema": _schema({
            "job_id": {"type": "string"},
            "status": {"type": "string",
                       "enum": ["seen", "shortlisted", "drafted", "applied",
                                "replied", "closed"]},
        }, ["job_id", "status"]),
    },
    {
        "name": "linkedin_list_applications",
        "tier": "official",
        "description": "The local pipeline, optionally filtered to one status.",
        "inputSchema": _schema({
            "status": {"type": "string"},
            "limit": {"type": "integer"},
        }),
    },
    {
        "name": "linkedin_draft_application",
        "tier": "official",
        "description": (
            "Everything needed to write a tailored application for one job: the "
            "stored posting plus the configured résumé. It does NOT apply -- "
            "AgentDeck never submits an application; write the letter and let "
            "the human send it."
        ),
        "inputSchema": _schema({"job_id": {"type": "string"}}, ["job_id"]),
    },
    # -- tier 3: the member's own session (opt-in) -------------------------
    {
        "name": "linkedin_saved_jobs",
        "tier": "session",
        "description": "Jobs the member saved on LinkedIn (reads their session).",
        "inputSchema": _schema({"limit": {"type": "integer"}}),
    },
    {
        "name": "linkedin_my_applications",
        "tier": "session",
        "description": "Applications LinkedIn has on record for the member.",
        "inputSchema": _schema({"limit": {"type": "integer"}}),
    },
    {
        "name": "linkedin_unread_messages",
        "tier": "session",
        "description": "Unread LinkedIn conversations -- recruiter replies.",
        "inputSchema": _schema({"limit": {"type": "integer"}}),
    },
]


def tool_list(conn=None) -> List[dict]:
    """The tools to advertise, given which tiers are on.

    A switched-off tier's tools are *absent* rather than present-and-failing:
    an agent that can't see a tool won't plan around it.
    """
    conn = conn if conn is not None else _connection()
    tiers = _tiers(conn)
    return [
        {k: v for k, v in spec.items() if k != "tier"}
        for spec in _TOOL_SPECS
        if spec["tier"] in tiers
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _store():
    from linkedin_store import LinkedInStore

    return LinkedInStore()


def _resume_text(conn) -> str:
    path = str((conn.settings.get("resume_path") if conn else "") or "").strip()
    if not path:
        return ""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return raw[:_RESUME_LIMIT]


def _t_me(args: dict, conn, vault: dict) -> dict:
    import linkedin_api

    _require("official", conn)
    return linkedin_api.me(_token(vault))


def _t_post(args: dict, conn, vault: dict) -> dict:
    import linkedin_api

    _require("official", conn)
    return linkedin_api.create_post(
        _token(vault),
        str(args.get("text") or ""),
        visibility=str(args.get("visibility") or "CONNECTIONS"),
    )


def _t_search(args: dict, conn, vault: dict) -> dict:
    import linkedin_jobs

    _require("jobs", conn)
    settings = conn.settings if conn else {}
    postings = linkedin_jobs.search_jobs(
        provider=settings.get("provider", linkedin_jobs.DEFAULT_PROVIDER),
        api_key=vault.get("provider_key", ""),
        actor=settings.get("actor", ""),
        keywords=str(args.get("keywords") or ""),
        location=str(args.get("location") or ""),
        remote=bool(args.get("remote")),
        posted_within=str(args.get("posted_within") or ""),
        experience=str(args.get("experience") or ""),
        easy_apply=bool(args.get("easy_apply")),
        limit=args.get("limit", 25),
    )
    fresh = _store().mark_seen(postings)
    fresh_ids = {job["id"] for job in fresh}
    return {
        "found": len(postings),
        "new": [p for p in postings if p["id"] in fresh_ids],
        "already_seen": len(postings) - len(fresh_ids),
        "note": "Only 'new' postings are worth reporting -- the rest were "
                "returned by an earlier run.",
    }


def _t_details(args: dict, conn, vault: dict) -> dict:
    import linkedin_jobs

    _require("jobs", conn)
    settings = conn.settings if conn else {}
    return linkedin_jobs.job_details(
        provider=settings.get("provider", linkedin_jobs.DEFAULT_PROVIDER),
        api_key=vault.get("provider_key", ""),
        job_id=str(args.get("job_id") or ""),
    )


def _t_shortlist(args: dict, conn, vault: dict) -> dict:
    job = _store().shortlist(str(args.get("job_id") or ""),
                             args.get("score", 0), str(args.get("why") or ""))
    if job is None:
        raise ToolError("No such job in the pipeline -- search first.")
    return dict(job)


def _t_track(args: dict, conn, vault: dict) -> dict:
    job = _store().set_status(str(args.get("job_id") or ""),
                              str(args.get("status") or ""))
    if job is None:
        raise ToolError("No such job, or that isn't a pipeline status.")
    return dict(job)


def _t_list(args: dict, conn, vault: dict) -> dict:
    store = _store()
    status = str(args.get("status") or "").strip().lower()
    rows = store.by_status(status) if status else store.all()
    try:
        limit = max(1, min(200, int(args.get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    return {"counts": store.counts(), "jobs": [dict(j) for j in rows[:limit]]}


def _t_draft(args: dict, conn, vault: dict) -> dict:
    job = _store().get(str(args.get("job_id") or ""))
    if job is None:
        raise ToolError("No such job in the pipeline -- search first.")
    resume = _resume_text(conn)
    return {
        "job": dict(job),
        "resume": resume,
        "resume_configured": bool(resume),
        "instructions": (
            "Write the application from the job and the résumé above. Do not "
            "claim experience the résumé doesn't show. AgentDeck cannot submit "
            "the application and will not: give the draft to the human, then "
            "call linkedin_track_application with status 'drafted'."
        ),
    }


def _session_call(fn, args: dict, conn, vault: dict) -> dict:
    _require("session", conn)
    cookie = vault.get("li_at", "")
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    return {"items": fn(cookie, limit=limit),
            "note": "Read from your own LinkedIn session. Read-only, rate-limited."}


def _t_saved(args: dict, conn, vault: dict) -> dict:
    import linkedin_session

    return _session_call(linkedin_session.saved_jobs, args, conn, vault)


def _t_my_apps(args: dict, conn, vault: dict) -> dict:
    import linkedin_session

    return _session_call(linkedin_session.my_applications, args, conn, vault)


def _t_unread(args: dict, conn, vault: dict) -> dict:
    import linkedin_session

    return _session_call(linkedin_session.unread_messages, args, conn, vault)


_IMPLS: Dict[str, Callable[[dict, object, dict], dict]] = {
    "linkedin_me": _t_me,
    "linkedin_post": _t_post,
    "linkedin_search_jobs": _t_search,
    "linkedin_job_details": _t_details,
    "linkedin_shortlist": _t_shortlist,
    "linkedin_track_application": _t_track,
    "linkedin_list_applications": _t_list,
    "linkedin_draft_application": _t_draft,
    "linkedin_saved_jobs": _t_saved,
    "linkedin_my_applications": _t_my_apps,
    "linkedin_unread_messages": _t_unread,
}


def call_tool(name: str, args: Optional[dict] = None) -> dict:
    """Run one tool. Returns an MCP ``tools/call`` result -- a failure is
    ``isError`` content, not an exception, so the agent can read and retry."""
    args = args if isinstance(args, dict) else {}
    conn = _connection()
    if conn is None:
        return _err("The LinkedIn plugin isn't connected in AgentDeck.")
    impl = _IMPLS.get(name)
    if impl is None:
        return _err(f"Unknown tool '{name}'.")
    spec = next((s for s in _TOOL_SPECS if s["name"] == name), None)
    if spec is not None and spec["tier"] not in _tiers(conn):
        return _err(
            f"The LinkedIn plugin's '{spec['tier']}' tier is switched off. "
            "Turn it on in AgentDeck → Plugins → LinkedIn."
        )
    try:
        payload = impl(args, conn, _vault())
    except ToolError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001 - every provider error lands here
        return _err(f"{exc.__class__.__name__}: {exc}")
    return _ok(payload)


def _ok(payload: object) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "isError": False}


def _err(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------

def handle(message: dict) -> Optional[dict]:
    """One JSON-RPC request in, one response out (``None`` for a notification)."""
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "Invalid request")
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params")
    params = params if isinstance(params, dict) else {}

    if msg_id is None:  # a notification: acknowledge by staying silent
        return None

    if method == "initialize":
        asked = params.get("protocolVersion")
        return _rpc_result(msg_id, {
            "protocolVersion": asked if isinstance(asked, str) and asked else _DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
        })
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": tool_list()})
    if method == "tools/call":
        name = str(params.get("name") or "")
        return _rpc_result(msg_id, call_tool(name, params.get("arguments")))
    if method in ("resources/list", "prompts/list"):
        # Claude Code asks for these even when we advertise neither.
        key = "resources" if method.startswith("resources") else "prompts"
        return _rpc_result(msg_id, {key: []})
    if method == "shutdown":
        return _rpc_result(msg_id, {})
    return _rpc_error(msg_id, -32601, f"Method not found: {method}")


def _version() -> str:
    try:
        from version import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "0"


def _rpc_result(msg_id: object, result: object) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def main(stdin=None, stdout=None) -> int:
    """Serve MCP on stdio until the client closes the pipe.

    Nothing may be printed to stdout but JSON-RPC -- a stray ``print`` would
    corrupt the stream and the agent would drop the server. Diagnostics go to
    stderr, which the agent shows in its MCP log.
    """
    src = stdin if stdin is not None else sys.stdin
    dst = stdout if stdout is not None else sys.stdout
    # MCP is UTF-8 and newline-framed. Neither is Windows' default: a pipe
    # inherits the ANSI code page (cp1252 here), so the first tool description
    # containing an arrow killed the process mid-write, and "\n" would be
    # translated to "\r\n" on the way out. Both are reconfigured, and every
    # response is ASCII-escaped as well, so a stream that refuses to be
    # reconfigured still can't produce an unencodable byte.
    for stream, extra in ((dst, {"newline": "\n"}), (src, {})):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", **extra)
        except (ValueError, OSError, LookupError):
            pass

    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response = _rpc_error(None, -32700, "Parse error")
        else:
            try:
                response = handle(message)
            except Exception as exc:  # noqa: BLE001 - never die on one bad call
                print(f"[linkedin-mcp] {exc.__class__.__name__}: {exc}",
                      file=sys.stderr, flush=True)
                response = _rpc_error(message.get("id") if isinstance(message, dict) else None,
                                      -32603, "Internal error")
        if response is None:
            continue
        try:
            dst.write(json.dumps(response, ensure_ascii=True) + "\n")
            dst.flush()
        except (BrokenPipeError, OSError):
            return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main.py's sentinel
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
