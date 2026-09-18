"""Offline tests for mcp_io.py -- format-agnostic config load / atomic write / lock.

    .venv\\Scripts\\python.exe test_mcp_io.py
"""

import json
import os
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

import mcp_io

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
print("[1] JSON round-trip")
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "a.json"
    data, existed = mcp_io.load(p, "json")
    check("missing file -> ({}, False)", data == {} and existed is False)
    data["mcpServers"] = {"x": {"url": "u"}}
    check("dump ok", mcp_io.dump(p, data, "json"))
    check("tmp file cleaned", not list(Path(d).glob("*.adk*.tmp")))
    back, existed = mcp_io.load(p, "json")
    check("reads back", back["mcpServers"]["x"]["url"] == "u" and existed is True)


# ---------------------------------------------------------------------------
print("[2] malformed file -> (empty, True), bytes intact until a good dump")
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    data, existed = mcp_io.load(p, "json")
    check("empty mapping", hasattr(data, "get") and not data)
    check("existed True", existed is True)
    check("original bytes untouched by load", p.read_text(encoding="utf-8") == "{ not json")
    data["ok"] = 1
    mcp_io.dump(p, data, "json")
    check("clean file after dump", json.loads(p.read_text(encoding="utf-8")) == {"ok": 1})


# ---------------------------------------------------------------------------
print("[3] TOML round-trip (needs tomlkit)")
if mcp_io.toml_ok():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.toml"
        p.write_text('# keep me\nmodel = "o1"\n\n[mcp_servers.foo]\nurl = "x"\n', encoding="utf-8")
        data, existed = mcp_io.load(p, "toml")
        check("existed", existed and data["model"] == "o1")
        srv = mcp_io.get_in(data, ("mcp_servers",), create=True)
        srv["bar"] = {"url": "y", "bearer_token": "t"}
        check("dump ok", mcp_io.dump(p, data, "toml"))
        text = p.read_text(encoding="utf-8")
        check("user comment preserved", "# keep me" in text)
        check("existing server preserved", "[mcp_servers.foo]" in text)
        back, _ = mcp_io.load(p, "toml")
        check("new server reads back", back["mcp_servers"]["bar"]["bearer_token"] == "t")
else:
    check("SKIP tomlkit not installed", True)


# ---------------------------------------------------------------------------
print("[4] YAML round-trip (needs ruamel.yaml)")
if mcp_io.yaml_ok():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.yaml"
        p.write_text("# goose\nGOOSE_MODEL: gpt\nextensions:\n  builtin:\n    type: builtin\n", encoding="utf-8")
        data, existed = mcp_io.load(p, "yaml")
        check("existed", existed and data["GOOSE_MODEL"] == "gpt")
        ext = mcp_io.get_in(data, ("extensions",), create=True)
        ext["github"] = {"type": "streamable_http", "uri": "u", "enabled": True}
        check("dump ok", mcp_io.dump(p, data, "yaml"))
        text = p.read_text(encoding="utf-8")
        check("comment preserved", "# goose" in text)
        check("builtin preserved", "builtin" in text)
        back, _ = mcp_io.load(p, "yaml")
        check("new extension reads back", back["extensions"]["github"]["uri"] == "u")
else:
    check("SKIP ruamel.yaml not installed", True)


# ---------------------------------------------------------------------------
print("[5] get_in")
with tempfile.TemporaryDirectory() as d:
    check("missing path, create=False -> None", mcp_io.get_in({}, ("a", "b"), create=False) is None)
    root = {}
    leaf = mcp_io.get_in(root, ("a", "b"), create=True)
    leaf["k"] = 1
    check("nested create", root == {"a": {"b": {"k": 1}}})
    flat = {}
    m = mcp_io.get_in(flat, ("amp.mcpServers",), create=True)
    m["s"] = {"url": "u"}
    check("dotted key is one literal key", list(flat.keys()) == ["amp.mcpServers"])
    check("non-mapping node, create=False -> None",
          mcp_io.get_in({"a": 5}, ("a", "b"), create=False) is None)


# ---------------------------------------------------------------------------
print("[6] locked() serialises a read-modify-write (no lost update)")
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "counter.json"
    mcp_io.dump(p, {"n": 0}, "json")

    def bump():
        for _ in range(25):
            with mcp_io.locked(p):
                data, _ = mcp_io.load(p, "json")
                data["n"] = int(data["n"]) + 1
                mcp_io.dump(p, data, "json")
                time.sleep(0.001)

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = mcp_io.load(p, "json")[0]["n"]
    check(f"all 100 increments landed (got {final})", final == 100)


# ---------------------------------------------------------------------------
print("[7] locked() is re-entrant")
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "x.json"
    try:
        with mcp_io.locked(p):
            with mcp_io.locked(p):
                pass
        check("nested locked(same path) does not deadlock", True)
    except Exception as e:  # noqa: BLE001
        check(f"nested locked raised {e!r}", False)


# ---------------------------------------------------------------------------
# An agent config holds the GitHub plugin's bearer token, so the temp file dump()
# writes through is a credential too: owner-only, deleted on failure, and never
# left behind for a `git add -A` in someone's home directory to pick up.
print("[8] the temp file is treated as a secret")
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "claude.json"
    check("dump ok", mcp_io.dump(p, {"mcpServers": {"github": {"t": 1}}}, "json"))
    check("no temp left on success", not list(Path(d).glob("*.adk*.tmp")))

    if os.name != "nt":
        mode = stat.S_IMODE(p.stat().st_mode)
        check(f"config is 0600 (got {mode:04o})", mode == 0o600)
    else:
        check("mode check is POSIX-only (skipped on Windows)", True)

    # A write that blows up mid-way must not leave the plaintext copy behind.
    real_replace = os.replace

    def _boom(src, dst):
        raise OSError("disk full")

    os.replace = _boom
    try:
        ok = mcp_io.dump(p, {"mcpServers": {"github": {"t": 2}}}, "json")
    finally:
        os.replace = real_replace
    check("failed dump reports False", ok is False)
    check("failed dump leaves no temp behind", not list(Path(d).glob("*.adk*.tmp")))
    check("failed dump left the old file intact", mcp_io.load(p, "json")[0]["mcpServers"]["github"]["t"] == 1)

    # An orphan from a killed process (no one left to clean it up) is swept on
    # the next write past it -- but only once it is stale.
    orphan = Path(d) / "claude.json.adk99999.tmp"
    orphan.write_text('{"secret": "x"}', encoding="utf-8")
    os.utime(orphan, (time.time() - 3600, time.time() - 3600))
    fresh = Path(d) / "claude.json.adk99998.tmp"
    fresh.write_text('{"in": "flight"}', encoding="utf-8")

    check("dump ok", mcp_io.dump(p, {"mcpServers": {"github": {"t": 3}}}, "json"))
    check("stale orphan swept", not orphan.exists())
    check("another writer's in-flight temp untouched", fresh.exists())

    unrelated = Path(d) / "other.json.adk12345.tmp"
    unrelated.write_text("{}", encoding="utf-8")
    os.utime(unrelated, (time.time() - 3600, time.time() - 3600))
    check("dump ok", mcp_io.dump(p, {"mcpServers": {"github": {"t": 4}}}, "json"))
    check("a different file's temp is not swept", unrelated.exists())


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
