"""Locate and read a coding agent's on-disk conversation, for the handoff feature.

AgentDeck runs an agent (Claude Code, opencode, codex, …) as a CLI in a ConPTY
pane. This module lets one pane's conversation be handed to a new pane:

* **same-agent resume** -- build the agent's native resume command
  (``claude --resume <id>`` / ``opencode --session <id> --fork`` / …), so the
  new pane picks the session up with full fidelity; or
* **cross-agent transcript** -- render the conversation to a Markdown document
  that a *different* agent is pointed at as its opening task.

One :class:`SessionAdapter` per agent, in a registry keyed by the ``agents.py``
key. :func:`adapter_for` never returns ``None`` -- an agent with no real support
gets a :class:`_GenericAdapter` whose ``locate``/``resume``/``transcript`` all
return ``None`` so every call site degrades to "start the target fresh".

Deliberately Qt-free (same rule as ``agents.py`` / ``entitlements.py`` /
``mcp_targets.py``): plain functions and dataclasses, unit-testable headless.

Test hook: ``ADK_AGENT_HOME_DIR`` redirects every agent's state directory into
``<that dir>/<agent-key>/`` so a test never touches the real ``~/.claude`` etc.
-- mirrors ``mcp_targets._resolve_path`` / ``ADK_MCP_CONFIG_DIR``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

__all__ = [
    "AgentSession",
    "SessionAdapter",
    "adapter_for",
    "supports_resume",
    "known_session_agents",
    "locate_latest",
    "resume_command",
    "transcript_markdown",
    "initial_prompt_command",
]

#: Test / portable-mode override -- see the module docstring.
_HOME_ENV = "ADK_AGENT_HOME_DIR"

#: Hard cap on a single rendered tool result inside a transcript (chars).
_TOOL_RESULT_CHARS = 2000
#: Hard cap on a rendered tool-call input blob (chars).
_TOOL_INPUT_CHARS = 1200
#: Default transcript budget before head+tail truncation kicks in.
DEFAULT_MAX_CHARS = 200_000


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentSession:
    """A pointer to one agent conversation on disk."""

    agent_key: str
    #: The agent's own id: a uuid (claude/codex), ``ses_…`` (opencode), a name
    #: (goose). Passed straight to the resume command.
    session_id: str
    #: The transcript file, when the agent keeps one. ``None`` for SQLite-backed
    #: agents (opencode).
    path: Optional[Path] = None
    #: The directory the session was recorded against, if known.
    cwd: Optional[str] = None
    #: First user line / stored title -- shown in the handoff dialog.
    title: str = ""
    #: Last-modified epoch seconds, for "most recent" ranking.
    mtime: float = 0.0
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _sandbox_root(agent_key: str) -> Optional[Path]:
    box = os.environ.get(_HOME_ENV)
    return Path(box) / agent_key if box else None


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _xdg_data() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (_home() / ".local" / "share"))


def _claude_root() -> Path:
    return _sandbox_root("claude") or Path(
        os.environ.get("CLAUDE_CONFIG_DIR") or (_home() / ".claude")
    )


def _codex_root() -> Path:
    return _sandbox_root("codex") or Path(
        os.environ.get("CODEX_HOME") or (_home() / ".codex")
    )


def _opencode_root() -> Path:
    return _sandbox_root("opencode") or (_xdg_data() / "opencode")


def _goose_root() -> Path:
    return _sandbox_root("goose") or (_xdg_data() / "goose")


def _norm_dir(p: str | None) -> str:
    """A directory string reduced to a comparable form: forward slashes, no
    trailing sep, lower-cased (Windows paths are case-insensitive)."""
    if not p:
        return ""
    s = str(p).replace("\\", "/").rstrip("/")
    return s.lower()


def _newest(paths: Iterable[Path]) -> Optional[Path]:
    best: Optional[Path] = None
    best_m = -1.0
    for p in paths:
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > best_m:
            best, best_m = p, m
    return best


# ---------------------------------------------------------------------------
# Markdown assembly
# ---------------------------------------------------------------------------

def _trunc(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… [truncated]"


def _fence(text: str, lang: str = "") -> str:
    body = (text or "").rstrip("\n")
    # A body containing ``` would break the fence -- bump to a longer one.
    fence = "```"
    while fence in body:
        fence += "`"
    return f"{fence}{lang}\n{body}\n{fence}"


class _Doc:
    """Accumulates transcript sections, then renders with a header + optional
    head/tail truncation so a huge conversation still fits a prompt budget."""

    def __init__(self) -> None:
        self._sections: List[str] = []

    def add(self, text: str) -> None:
        t = (text or "").strip()
        if t:
            self._sections.append(t)

    def render(
        self,
        *,
        source_label: str,
        session: AgentSession,
        max_chars: int,
    ) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        head = [
            f"# Handoff from {source_label}",
            "",
            f"_Exported by AgentDeck {stamp} from session `{session.session_id}`._",
        ]
        if session.cwd:
            head.append(f"_Working directory: {session.cwd}_")
        if session.path:
            head.append(f"_Source transcript: {session.path}_")

        body, omitted = self._join(max_chars)
        if omitted:
            head.append(
                f"_[Truncated for length — {omitted} earlier message(s) omitted; "
                f"the most recent context is kept.]_"
            )
        head.append("")
        head.append("---")
        head.append("")
        return "\n".join(head) + "\n" + body + "\n"

    def _join(self, max_chars: int) -> tuple[str, int]:
        full = "\n\n".join(self._sections)
        if len(full) <= max_chars or len(self._sections) <= 2:
            return full, 0
        # Keep a small head (the task framing) and as much of the tail as fits
        # -- recent turns matter most for continuing the work.
        head_budget = max(2000, int(max_chars * 0.15))
        tail_budget = max_chars - head_budget

        head_parts: List[str] = []
        used = 0
        for sec in self._sections:
            if used + len(sec) > head_budget and head_parts:
                break
            head_parts.append(sec)
            used += len(sec) + 2

        tail_parts: List[str] = []
        used = 0
        for sec in reversed(self._sections[len(head_parts):]):
            if used + len(sec) > tail_budget and tail_parts:
                break
            tail_parts.append(sec)
            used += len(sec) + 2
        tail_parts.reverse()

        omitted = len(self._sections) - len(head_parts) - len(tail_parts)
        if omitted <= 0:
            return full, 0
        joiner = f"\n\n_[… {omitted} earlier message(s) omitted for length …]_\n\n"
        return "\n\n".join(head_parts) + joiner + "\n\n".join(tail_parts), omitted


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

class SessionAdapter:
    """Base adapter -- every hook is a no-op that returns ``None``/``False``.

    A real adapter overrides ``locate_latest`` plus one or both of
    ``resume_command`` / ``transcript_markdown``.
    """

    key = ""
    label = ""
    can_resume = False
    #: True when the agent can be launched as ``<cmd> "<initial prompt>"``.
    takes_initial_prompt_arg = False

    def locate_latest(
        self, cwd: Optional[str], *, any_cwd: bool = False
    ) -> Optional[AgentSession]:
        return None

    def resume_command(
        self, base_command: str, session: AgentSession, *, fork: bool
    ) -> Optional[str]:
        return None

    def transcript_markdown(
        self,
        session: AgentSession,
        *,
        include_thinking: bool = False,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> Optional[str]:
        return None

    def initial_prompt_command(
        self, base_command: str, prompt: str
    ) -> Optional[str]:
        if not self.takes_initial_prompt_arg:
            return None
        safe = prompt.replace('"', "'")
        return f'{base_command} "{safe}"'


class _GenericAdapter(SessionAdapter):
    """Fallback for agents with no session support -- claude/codex/gemini/qwen
    can still take an initial prompt on the command line."""

    def __init__(self, key: str, label: str, initial_prompt_arg: bool) -> None:
        self.key = key
        self.label = label
        self.takes_initial_prompt_arg = initial_prompt_arg


# -- Claude Code -------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")


def _claude_slug(path: str) -> str:
    """Claude Code's project-dir slug: every non-alphanumeric char -> ``-``.

    Verified: ``C:\\Users\\Atik Shahriar`` -> ``C--Users-Atik-Shahriar``,
    ``E:\\VibeFlow Source Code\\VibeFlow`` -> ``E--VibeFlow-Source-Code-VibeFlow``.
    """
    return _NON_ALNUM.sub("-", str(path).rstrip("\\/"))


def _slug_candidates(cwd: str) -> List[str]:
    forms = {cwd, cwd.replace("/", "\\"), cwd.replace("\\", "/")}
    try:
        forms.add(str(Path(cwd)))
        forms.add(os.path.abspath(cwd))
    except Exception:  # noqa: BLE001
        pass
    return [_claude_slug(f) for f in forms if f]


class _ClaudeAdapter(SessionAdapter):
    key = "claude"
    label = "Claude Code"
    can_resume = True
    takes_initial_prompt_arg = True

    def _projects_dir(self) -> Path:
        return _claude_root() / "projects"

    def _session_dir_for(self, cwd: str) -> Optional[Path]:
        projects = self._projects_dir()
        if not projects.is_dir():
            return None
        wanted = {s.lower() for s in _slug_candidates(cwd)}
        try:
            for child in projects.iterdir():
                if child.is_dir() and child.name.lower() in wanted:
                    return child
        except OSError:
            return None
        return None

    def locate_latest(self, cwd, *, any_cwd=False):
        dirs: List[Path] = []
        if cwd:
            d = self._session_dir_for(cwd)
            if d is not None:
                dirs.append(d)
        if any_cwd or (not dirs and not cwd):
            projects = self._projects_dir()
            if projects.is_dir():
                dirs = [p for p in projects.iterdir() if p.is_dir()]
        transcripts: List[Path] = []
        for d in dirs:
            try:
                transcripts += [p for p in d.glob("*.jsonl") if p.stat().st_size > 0]
            except OSError:
                continue
        best = _newest(transcripts)
        if best is None:
            return None
        return AgentSession(
            agent_key=self.key,
            session_id=best.stem,
            path=best,
            cwd=cwd,
            title=_first_user_line(best),
            mtime=best.stat().st_mtime,
        )

    def resume_command(self, base_command, session, *, fork):
        base = base_command or "claude"
        flag = "--fork-session --resume" if fork else "--resume"
        return f"{base} {flag} {session.session_id}"

    def transcript_markdown(self, session, *, include_thinking=False,
                            max_chars=DEFAULT_MAX_CHARS):
        if session.path is None or not session.path.exists():
            return None
        doc = _Doc()
        try:
            _render_claude_jsonl(session.path, doc, include_thinking)
        except OSError:
            return None
        return doc.render(source_label=self.label, session=session, max_chars=max_chars)


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _first_user_line(path: Path) -> str:
    for obj in _iter_jsonl(path):
        if obj.get("type") != "user" or obj.get("isMeta"):
            continue
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return content.strip().splitlines()[0][:120]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = str(block.get("text") or "").strip()
                    if t:
                        return t.splitlines()[0][:120]
    return ""


def _render_claude_jsonl(path: Path, doc: _Doc, include_thinking: bool) -> None:
    for obj in _iter_jsonl(path):
        typ = obj.get("type")
        if typ not in ("user", "assistant"):
            continue
        if obj.get("isMeta") or obj.get("isSidechain"):
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")

        if typ == "user":
            parts: List[str] = []
            if isinstance(content, str):
                text = content.strip()
                if text.startswith("<") and text.endswith(">") and "command-name" in text:
                    continue  # a slash-command envelope, not a real turn
                if text:
                    parts.append(text)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type")
                    if bt == "text":
                        parts.append(str(block.get("text") or ""))
                    elif bt == "tool_result":
                        res = block.get("content")
                        if isinstance(res, list):
                            res = "\n".join(
                                str(b.get("text") or "")
                                for b in res
                                if isinstance(b, dict)
                            )
                        parts.append(
                            "**Tool result:**\n"
                            + _fence(_trunc(str(res or ""), _TOOL_RESULT_CHARS))
                        )
                    elif bt == "image":
                        parts.append("_[image]_")
            body = "\n\n".join(p for p in parts if p and p.strip())
            if body.strip():
                doc.add(f"## User\n\n{body}")

        else:  # assistant
            if not isinstance(content, list):
                continue
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text":
                    parts.append(str(block.get("text") or ""))
                elif bt == "thinking" and include_thinking:
                    think = str(block.get("thinking") or "").strip()
                    if think:
                        quoted = "\n".join("> " + ln for ln in think.splitlines())
                        parts.append(f"_Thinking:_\n{quoted}")
                elif bt == "tool_use":
                    name = block.get("name") or "tool"
                    raw = json.dumps(block.get("input") or {}, indent=2, ensure_ascii=False)
                    parts.append(
                        f"### Tool call: `{name}`\n"
                        + _fence(_trunc(raw, _TOOL_INPUT_CHARS), "json")
                    )
            body = "\n\n".join(p for p in parts if p and p.strip())
            if body.strip():
                doc.add(f"## Assistant\n\n{body}")


# -- codex ------------------------------------------------------------------

class _CodexAdapter(SessionAdapter):
    key = "codex"
    label = "Codex"
    can_resume = True
    takes_initial_prompt_arg = True

    def _rollouts(self) -> List[Path]:
        base = _codex_root() / "sessions"
        if not base.is_dir():
            return []
        return list(base.rglob("rollout-*.jsonl"))

    def locate_latest(self, cwd, *, any_cwd=False):
        rollouts = self._rollouts()
        if not rollouts:
            return None
        want = _norm_dir(cwd)
        chosen: Optional[Path] = None
        if want and not any_cwd:
            best_m = -1.0
            for p in rollouts:
                meta = _codex_meta(p)
                if meta and _norm_dir(meta.get("cwd")) == want:
                    m = p.stat().st_mtime
                    if m > best_m:
                        chosen, best_m = p, m
        if chosen is None and (any_cwd or not want):
            chosen = _newest(rollouts)
        if chosen is None:
            return None
        meta = _codex_meta(chosen) or {}
        return AgentSession(
            agent_key=self.key,
            session_id=str(meta.get("id") or chosen.stem.split("-")[-1]),
            path=chosen,
            cwd=meta.get("cwd") or cwd,
            title=_codex_first_user(chosen),
            mtime=chosen.stat().st_mtime,
        )

    def resume_command(self, base_command, session, *, fork):
        base = base_command or "codex"
        return f"{base} resume {session.session_id}"

    def transcript_markdown(self, session, *, include_thinking=False,
                            max_chars=DEFAULT_MAX_CHARS):
        if session.path is None or not session.path.exists():
            return None
        doc = _Doc()
        try:
            _render_codex_rollout(session.path, doc, include_thinking)
        except OSError:
            return None
        return doc.render(source_label=self.label, session=session, max_chars=max_chars)


def _codex_meta(path: Path) -> Optional[dict]:
    for obj in _iter_jsonl(path):
        if obj.get("type") == "session_meta":
            return obj.get("payload") or {}
        break
    return None


def _codex_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(b.get("text") or "")
            for b in content
            if isinstance(b, dict) and b.get("type") in ("input_text", "output_text", "text")
        )
    return ""


def _codex_first_user(path: Path) -> str:
    for obj in _iter_jsonl(path):
        if obj.get("type") != "response_item":
            continue
        p = obj.get("payload") or {}
        if p.get("type") == "message" and p.get("role") == "user":
            text = _codex_text(p.get("content")).strip()
            if text and not text.startswith("<environment_context>"):
                return text.splitlines()[0][:120]
    return ""


def _render_codex_rollout(path: Path, doc: _Doc, include_thinking: bool) -> None:
    for obj in _iter_jsonl(path):
        if obj.get("type") != "response_item":
            continue
        p = obj.get("payload") or {}
        pt = p.get("type")
        if pt == "message":
            role = p.get("role")
            text = _codex_text(p.get("content")).strip()
            if not text or text.startswith("<environment_context>"):
                continue
            heading = "User" if role == "user" else "Assistant"
            doc.add(f"## {heading}\n\n{text}")
        elif pt == "reasoning" and include_thinking:
            summary = p.get("summary") or p.get("content")
            text = _codex_text(summary).strip() if summary else ""
            if not text and isinstance(summary, list):
                text = "\n".join(
                    str(s.get("text") or "") for s in summary if isinstance(s, dict)
                ).strip()
            if text:
                quoted = "\n".join("> " + ln for ln in text.splitlines())
                doc.add(f"_Thinking:_\n{quoted}")
        elif pt == "function_call":
            name = p.get("name") or "tool"
            args = p.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args or {}, ensure_ascii=False)
            doc.add(
                f"### Tool call: `{name}`\n"
                + _fence(_trunc(args, _TOOL_INPUT_CHARS), "json")
            )
        elif pt == "function_call_output":
            out = p.get("output")
            if isinstance(out, dict):
                out = out.get("content") or json.dumps(out, ensure_ascii=False)
            doc.add(
                "**Tool result:**\n"
                + _fence(_trunc(str(out or ""), _TOOL_RESULT_CHARS))
            )


# -- opencode -------------------------------------------------------------

class _OpencodeAdapter(SessionAdapter):
    key = "opencode"
    label = "opencode"
    can_resume = True
    takes_initial_prompt_arg = False  # its positional arg is non-interactive `run`

    def _db(self) -> Optional[Path]:
        db = _opencode_root() / "opencode.db"
        return db if db.exists() else None

    def _connect(self, db: Path) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)

    def locate_latest(self, cwd, *, any_cwd=False):
        db = self._db()
        if db is None:
            return None
        want = _norm_dir(cwd)
        try:
            con = self._connect(db)
        except sqlite3.Error:
            return None
        try:
            rows = list(
                con.execute(
                    "SELECT id, directory, title, time_updated "
                    "FROM session ORDER BY time_updated DESC"
                )
            )
        except sqlite3.Error:
            return None
        finally:
            con.close()
        chosen = None
        if want and not any_cwd:
            for sid, directory, title, updated in rows:
                if _norm_dir(directory) == want:
                    chosen = (sid, directory, title, updated)
                    break
        if chosen is None and (any_cwd or not want) and rows:
            chosen = rows[0]
        if chosen is None:
            return None
        sid, directory, title, updated = chosen
        return AgentSession(
            agent_key=self.key,
            session_id=str(sid),
            path=None,
            cwd=directory or cwd,
            title=str(title or "")[:120],
            mtime=(updated or 0) / 1000.0,
        )

    def resume_command(self, base_command, session, *, fork):
        base = base_command or "opencode"
        cmd = f"{base} --session {session.session_id}"
        return cmd + " --fork" if fork else cmd

    def transcript_markdown(self, session, *, include_thinking=False,
                            max_chars=DEFAULT_MAX_CHARS):
        doc = _Doc()
        ok = self._render_from_db(session, doc, include_thinking)
        if not ok:
            ok = self._render_from_export(session, doc, include_thinking)
        if not ok:
            return None
        return doc.render(source_label=self.label, session=session, max_chars=max_chars)

    # -- rendering paths --

    def _render_from_db(self, session, doc, include_thinking) -> bool:
        db = self._db()
        if db is None:
            return False
        try:
            con = self._connect(db)
        except sqlite3.Error:
            return False
        try:
            # Newer schema first (session_message), then the legacy message/part
            # tables (what ships on 1.18.x here).
            n = con.execute(
                "SELECT count(*) FROM session_message WHERE session_id=?",
                (session.session_id,),
            ).fetchone()[0]
            if n:
                return _render_opencode_session_message(con, session.session_id, doc,
                                                        include_thinking)
            return _render_opencode_message_part(con, session.session_id, doc,
                                                 include_thinking)
        except sqlite3.Error:
            return False
        finally:
            con.close()

    def _render_from_export(self, session, doc, include_thinking) -> bool:
        exe = shutil.which("opencode")
        if not exe:
            return False
        try:
            proc = subprocess.run(
                [exe, "export", session.session_id],
                capture_output=True, text=True, timeout=25,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if proc.returncode != 0 or not proc.stdout.strip():
            return False
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            return False
        _render_opencode_export(data, doc, include_thinking)
        return True


def _opencode_parts_to_md(parts: list, include_thinking: bool) -> List[str]:
    out: List[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        pt = part.get("type")
        if pt == "text":
            t = str(part.get("text") or "").strip()
            if t:
                out.append(t)
        elif pt == "reasoning" and include_thinking:
            t = str(part.get("text") or "").strip()
            if t:
                out.append("\n".join("> " + ln for ln in t.splitlines()))
        elif pt == "tool":
            name = part.get("tool") or "tool"
            state = part.get("state") or {}
            raw = json.dumps(state.get("input") or {}, indent=2, ensure_ascii=False)
            chunk = (
                f"### Tool call: `{name}`\n"
                + _fence(_trunc(raw, _TOOL_INPUT_CHARS), "json")
            )
            output = state.get("output")
            if output:
                chunk += "\n\n**Tool result:**\n" + _fence(
                    _trunc(str(output), _TOOL_RESULT_CHARS)
                )
            out.append(chunk)
    return out


def _render_opencode_message_part(con, session_id, doc, include_thinking) -> bool:
    msgs = list(
        con.execute(
            "SELECT id, data FROM message WHERE session_id=? ORDER BY time_created",
            (session_id,),
        )
    )
    if not msgs:
        return False
    parts_by_msg: dict[str, list] = {}
    for mid, data in con.execute(
        "SELECT message_id, data FROM part WHERE session_id=? ORDER BY time_created",
        (session_id,),
    ):
        try:
            parts_by_msg.setdefault(mid, []).append(json.loads(data))
        except ValueError:
            continue
    for mid, data in msgs:
        try:
            info = json.loads(data)
        except ValueError:
            continue
        role = info.get("role")
        heading = "User" if role == "user" else "Assistant"
        chunks = _opencode_parts_to_md(parts_by_msg.get(mid, []), include_thinking)
        body = "\n\n".join(chunks).strip()
        if body:
            doc.add(f"## {heading}\n\n{body}")
    return True


def _render_opencode_session_message(con, session_id, doc, include_thinking) -> bool:
    rows = list(
        con.execute(
            "SELECT type, data FROM session_message WHERE session_id=? ORDER BY seq",
            (session_id,),
        )
    )
    if not rows:
        return False
    for _typ, data in rows:
        try:
            info = json.loads(data)
        except ValueError:
            continue
        role = info.get("role") or (info.get("info") or {}).get("role")
        parts = info.get("parts") or []
        heading = "User" if role == "user" else "Assistant"
        chunks = _opencode_parts_to_md(parts, include_thinking)
        body = "\n\n".join(chunks).strip()
        if body:
            doc.add(f"## {heading}\n\n{body}")
    return True


def _render_opencode_export(data: dict, doc: _Doc, include_thinking: bool) -> None:
    for message in data.get("messages") or []:
        info = message.get("info") or {}
        role = info.get("role")
        heading = "User" if role == "user" else "Assistant"
        chunks = _opencode_parts_to_md(message.get("parts") or [], include_thinking)
        body = "\n\n".join(chunks).strip()
        if body:
            doc.add(f"## {heading}\n\n{body}")


# -- aider (transcript is already Markdown) --------------------------------

class _AiderAdapter(SessionAdapter):
    key = "aider"
    label = "Aider"
    can_resume = False
    takes_initial_prompt_arg = False

    def locate_latest(self, cwd, *, any_cwd=False):
        if not cwd:
            return None
        f = Path(cwd) / ".aider.chat.history.md"
        if not f.exists() or f.stat().st_size == 0:
            return None
        return AgentSession(
            agent_key=self.key,
            session_id="",
            path=f,
            cwd=cwd,
            title="Aider chat history",
            mtime=f.stat().st_mtime,
        )

    def transcript_markdown(self, session, *, include_thinking=False,
                            max_chars=DEFAULT_MAX_CHARS):
        if session.path is None or not session.path.exists():
            return None
        try:
            raw = session.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        doc = _Doc()
        doc.add(raw.strip())
        return doc.render(source_label=self.label, session=session, max_chars=max_chars)


# -- goose ----------------------------------------------------------------

class _GooseAdapter(SessionAdapter):
    key = "goose"
    label = "Goose"
    can_resume = True
    takes_initial_prompt_arg = False

    def _sessions(self) -> List[Path]:
        base = _goose_root() / "sessions"
        if not base.is_dir():
            return []
        return [p for p in base.glob("*.jsonl") if p.stat().st_size > 0]

    def locate_latest(self, cwd, *, any_cwd=False):
        best = _newest(self._sessions())
        if best is None:
            return None
        return AgentSession(
            agent_key=self.key,
            session_id=best.stem,
            path=best,
            cwd=cwd,
            title="",
            mtime=best.stat().st_mtime,
        )

    def resume_command(self, base_command, session, *, fork):
        base = base_command or "goose"
        return f"{base} session --resume --name {session.session_id}"

    def transcript_markdown(self, session, *, include_thinking=False,
                            max_chars=DEFAULT_MAX_CHARS):
        if session.path is None or not session.path.exists():
            return None
        doc = _Doc()
        try:
            for obj in _iter_jsonl(session.path):
                role = obj.get("role") or (obj.get("message") or {}).get("role")
                text = obj.get("content")
                if isinstance(text, list):
                    text = "\n".join(
                        str(b.get("text") or "") for b in text if isinstance(b, dict)
                    )
                text = str(text or "").strip()
                if role and text:
                    heading = "User" if role == "user" else "Assistant"
                    doc.add(f"## {heading}\n\n{text}")
        except (OSError, ValueError):
            return None
        rendered = doc.render(source_label=self.label, session=session, max_chars=max_chars)
        # If nothing parsed, don't hand back a header-only doc.
        return rendered if "## " in rendered else None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _build_registry() -> dict[str, SessionAdapter]:
    reg: dict[str, SessionAdapter] = {}
    for adapter in (
        _ClaudeAdapter(),
        _CodexAdapter(),
        _OpencodeAdapter(),
        _AiderAdapter(),
        _GooseAdapter(),
    ):
        reg[adapter.key] = adapter
    # Best-effort resume-only for the Gemini-family + cursor (no verified
    # transcript layout; locate returns None so callers fall back gracefully).
    for key, label in (("gemini", "Gemini CLI"), ("qwen", "Qwen Code"),
                       ("cursor-agent", "Cursor Agent")):
        a = _GenericAdapter(key, label, initial_prompt_arg=key in ("gemini", "qwen"))
        a.can_resume = True
        reg[key] = a
    return reg


_REGISTRY = _build_registry()

#: Which agents can be launched with the initial prompt as a CLI arg (the rest
#: get the prompt typed in without Enter).
_PROMPT_ARG_AGENTS = {"claude", "codex", "gemini", "qwen"}


def adapter_for(agent_key: str) -> SessionAdapter:
    """The adapter for ``agent_key`` -- never ``None``.

    An unknown or unsupported key gets a generic stub whose locate/resume/
    transcript all return ``None``.
    """
    key = (agent_key or "").strip().lower()
    if key in _REGISTRY:
        return _REGISTRY[key]
    return _GenericAdapter(key, key or "agent", initial_prompt_arg=key in _PROMPT_ARG_AGENTS)


def supports_resume(agent_key: str) -> bool:
    return bool(adapter_for(agent_key).can_resume)


def known_session_agents() -> List[str]:
    """Keys with a real (non-stub) adapter."""
    return [k for k, a in _REGISTRY.items() if not isinstance(a, _GenericAdapter)]


def locate_latest(
    agent_key: str, cwd: Optional[str], *, any_cwd: bool = False
) -> Optional[AgentSession]:
    try:
        return adapter_for(agent_key).locate_latest(cwd, any_cwd=any_cwd)
    except Exception:  # noqa: BLE001 - never let a scan crash the handoff
        return None


def resume_command(
    agent_key: str,
    base_command: str,
    session: AgentSession,
    *,
    fork: bool = True,
) -> Optional[str]:
    a = adapter_for(agent_key)
    if not a.can_resume or not session or not session.session_id:
        return None
    try:
        return a.resume_command(base_command, session, fork=fork)
    except Exception:  # noqa: BLE001
        return None


def transcript_markdown(
    agent_key: str,
    session: AgentSession,
    *,
    include_thinking: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Optional[str]:
    if not session:
        return None
    try:
        return adapter_for(agent_key).transcript_markdown(
            session, include_thinking=include_thinking, max_chars=max_chars
        )
    except Exception:  # noqa: BLE001
        return None


def initial_prompt_command(
    agent_key: str, base_command: str, prompt: str
) -> Optional[str]:
    try:
        return adapter_for(agent_key).initial_prompt_command(base_command, prompt)
    except Exception:  # noqa: BLE001
        return None
