"""Offline tests for agent_sessions (conversation handoff). Run:

    .venv\\Scripts\\python.exe test_agent_sessions.py

Writes its own fixtures under a temp ADK_AGENT_HOME_DIR -- never touches the
real ~/.claude, ~/.codex, or the opencode data dir.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_sandbox = Path(tempfile.mkdtemp(prefix="adk-sessions-"))
os.environ["ADK_AGENT_HOME_DIR"] = str(_sandbox)

import agent_sessions as S  # noqa: E402 - after the env override

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


PROJ = r"C:\Users\Atik Shahriar\proj"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_claude():
    slug = S._claude_slug(PROJ)
    d = _sandbox / "claude" / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    sid = "11111111-2222-3333-4444-555555555555"
    lines = [
        {"type": "queue-operation", "operation": "enqueue"},
        {"type": "mode", "mode": "normal", "sessionId": sid},
        {"type": "user", "message": {"role": "user", "content": "fix the parser bug"},
         "sessionId": sid, "cwd": PROJ},
        {"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [
            {"type": "thinking", "thinking": "secret plan: look at streams.py"},
            {"type": "text", "text": "Looking at the parser now."},
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "pytest -q"}},
        ]}, "sessionId": sid},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "3 passed"},
        ]}, "toolUseResult": {"stdout": "3 passed"}, "sessionId": sid},
        {"type": "assistant", "message": {"id": "m2", "role": "assistant", "content": [
            {"type": "text", "text": "Tests pass. Done."},
        ]}, "sessionId": sid},
        {"type": "system", "subtype": "turn_duration"},
        {"isMeta": True, "type": "user", "message": {"role": "user", "content": "META"}},
    ]
    p = d / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return sid, p


def _write_codex():
    d = _sandbox / "codex" / "sessions" / "2026" / "09" / "02"
    d.mkdir(parents=True, exist_ok=True)
    sid = "0199aaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    lines = [
        {"type": "session_meta", "payload": {"id": sid, "cwd": PROJ}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "<environment_context>\n  <cwd>x</cwd>\n</environment_context>"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "add a health endpoint"}]}},
        {"type": "response_item", "payload": {"type": "reasoning",
         "summary": [{"type": "text", "text": "hidden codex reasoning"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell",
         "arguments": "{\"command\": \"ls\"}", "call_id": "c1"}},
        {"type": "response_item", "payload": {"type": "function_call_output",
         "call_id": "c1", "output": "app.py\n"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "Added /health returning 200."}]}},
    ]
    p = d / f"rollout-2026-09-02T10-00-00-{sid}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return sid, p


def _write_opencode():
    root = _sandbox / "opencode"
    root.mkdir(parents=True, exist_ok=True)
    db = root / "opencode.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, title TEXT,
                              time_updated INTEGER);
        CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT,
                                      data TEXT, seq INTEGER);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                              time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT,
                           time_created INTEGER, data TEXT);
        """
    )
    sid = "ses_testopencode00000000000"
    con.execute("INSERT INTO session VALUES (?,?,?,?)",
                (sid, PROJ.replace("\\", "/"), "Refactor the store", 1788000000000))
    con.execute("INSERT INTO message VALUES (?,?,?,?)",
                ("msg1", sid, 1, json.dumps({"role": "user"})))
    con.execute("INSERT INTO message VALUES (?,?,?,?)",
                ("msg2", sid, 2, json.dumps({"role": "assistant"})))
    con.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                ("p1", "msg1", sid, 1, json.dumps({"type": "text", "text": "split notes_store"})))
    con.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                ("p2", "msg2", sid, 2, json.dumps({"type": "reasoning", "text": "opencode private reasoning"})))
    con.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                ("p3", "msg2", sid, 3, json.dumps({"type": "tool", "tool": "bash",
                 "state": {"input": {"command": "ls"}, "output": "a.py\n"}})))
    con.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                ("p4", "msg2", sid, 4, json.dumps({"type": "text", "text": "Done, split into two files."})))
    con.commit()
    con.close()
    return sid


CLAUDE_ID, CLAUDE_PATH = _write_claude()
CODEX_ID, CODEX_PATH = _write_codex()
OPENCODE_ID = _write_opencode()


# ---------------------------------------------------------------------------
print("[1] claude slug")
check("Users path", S._claude_slug(r"C:\Users\Atik Shahriar") == "C--Users-Atik-Shahriar")
check("spaces + drive", S._claude_slug(r"E:\VibeFlow Source Code\VibeFlow")
      == "E--VibeFlow-Source-Code-VibeFlow")


# ---------------------------------------------------------------------------
print("[2] locate_latest")
s = S.locate_latest("claude", PROJ)
check("claude hit", s is not None and s.session_id == CLAUDE_ID)
check("claude title = first user line", s is not None and s.title == "fix the parser bug")
check("claude path exists", s is not None and s.path and s.path.exists())
check("claude miss on unknown cwd", S.locate_latest("claude", r"C:\nowhere") is None)
check("claude any_cwd falls back",
      S.locate_latest("claude", r"C:\nowhere", any_cwd=True) is not None)

sc = S.locate_latest("codex", PROJ)
check("codex hit by cwd", sc is not None and sc.session_id == CODEX_ID)
check("codex title skips environment_context",
      sc is not None and sc.title == "add a health endpoint")

