"""Offline tests for plugin_store.py -- the local plugins.json + capability model.

    .venv\\Scripts\\python.exe test_plugin_store.py
"""

import sys
import tempfile
from pathlib import Path

from plugin_store import (
    GITHUB,
    VERCEL,
    JIRA,
    GITLAB,
    LINEAR,
    SUPABASE,
    PluginConnection,
    PluginStore,
    normalise_capabilities,
    toolsets_for,
)

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


# ---------------------------------------------------------------------------
print("[1] capability normalisation")
check("read is always present", "read" in normalise_capabilities([]))
check("unknown keys dropped", normalise_capabilities(["read", "bogus"]) == ["read"])
check("canonical order", normalise_capabilities(["actions", "review", "read"]) == ["read", "review", "actions"])
check("dedup", normalise_capabilities(["review", "review"]) == ["read", "review"])


# ---------------------------------------------------------------------------
print("[2] toolsets_for")
check("read -> read toolsets", "pull_requests" in toolsets_for(["read"]))
check("actions capability adds the actions toolset", "actions" in toolsets_for(["read", "actions"]))
check("no duplicates", len(toolsets_for(["read", "review", "write"])) == len(set(toolsets_for(["read", "review", "write"]))))
check("read-only selection has no actions toolset", "actions" not in toolsets_for(["read"]))


# ---------------------------------------------------------------------------
print("[3] PluginConnection")
c = PluginConnection(GITHUB, login="atik806", capabilities=["review"], automation={"review": "auto"})
check("login kept", c.login == "atik806")
check("caps normalised on construction", c.capabilities == ["read", "review"])
check("automation mode read back", c.automation_mode("review") == "auto")
check("default automation is ask", c.automation_mode("actions") == "ask")
check("round-trips", PluginConnection.from_dict(GITHUB, c.to_dict()).capabilities == ["read", "review"])
check("bad automation value coerced to ask",
      PluginConnection(GITHUB, automation={"review": "banana"}).automation_mode("review") == "ask")


# ---------------------------------------------------------------------------
print("[4] PluginStore persistence")
with tempfile.TemporaryDirectory() as d:
    store = PluginStore(Path(d) / "plugins.json")
    check("nothing connected initially", not store.is_connected(GITHUB))
    check("get on empty -> None", store.get(GITHUB) is None)

    store.put(PluginConnection(GITHUB, login="atik806", capabilities=["review"]))
    check("connected after put", store.is_connected(GITHUB))
    check("reads back", store.get(GITHUB).login == "atik806")

    updated = store.update(GITHUB, capabilities=["review", "actions"], automation={"actions": "auto"})
    check("update returns the patched connection", "actions" in updated.capabilities)
    check("update persisted", "actions" in store.get(GITHUB).capabilities)
    check("update automation persisted", store.get(GITHUB).automation_mode("actions") == "auto")

    check("update on missing provider -> None", store.update("gitlab", login="x") is None)

    store.remove(GITHUB)
    check("removed", not store.is_connected(GITHUB))
    check("remove is idempotent", store.remove(GITHUB) is True)


# ---------------------------------------------------------------------------
print("[5] PluginStore tolerates a corrupt file")
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "plugins.json"
    p.write_text("{ not json", encoding="utf-8")
    store = PluginStore(p)
    check("corrupt file -> not connected", not store.is_connected(GITHUB))
    check("can still write over it", store.put(PluginConnection(GITHUB, login="z")))
    check("and read back", store.get(GITHUB).login == "z")


# ---------------------------------------------------------------------------
print("[6] the store is provider-generic (Vercel -- thin, no capability model)")
check("VERCEL constant", VERCEL == "vercel")
with tempfile.TemporaryDirectory() as d:
    store = PluginStore(Path(d) / "plugins.json")
    check("vercel not connected initially", not store.is_connected(VERCEL))
    store.put(PluginConnection(VERCEL))
    check("connected after put", store.is_connected(VERCEL))
    check("round-trips", PluginConnection.from_dict(VERCEL, store.get(VERCEL).to_dict()).provider == VERCEL)
    check("github + vercel coexist in one file",
          store.put(PluginConnection(GITHUB, login="atik806"))
          and store.is_connected(GITHUB) and store.is_connected(VERCEL))
    store.remove(VERCEL)
    check("vercel removed, github untouched",
          not store.is_connected(VERCEL) and store.is_connected(GITHUB))


# ---------------------------------------------------------------------------
print("[7] the store is provider-generic (Jira -- thin, same as Vercel)")
check("JIRA constant", JIRA == "jira")
with tempfile.TemporaryDirectory() as d:
    store = PluginStore(Path(d) / "plugins.json")
    check("jira not connected initially", not store.is_connected(JIRA))
    store.put(PluginConnection(JIRA))
    check("connected after put", store.is_connected(JIRA))
    check("round-trips", PluginConnection.from_dict(JIRA, store.get(JIRA).to_dict()).provider == JIRA)
    check("github + vercel + jira coexist in one file",
          store.put(PluginConnection(GITHUB, login="atik806"))
          and store.put(PluginConnection(VERCEL))
          and store.is_connected(GITHUB) and store.is_connected(VERCEL) and store.is_connected(JIRA))
    store.remove(JIRA)
    check("jira removed, github + vercel untouched",
          not store.is_connected(JIRA) and store.is_connected(GITHUB) and store.is_connected(VERCEL))


# ---------------------------------------------------------------------------
print("[8] the store is provider-generic (GitLab -- thin, same as Vercel)")
check("GITLAB constant", GITLAB == "gitlab")
with tempfile.TemporaryDirectory() as d:
    store = PluginStore(Path(d) / "plugins.json")
    check("gitlab not connected initially", not store.is_connected(GITLAB))
    store.put(PluginConnection(GITLAB))
    check("connected after put", store.is_connected(GITLAB))
    check("round-trips", PluginConnection.from_dict(GITLAB, store.get(GITLAB).to_dict()).provider == GITLAB)


