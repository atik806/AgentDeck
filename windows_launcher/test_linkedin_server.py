"""Offline tests for linkedin_server.py -- the local MCP server.

Runs the protocol in-process (``handle``) and once for real over a pipe, with
``APPDATA`` redirected so plugins.json / the vault / the pipeline all land in a
sandbox. No network: the two tiers that would reach out are stubbed.

    .venv\\Scripts\\python.exe test_linkedin_server.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="adk-linkedinsrv-")
# config_dir() resolves through platformdirs' known-folder API, which ignores
# %APPDATA% -- so each store gets an explicit redirect, and the subprocess in
# [9] inherits them. Without this the suite would read and write the developer's
# real plugins.json / vault / pipeline.
os.environ["ADK_PLUGIN_STORE"] = str(Path(_SANDBOX) / "plugins.json")
os.environ["ADK_LINKEDIN_VAULT"] = str(Path(_SANDBOX) / "linkedin.bin")
os.environ["ADK_LINKEDIN_JOBS"] = str(Path(_SANDBOX) / "linkedin_jobs.json")

import linkedin_server
from plugin_store import LINKEDIN, PluginConnection, PluginStore

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


def _connect(tiers="official", **settings):
    base = {"tiers": tiers, "client_id": "abc", "provider": "apify", "actor": ""}
    base.update(settings)
    PluginStore().put(PluginConnection(LINKEDIN, settings=base))


def _disconnect():
    PluginStore().remove(LINKEDIN)


def _names(tools):
    return {t["name"] for t in tools}


def _payload(result):
    return json.loads(result["content"][0]["text"])


def _text(result):
    return result["content"][0]["text"]


# ---------------------------------------------------------------------------
print("[1] handshake")
init = linkedin_server.handle(
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18"}})
check("echoes the client's protocol version", init["result"]["protocolVersion"] == "2025-06-18")
check("names itself 'linkedin'", init["result"]["serverInfo"]["name"] == "linkedin")
check("advertises tools", "tools" in init["result"]["capabilities"])
old = linkedin_server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
check("falls back to a known version when none is asked for",
      old["result"]["protocolVersion"] == "2024-11-05")
check("ping answers", linkedin_server.handle(
    {"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"] == {})
check("a notification gets NO response (REGRESSION)",
      linkedin_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None)
check("unknown method -> JSON-RPC error", "error" in linkedin_server.handle(
    {"jsonrpc": "2.0", "id": 4, "method": "banana/split"}))
check("resources/list answers empty rather than erroring", linkedin_server.handle(
    {"jsonrpc": "2.0", "id": 5, "method": "resources/list"})["result"] == {"resources": []})


# ---------------------------------------------------------------------------
print("[2] tools/list follows the tiers")
_disconnect()
check("disconnected: no tools at all", linkedin_server.tool_list() == [])

_connect("official")
names = _names(linkedin_server.tool_list())
check("official tier: identity + posting", {"linkedin_me", "linkedin_post"} <= names)
check("...and the whole local pipeline",
      {"linkedin_shortlist", "linkedin_track_application",
       "linkedin_list_applications", "linkedin_draft_application"} <= names)
check("no job tools without the jobs tier", "linkedin_search_jobs" not in names)
check("no session tools by default (REGRESSION)", "linkedin_saved_jobs" not in names)

_connect("official,jobs")
names = _names(linkedin_server.tool_list())
check("jobs tier adds search + details",
      {"linkedin_search_jobs", "linkedin_job_details"} <= names)
check("still no session tools", "linkedin_saved_jobs" not in names)

_connect("official,jobs,session")
names = _names(linkedin_server.tool_list())
check("session tier adds its three reads",
      {"linkedin_saved_jobs", "linkedin_my_applications",
       "linkedin_unread_messages"} <= names)
check("eleven tools in total", len(names) == 11)
check("every tool has a schema",
      all(isinstance(t.get("inputSchema"), dict) for t in linkedin_server.tool_list()))
check("no tier key leaks into the wire format",
      all("tier" not in t for t in linkedin_server.tool_list()))


# ---------------------------------------------------------------------------
print("[3] NO tool can submit an application (REGRESSION -- docs/PLUGINS.md 19)")
_source = Path(__file__).with_name("linkedin_server.py").read_text(encoding="utf-8")
check("no tool is named apply/submit",
      not any(word in t["name"] for t in linkedin_server.tool_list()
              for word in ("apply", "submit")))
check("the draft tool says so in its description",
      "does NOT apply" in next(t["description"] for t in linkedin_server.tool_list()
                               if t["name"] == "linkedin_draft_application"))
_connect("official,jobs,session")
check("the only LinkedIn write path is the sanctioned post API",
      "create_post" in _source and "easyApply" not in _source)


# ---------------------------------------------------------------------------
print("[4] a switched-off tier refuses at call time, not just in the listing")
_connect("official")
res = linkedin_server.call_tool("linkedin_search_jobs", {"keywords": "python"})
check("calling a hidden tool is an error", res["isError"] is True)
check("...and says which switch to flip", "jobs" in _text(res) and "tier" in _text(res))
res = linkedin_server.call_tool("linkedin_saved_jobs", {})
check("same for session", res["isError"] is True and "session" in _text(res))
res = linkedin_server.call_tool("linkedin_nonsense", {})
check("unknown tool is a clean error", res["isError"] is True and "Unknown tool" in _text(res))
_disconnect()
res = linkedin_server.call_tool("linkedin_me", {})
check("disconnected is a clean error", res["isError"] is True and "connected" in _text(res))


# ---------------------------------------------------------------------------
print("[5] the pipeline tools work end to end (no network)")
_connect("official,jobs")
store = linkedin_server._store()
store.clear()
store.mark_seen([{"id": "2001", "title": "Python Engineer", "company": "Acme",
                  "url": "https://www.linkedin.com/jobs/view/2001/"}])

res = linkedin_server.call_tool("linkedin_shortlist",
                                {"job_id": "2001", "score": 88, "why": "stack match"})
check("shortlist succeeds", res["isError"] is False)
check("...and returns the row", _payload(res)["status"] == "shortlisted")

res = linkedin_server.call_tool("linkedin_list_applications", {})
check("list returns counts + rows", _payload(res)["counts"]["shortlisted"] == 1)

cv = Path(_SANDBOX) / "cv.md"
cv.write_text("# Jane Dev\n10 years of Python.", encoding="utf-8")
_connect("official,jobs", resume_path=str(cv))
res = linkedin_server.call_tool("linkedin_draft_application", {"job_id": "2001"})
draft = _payload(res)
check("draft carries the job", draft["job"]["id"] == "2001")
check("draft carries the CV", "10 years of Python" in draft["resume"])
check("draft REFUSES to apply, in words the agent reads",
      "cannot submit" in draft["instructions"])

res = linkedin_server.call_tool("linkedin_track_application",
                                {"job_id": "2001", "status": "drafted"})
check("tracking moves the row", _payload(res)["status"] == "drafted")
res = linkedin_server.call_tool("linkedin_track_application",
                                {"job_id": "nope", "status": "applied"})
check("an unknown job is an error, not a crash", res["isError"] is True)
res = linkedin_server.call_tool("linkedin_draft_application", {"job_id": "nope"})
check("drafting an unknown job is an error", res["isError"] is True)


# ---------------------------------------------------------------------------
print("[6] search folds the provider into the pipeline and reports only what's new")
_connect("official,jobs")
linkedin_server._store().clear()
import linkedin_jobs

_calls = []


def _fake_search(**kwargs):
    _calls.append(kwargs)
    return [{"id": "3001", "title": "Staff Engineer", "company": "Globex",
             "url": "https://www.linkedin.com/jobs/view/3001/", "source": "apify"},
            {"id": "3002", "title": "Backend Engineer", "company": "Initech",
             "url": "https://www.linkedin.com/jobs/view/3002/", "source": "apify"}]


_real_search = linkedin_jobs.search_jobs
linkedin_jobs.search_jobs = _fake_search
try:
    from linkedin_secret import LinkedInSecretStore

    LinkedInSecretStore().save(provider_key="k-123")
    res = linkedin_server.call_tool("linkedin_search_jobs", {"keywords": "python", "limit": 5})
    body = _payload(res)
    check("both are new the first time", len(body["new"]) == 2)
    check("found is the raw count", body["found"] == 2)
    check("the provider key came from the vault, not the config",
          _calls[-1]["api_key"] == "k-123")
    check("the key is NOT echoed back to the agent", "k-123" not in _text(res))

    res = linkedin_server.call_tool("linkedin_search_jobs", {"keywords": "python"})
    body = _payload(res)
    check("a second run reports NOTHING new (REGRESSION)", body["new"] == [])
    check("...but says how many it skipped", body["already_seen"] == 2)
finally:
    linkedin_jobs.search_jobs = _real_search


# ---------------------------------------------------------------------------
print("[7] a provider failure is a readable tool error, not a stack trace")
_connect("official,jobs")


def _boom(**_kwargs):
    raise linkedin_jobs.ProviderError("The job provider rejected your API key.")


linkedin_jobs.search_jobs = _boom
try:
    res = linkedin_server.call_tool("linkedin_search_jobs", {"keywords": "x"})
    check("marked as an error", res["isError"] is True)
    check("carries the provider's sentence", "rejected your API key" in _text(res))
    check("no traceback leaks", "Traceback" not in _text(res))
finally:
    linkedin_jobs.search_jobs = _real_search


# ---------------------------------------------------------------------------
print("[8] tier 1 needs a token, and an expired one says so")
_connect("official")
from linkedin_secret import LinkedInSecretStore

vault = LinkedInSecretStore()
vault.save(access_token="")
res = linkedin_server.call_tool("linkedin_me", {})
check("no token -> 'connect the plugin'", res["isError"] is True and "Connect" in _text(res))

vault.save(access_token="tok", token_expires="1")   # epoch 1 = long expired
res = linkedin_server.call_tool("linkedin_me", {})
check("expired token says to reconnect", res["isError"] is True and "expired" in _text(res))
vault.save(access_token="", token_expires="")


# ---------------------------------------------------------------------------
print("[9] a real process over a real pipe")
env = dict(os.environ)          # carries the three ADK_* redirects above
proc = subprocess.Popen(
    [sys.executable, "-u", str(Path(__file__).with_name("linkedin_server.py"))],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", env=env,
)


def rpc(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


try:
    reply = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    check("initialize over the pipe", reply["result"]["serverInfo"]["name"] == "linkedin")
    reply = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    check("tools/list over the pipe", "linkedin_me" in {t["name"] for t in reply["result"]["tools"]})
    # REGRESSION: a pipe on Windows inherits cp1252, and the first description
    # carrying an arrow used to kill the process mid-write.
    check("a non-ASCII description survives the pipe",
          "→" in next(t["description"] for t in reply["result"]["tools"]
                           if t["name"] == "linkedin_track_application"))
    proc.stdin.write("this is not json\n")
    proc.stdin.flush()
    reply = json.loads(proc.stdout.readline())
    check("garbage in -> parse error out, server stays up", reply["error"]["code"] == -32700)
    reply = rpc({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    check("...and it still answers", reply["result"] == {})
finally:
    proc.stdin.close()
    proc.wait(timeout=15)
check("closing stdin exits cleanly", proc.returncode == 0)
check("nothing was written to stderr", proc.stderr.read().strip() == "")


# ---------------------------------------------------------------------------
print("[10] the suite stayed inside its sandbox")
import config

real = config.config_dir()
check("plugins.json was written in the sandbox",
      (Path(_SANDBOX) / "plugins.json").exists())
check("the real plugins.json has no linkedin row (REGRESSION)",
      "linkedin" not in (real / "plugins.json").read_text(encoding="utf-8")
      if (real / "plugins.json").exists() else True)
check("no real vault was created", not (real / "linkedin.bin").exists())
check("no real pipeline was created", not (real / "linkedin_jobs.json").exists())


# ---------------------------------------------------------------------------
print("[11] search accounting -- the numbers the digest is built from")
_connect("official,jobs")
linkedin_server._store().clear()


def _dupes(**_kwargs):
    # A provider that returns the same posting twice, plus one with an id
    # longer than the store's key limit.
    long_id = "L" * 90
    row = {"id": "8001", "title": "Staff Engineer", "company": "Globex",
           "url": "https://www.linkedin.com/jobs/view/8001/", "source": "apify"}
    return [row, dict(row), {"id": long_id, "title": "Long Id Role",
                             "company": "Initech", "source": "apify"}]


linkedin_jobs.search_jobs = _dupes
try:
    body = _payload(linkedin_server.call_tool("linkedin_search_jobs", {"keywords": "python"}))
    check("a duplicate row is counted once", body["found"] == 2)
    check("...and reported as dropped", body.get("duplicates_dropped") == 1)
    check("a long provider id is still reported as new (REGRESSION)",
          len(body["new"]) == 2)
    check("nothing was skipped on a first run (REGRESSION)", body["already_seen"] == 0)
    body = _payload(linkedin_server.call_tool("linkedin_search_jobs", {"keywords": "python"}))
    check("the second run skips both", body["already_seen"] == 2 and body["new"] == [])
finally:
    linkedin_jobs.search_jobs = _real_search

res = linkedin_server.call_tool("linkedin_search_jobs", {"keywords": "   "})
check("a search with nothing to search for is an error, not a wasted call",
      res["isError"] is True and "keywords" in _text(res))


# ---------------------------------------------------------------------------
print("[12] a filter the provider can't honour is reported, not dropped")
_connect("official,jobs", provider="jsearch")


def _plain(**_kwargs):
    return [{"id": "8100", "title": "Remote Role", "company": "Acme", "source": "jsearch"}]


linkedin_jobs.search_jobs = _plain
try:
    body = _payload(linkedin_server.call_tool(
        "linkedin_search_jobs", {"keywords": "python", "easy_apply": True}))
    check("jsearch says it can't do easy-apply", body.get("ignored_filters") == ["easy_apply"])
    check("...in words the agent will relay", "cannot filter on" in body["note"])
    body = _payload(linkedin_server.call_tool(
        "linkedin_search_jobs", {"keywords": "python", "remote": True}))
    check("a filter it *can* do isn't flagged", "ignored_filters" not in body)
finally:
    linkedin_jobs.search_jobs = _real_search


# ---------------------------------------------------------------------------
print("[13] a CV it can't read is said so, not fed to the agent as mojibake")
_connect("official")
linkedin_server._store().mark_seen([{"id": "8200", "title": "Role", "company": "Acme"}])

pdf = Path(_SANDBOX) / "cv.pdf"
pdf.write_bytes(b"%PDF-1.7\x00\x01binary")
_connect("official", resume_path=str(pdf))
body = _payload(linkedin_server.call_tool("linkedin_draft_application", {"job_id": "8200"}))
check("a PDF CV is refused (REGRESSION)", body["resume"] == "")
check("...and not claimed as configured", body["resume_configured"] is False)
check("...with the reason spelled out", "can't read as text" in body["resume_problem"])
check("...carried into the drafting instructions",
      "Say so rather than drafting from nothing" in body["instructions"])

md = Path(_SANDBOX) / "cv.md"
md.write_text("# Jane Doe\nPython, Postgres.", encoding="utf-8")
_connect("official", resume_path=str(md))
body = _payload(linkedin_server.call_tool("linkedin_draft_application", {"job_id": "8200"}))
check("a markdown CV is read", "Jane Doe" in body["resume"])
check("...with no complaint", body["resume_problem"] == "")

md.write_text("   \n", encoding="utf-8")
body = _payload(linkedin_server.call_tool("linkedin_draft_application", {"job_id": "8200"}))
check("an empty CV is called empty", "is empty" in body["resume_problem"])


# ---------------------------------------------------------------------------
print("[14] a bogus pipeline status is a mistake, not an empty pipeline")
_connect("official")
res = linkedin_server.call_tool("linkedin_list_applications", {"status": "interviewing"})
check("marked as an error (REGRESSION)", res["isError"] is True)
check("...and lists the real ones", "shortlisted" in _text(res))
res = linkedin_server.call_tool("linkedin_list_applications", {"status": "seen"})
check("a real status still works", res["isError"] is False)


# ---------------------------------------------------------------------------
print("[15] protocol negotiation answers with a version we implement")
reply = linkedin_server.handle({"jsonrpc": "2.0", "id": 9, "method": "initialize",
                                "params": {"protocolVersion": "2099-01-01"}})
check("an unknown version is NOT echoed back (REGRESSION)",
      reply["result"]["protocolVersion"] == linkedin_server._DEFAULT_PROTOCOL)
check("...while a known one still is",
      linkedin_server.handle({"jsonrpc": "2.0", "id": 9, "method": "initialize",
                              "params": {"protocolVersion": "2025-03-26"}}
                             )["result"]["protocolVersion"] == "2025-03-26")


# ---------------------------------------------------------------------------
print("[16] session reads carry both cookies")
_connect("official,session")
LinkedInSecretStore().save(li_at="cookie-value", li_jsession="ajax:4242")
import linkedin_session

_seen = {}


def _spy(li_at, *, limit=20, jsession=""):
    _seen.update(li_at=li_at, jsession=jsession, limit=limit)
    return []


_real_saved = linkedin_session.saved_jobs
linkedin_session.saved_jobs = _spy
try:
    linkedin_server.call_tool("linkedin_saved_jobs", {"limit": 5})
    check("the li_at reaches the read", _seen.get("li_at") == "cookie-value")
    check("the JSESSIONID does too (REGRESSION)", _seen.get("jsession") == "ajax:4242")
finally:
    linkedin_session.saved_jobs = _real_saved


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
