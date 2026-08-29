"""Offline tests for agent discovery. Run:

    .venv\\Scripts\\python.exe test_agents.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import agents
from agents import (
    CUSTOM_KEY,
    PLAIN_KEY,
    agent_label,
    available_agents,
    install_hint,
    is_claude_command,
    is_installed,
    known_agents,
    pretrust_folder,
    resolve_agent,
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
print("[1] available_agents shape")
found = available_agents()
check("returns a list of 3-tuples",
      isinstance(found, list) and all(len(t) == 3 for t in found))
check("every reported agent is really on PATH",
      all(shutil.which(cmd) for _k, _l, cmd in found))
check("keys are unique", len({k for k, _l, _c in found}) == len(found))


# ---------------------------------------------------------------------------
print("[2] resolve_agent")
check("none -> empty", resolve_agent(PLAIN_KEY) == "")
check("'' -> empty", resolve_agent("") == "")
check("unknown key -> empty", resolve_agent("totally-not-an-agent") == "")
check("custom is trimmed",
      resolve_agent(CUSTOM_KEY, "  aider --model x  ") == "aider --model x")
check("custom empty -> empty", resolve_agent(CUSTOM_KEY, "   ") == "")

# a known-but-maybe-missing agent: command iff installed
claude = resolve_agent("claude")
check("known agent resolves to its command or '' (never a wrong string)",
      claude in ("claude", ""))
if shutil.which("claude"):
    check("claude present -> 'claude'", claude == "claude")


# ---------------------------------------------------------------------------
print("[3] labels")
check("plain label", agent_label(PLAIN_KEY) == "Plain shell")
check("custom label", agent_label(CUSTOM_KEY) == "Custom command")
check("known label", agent_label("claude") == "Claude Code")
check("unknown label falls back to the key", agent_label("weird") == "weird")


# ---------------------------------------------------------------------------
print("[3b] known_agents / is_installed / install_hint")
known = known_agents()
check("known_agents lists every _KNOWN entry", len(known) == len(agents._KNOWN))
check("available is a subset of known",
      {k for k, _l, _c in found}.issubset({k for k, _l, _c in known}))
check("is_installed agrees with available_agents",
      all(is_installed(k) for k, _l, _c in found))
check("is_installed(unknown) is False", is_installed("nope") is False)
for k, _l, _c in known:
    hint = install_hint(k)
    check(f"install_hint({k}) has a command + docs url",
          isinstance(hint, dict) and hint.get("command")
          and str(hint.get("docs", "")).startswith("http"))
check("install_hint(unknown) is None", install_hint("nope") is None)
check("install_hint(plain) is None", install_hint(PLAIN_KEY) is None)


# ---------------------------------------------------------------------------
print("[3c] all_agents / refresh_path")
allrows = agents.all_agents()
check("all_agents has one row per known agent", len(allrows) == len(known))
check("all_agents rows are (key, label, command, installed)",
      all(len(r) == 4 and isinstance(r[3], bool) for r in allrows))
check("all_agents installed flag agrees with available_agents",
      {k for k, _l, _c, ok in allrows if ok}
      == {k for k, _l, _c in available_agents()})
check("the expanded list includes the new agents",
      {"copilot", "antigravity", "amp", "qwen", "crush", "goose"}
      <= {k for k, _l, _c in known})
agents.refresh_path()   # must never raise
check("refresh_path() returned cleanly", True)


# ---------------------------------------------------------------------------
print("[4] stubbed PATH: nothing installed")
_real_which = shutil.which
try:
    shutil.which = lambda *_a, **_k: None
    check("no agents found when PATH is empty", available_agents() == [])
    check("is_installed is all False with an empty PATH",
          not any(is_installed(k) for k, _l, _c in known_agents()))
    check("known_agents still lists everything (for the install guide)",
          len(known_agents()) == len(agents._KNOWN))
    check("known key resolves to '' when not on PATH",
          resolve_agent("claude") == "")
    check("custom still works (no PATH check)",
          resolve_agent(CUSTOM_KEY, "echo hi") == "echo hi")
finally:
    shutil.which = _real_which


# ---------------------------------------------------------------------------
print("[5] is_claude_command")
check("bare claude", is_claude_command("claude"))
check("claude with args", is_claude_command("claude --resume"))
check("quoted path to claude", is_claude_command('"C:\\bin\\claude.exe" --foo'))
check("not claude", not is_claude_command("codex"))
check("empty", not is_claude_command(""))
check("claude as a substring only -> no", not is_claude_command("claudex"))


# ---------------------------------------------------------------------------
print("[6] pretrust_folder patches ~/.claude.json (temp copy)")
_real_cfg = agents._CLAUDE_CONFIG
tmp = Path(tempfile.mkdtemp()) / ".claude.json"
try:
    agents._CLAUDE_CONFIG = tmp

    # no file yet -> creates one, trusts the folder
    tmp.unlink(missing_ok=True)
    changed = pretrust_folder("claude", r"E:\Work\proj")
    data = json.loads(tmp.read_text())
    entry = data["projects"].get(r"E:\Work\proj") or data["projects"].get("E:/Work/proj")
    check("created config + trusted the folder",
          changed and entry and entry["hasTrustDialogAccepted"] is True)
    check("both path separators are covered",
          "E:/Work/proj" in data["projects"] and r"E:\Work\proj" in data["projects"])

    # idempotent -- already trusted, nothing to change
    check("second call is a no-op", pretrust_folder("claude", r"E:\Work\proj") is False)

    # existing config with other projects is preserved
    tmp.write_text(json.dumps({
        "numStartups": 7,
        "projects": {"C:/existing": {"hasTrustDialogAccepted": True, "allowedTools": ["x"]}},
    }))
    pretrust_folder("claude --resume", "C:/new/folder")
    data = json.loads(tmp.read_text())
    check("kept unrelated top-level keys", data.get("numStartups") == 7)
    check("kept the existing project untouched",
          data["projects"]["C:/existing"]["allowedTools"] == ["x"])
    check("trusted the new folder",
          data["projects"]["C:/new/folder"]["hasTrustDialogAccepted"] is True)

    # not a claude command -> never touches the file
    before = tmp.read_text()
    check("codex does not trigger a write",
          pretrust_folder("codex", "C:/whatever") is False and tmp.read_text() == before)
    check("plain shell does not trigger a write",
          pretrust_folder("", "C:/whatever") is False)

    # corrupt config -> best-effort, no raise
    tmp.write_text("{ not json")
    check("corrupt config is survived quietly",
          pretrust_folder("claude", "C:/x") is False)
finally:
    agents._CLAUDE_CONFIG = _real_cfg


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