so = S.locate_latest("opencode", PROJ)
check("opencode hit by directory (slash-normalised)",
      so is not None and so.session_id == OPENCODE_ID)
check("opencode any_cwd", S.locate_latest("opencode", "x", any_cwd=True) is not None)
check("aider miss when no history file", S.locate_latest("aider", PROJ) is None)


# ---------------------------------------------------------------------------
print("[3] resume_command")
check("claude fork", S.resume_command("claude", "claude", s, fork=True)
      == f"claude --fork-session --resume {CLAUDE_ID}")
check("claude no fork", S.resume_command("claude", "claude", s, fork=False)
      == f"claude --resume {CLAUDE_ID}")
check("codex", S.resume_command("codex", "codex", sc) == f"codex resume {CODEX_ID}")
check("opencode fork", S.resume_command("opencode", "opencode", so, fork=True)
      == f"opencode --session {OPENCODE_ID} --fork")
check("opencode no fork", S.resume_command("opencode", "opencode", so, fork=False)
      == f"opencode --session {OPENCODE_ID}")
check("aider has no resume", S.resume_command("aider", "aider", s) is None)
check("amp has no resume", S.resume_command("amp", "amp", s) is None)


# ---------------------------------------------------------------------------
print("[4] transcript_markdown - claude")
md = S.transcript_markdown("claude", s)
check("has User section", "## User" in md and "fix the parser bug" in md)
check("has Assistant section", "## Assistant" in md and "Looking at the parser now." in md)
check("renders tool call", "### Tool call: `Bash`" in md and "pytest -q" in md)
check("renders tool result", "Tool result" in md and "3 passed" in md)
check("thinking excluded by default", "secret plan" not in md)
md_t = S.transcript_markdown("claude", s, include_thinking=True)
check("thinking included on request", "secret plan" in md_t)


# ---------------------------------------------------------------------------
print("[5] transcript_markdown - codex + opencode")
mdc = S.transcript_markdown("codex", sc)
check("codex User", "## User" in mdc and "add a health endpoint" in mdc)
check("codex Assistant", "## Assistant" in mdc and "/health returning 200" in mdc)
check("codex tool call", "### Tool call: `shell`" in mdc)
check("codex env context filtered from body", "<environment_context>" not in mdc)
check("codex reasoning gated", "hidden codex reasoning" not in mdc)

mdo = S.transcript_markdown("opencode", so)
check("opencode User", "## User" in mdo and "split notes_store" in mdo)
check("opencode Assistant text", "Done, split into two files." in mdo)
check("opencode tool part", "### Tool call: `bash`" in mdo)
check("opencode reasoning gated", "opencode private reasoning" not in mdo)


# ---------------------------------------------------------------------------
print("[6] truncation")
big_session = S.AgentSession(agent_key="claude", session_id="x",
                             path=CLAUDE_PATH, cwd=PROJ)
# Force a tiny budget: head+tail with an omission marker.
huge = _sandbox / "claude" / "projects" / S._claude_slug(PROJ) / "big.jsonl"
rows = [{"type": "user", "message": {"role": "user", "content": f"msg {i} " + "x" * 400}}
        for i in range(60)]
huge.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
bs = S.AgentSession(agent_key="claude", session_id="big", path=huge, cwd=PROJ)
tmd = S.transcript_markdown("claude", bs, max_chars=6000)
check("truncated output stays near budget", len(tmd) < 9000)
check("omission marker present", "omitted for length" in tmd or "earlier message(s) omitted" in tmd)

oversized = S.AgentSession(agent_key="claude", session_id="o", path=(
    _sandbox / "claude" / "projects" / S._claude_slug(PROJ) / "o.jsonl"), cwd=PROJ)
oversized.path.write_text(json.dumps({"type": "assistant", "message": {"id": "z",
    "role": "assistant", "content": [{"type": "tool_use", "name": "Bash",
    "input": {"x": "y" * 50}}]}}) + "\n" + json.dumps({"type": "user", "message": {
    "role": "user", "content": [{"type": "tool_result", "content": "Z" * 9000}]}}),
    encoding="utf-8")
omd = S.transcript_markdown("claude", oversized)
check("oversized tool result truncated", "[truncated]" in omd)


# ---------------------------------------------------------------------------
print("[7] initial_prompt_command + supports_resume + adapter_for")
check("claude takes prompt arg",
      S.initial_prompt_command("claude", "claude", "do X") == 'claude "do X"')
check("codex takes prompt arg",
      S.initial_prompt_command("codex", "codex", "do X") == 'codex "do X"')
check("opencode has no prompt arg", S.initial_prompt_command("opencode", "opencode", "x") is None)
check("prompt arg escapes quotes",
      S.initial_prompt_command("claude", "claude", 'say "hi"') == "claude \"say 'hi'\"")

for k in ("claude", "codex", "opencode", "goose"):
    check(f"supports_resume {k}", S.supports_resume(k) is True)
for k in ("aider", "amp", "copilot", "antigravity", "crush"):
    check(f"no resume for {k}", S.supports_resume(k) is False)

stub = S.adapter_for("totally-fake-agent")
check("adapter_for never None", stub is not None)
check("stub locate returns None", stub.locate_latest(PROJ) is None)
check("stub resume returns None", stub.resume_command("x", s, fork=True) is None)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
