"""Offline tests for plugin_store.py -- the local plugins.json + capability model.

    .venv\\Scripts\\python.exe test_plugin_store.py
"""

import sys
import tempfile
from pathlib import Path

from plugin_store import (
    GITHUB,
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


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