# ---------------------------------------------------------------------------
print("[9] the store is provider-generic (Linear -- thin, same as Vercel)")
check("LINEAR constant", LINEAR == "linear")
with tempfile.TemporaryDirectory() as d:
    store = PluginStore(Path(d) / "plugins.json")
    check("linear not connected initially", not store.is_connected(LINEAR))
    store.put(PluginConnection(LINEAR))
    check("connected after put", store.is_connected(LINEAR))
    check("round-trips", PluginConnection.from_dict(LINEAR, store.get(LINEAR).to_dict()).provider == LINEAR)
    check("all five providers coexist in one file",
          store.put(PluginConnection(GITHUB, login="atik806"))
          and store.put(PluginConnection(VERCEL))
          and store.put(PluginConnection(JIRA))
          and store.put(PluginConnection(GITLAB))
          and store.is_connected(GITHUB) and store.is_connected(VERCEL)
          and store.is_connected(JIRA) and store.is_connected(GITLAB)
          and store.is_connected(LINEAR))
    store.remove(LINEAR)
    check("linear removed, the other four untouched",
          not store.is_connected(LINEAR) and store.is_connected(GITHUB)
          and store.is_connected(VERCEL) and store.is_connected(JIRA)
          and store.is_connected(GITLAB))


print("[10] the store is provider-generic (Supabase -- thin, but carries a settings dict)")
check("SUPABASE constant", SUPABASE == "supabase")
with tempfile.TemporaryDirectory() as d:
    store = PluginStore(Path(d) / "plugins.json")
    check("supabase not connected initially", not store.is_connected(SUPABASE))

    conn = PluginConnection(SUPABASE, settings={"project_ref": "abcdefghijklmnopqrst", "read_only": "true"})
    check("settings kept on construction", conn.settings["project_ref"] == "abcdefghijklmnopqrst")
    store.put(conn)
    check("connected after put", store.is_connected(SUPABASE))
    check("settings round-trip through to_dict/from_dict",
          store.get(SUPABASE).settings == {"project_ref": "abcdefghijklmnopqrst", "read_only": "true"})

    updated = store.update(SUPABASE, settings={"project_ref": "zzzzzzzzzzzzzzzzzzzz", "read_only": "false"})
    check("update returns the patched connection", updated.settings["project_ref"] == "zzzzzzzzzzzzzzzzzzzz")
    check("update persisted", store.get(SUPABASE).settings["project_ref"] == "zzzzzzzzzzzzzzzzzzzz")

    check("a provider with no settings serialises without a 'settings' key",
          "settings" not in PluginConnection(GITLAB).to_dict())
    check("an empty settings dict round-trips to {} (not stored, not required)",
          PluginConnection.from_dict(GITLAB, PluginConnection(GITLAB).to_dict()).settings == {})

    check("all six providers coexist in one file",
          store.put(PluginConnection(GITHUB, login="atik806"))
          and store.put(PluginConnection(VERCEL))
          and store.put(PluginConnection(JIRA))
          and store.put(PluginConnection(GITLAB))
          and store.put(PluginConnection(LINEAR))
          and store.is_connected(GITHUB) and store.is_connected(VERCEL)
          and store.is_connected(JIRA) and store.is_connected(GITLAB)
          and store.is_connected(LINEAR) and store.is_connected(SUPABASE))
    store.remove(SUPABASE)
    check("supabase removed, the other five untouched",
          not store.is_connected(SUPABASE) and store.is_connected(GITHUB)
          and store.is_connected(VERCEL) and store.is_connected(JIRA)
          and store.is_connected(GITLAB) and store.is_connected(LINEAR))


print("[11] the store is provider-generic (Google Drive -- settings carry the client id, never the secret)")
from plugin_store import GDRIVE

check("GDRIVE constant", GDRIVE == "gdrive")
_GD_ID = "1234567890-abcdefg.apps.googleusercontent.com"
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "plugins.json"
    store = PluginStore(path)
    check("gdrive not connected initially", not store.is_connected(GDRIVE))
    store.put(PluginConnection(GDRIVE, settings={"client_id": _GD_ID, "scopes": "a b"}))
    check("connected after put", store.is_connected(GDRIVE))
    check("client id round-trips", store.get(GDRIVE).settings["client_id"] == _GD_ID)
    check("scopes round-trip", store.get(GDRIVE).settings["scopes"] == "a b")
    check("no login for this provider", store.get(GDRIVE).login == "")

    raw = path.read_text(encoding="utf-8")
    check("client id is on disk (it is not a secret)", _GD_ID in raw)
    check("no client_secret key is ever written", "client_secret" not in raw)
    check("no clientSecret key either", "clientSecret" not in raw)

    check("all seven providers coexist in one file",
          store.put(PluginConnection(GITHUB, login="atik806"))
          and store.put(PluginConnection(VERCEL))
          and store.put(PluginConnection(JIRA))
          and store.put(PluginConnection(GITLAB))
          and store.put(PluginConnection(LINEAR))
          and store.put(PluginConnection(SUPABASE, settings={"project_ref": "abcdefghijkl"}))
          and all(store.is_connected(p) for p in
                  (GITHUB, VERCEL, JIRA, GITLAB, LINEAR, SUPABASE, GDRIVE)))
    store.remove(GDRIVE)
    check("gdrive removed, the other six untouched",
          not store.is_connected(GDRIVE)
          and all(store.is_connected(p) for p in
                  (GITHUB, VERCEL, JIRA, GITLAB, LINEAR, SUPABASE)))


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
