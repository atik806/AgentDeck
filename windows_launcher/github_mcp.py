"""Wire a connected GitHub account into the coding agent(s) running in a pane.

Agents in AgentDeck are external CLIs we launch in a ConPTY pane -- we can't add
tools to them at runtime. What we *can* do is write the agent's config file. This
module adds a **GitHub MCP server** to it, authenticated with the user's connected
token and scoped to the capabilities they opted into, so the agent gets
``create_pull_request_review`` / ``run_workflow`` / … as native tools.

Qt-free. The per-agent details (which file, which format, ``url`` vs ``httpUrl``,
whether a bearer goes in a header or a ``bearer_token`` field) live in
``mcp_targets``; this module builds one transport-agnostic :func:`canonical_server`
spec and writes it to every target agent that can take it.

**Where the server is written:** each agent's *user* scope (Claude Code's root
``mcpServers`` in ``~/.claude.json``, Codex's ``~/.codex/config.toml``, …) -- *not*
a ``<folder>/.mcp.json`` and *not* a per-project entry. So a pane has the GitHub
tools whatever folder it's `cd`'d to, which is how the user thinks about it ("I
connected GitHub"). A user-scope server is trusted automatically, and the bearer
token never lands in a project tree.

Transport:
* ``remote`` (default) -- GitHub's hosted MCP server, token as a bearer header
  (or a ``bearer_token`` field for Codex), no local binary;
* ``local`` -- the ``github-mcp-server`` binary on PATH (Claude Code only).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

import mcp_io
import mcp_targets
from mcp_targets import McpLedger
from plugin_store import GITHUB as _PROVIDER
from plugin_store import PluginConnection, toolsets_for

__all__ = [
    "AGENTDECK_DIR",
    "REMOTE_MCP_URL",
    "supports_agent",
    "canonical_server",
    "mcp_server_config",
    "review_supported",
    "inject",
    "remove",
    "remove_all",
    "cleanup_legacy_mcp_json",
    "build_review_brief",
    "write_review_brief",
    "review_startup_command",
]

AGENTDECK_DIR = ".agentdeck"
REMOTE_MCP_URL = "https://api.githubcopilot.com/mcp/"

#: Marks the server entry as ours, so :func:`remove` only ever deletes a block
#: AgentDeck wrote -- never a ``github`` server the user configured by hand.
#: (JSON agents; TOML/YAML adapters use ``x_agentdeck_managed``.)
_MANAGED = "x-agentdeck-managed"
_SERVER_NAME = "github"

#: Agents whose one-shot "review this PR" task invocation is verified to work.
_REVIEW_AGENTS = {"claude", "codex"}


def _is_claude(agent_command: str) -> bool:
    try:
        from agents import is_claude_command

        return is_claude_command(agent_command)
    except Exception:  # noqa: BLE001
        first = (agent_command or "").strip().split()[:1]
        return bool(first) and Path(first[0].strip('"\'')).stem.lower() == "claude"


def _key_of(agent: Optional[str]) -> str:
    """A ``_KNOWN`` key from either a bare key or a full command string."""
    if not agent:
        return ""
    if mcp_targets.target(agent) is not None:
        return agent.strip().lower()
    try:
        from agents import agent_key_for_command

        return agent_key_for_command(agent)
    except Exception:  # noqa: BLE001
        return ""


def supports_agent(agent: Optional[str]) -> bool:
    """True when this agent can take a header-authenticated remote MCP server
    (i.e. the GitHub plugin can wire it). Accepts a key or a command string."""
    return bool(mcp_targets.caps(_key_of(agent)).get("mcp_remote_headers"))


def review_supported(agent_key: str) -> bool:
    """Whether the PR-review one-shot task flow is known to work for this agent."""
    return _key_of(agent_key) in _REVIEW_AGENTS


# ---------------------------------------------------------------------------
# Server config
# ---------------------------------------------------------------------------

def canonical_server(
    token: str,
    *,
    toolsets: Optional[List[str]] = None,
    transport: str = "remote",
    read_only: bool = False,
) -> dict:
    """The transport-agnostic spec ``mcp_targets.render_entry`` turns into each
    agent's own entry shape."""
    toolsets = toolsets or ["context", "repos", "pull_requests"]
    ts = ",".join(toolsets)
    if transport == "local":
        return {
            "transport": "stdio",
            "command": shutil.which("github-mcp-server") or "github-mcp-server",
            "args": ["stdio"],
            "env": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": token,
                "GITHUB_TOOLSETS": ts,
                **({"GITHUB_READ_ONLY": "1"} if read_only else {}),
            },
            "oauth": False,
        }
    headers = {"Authorization": f"Bearer {token}", "X-MCP-Toolsets": ts}
    if read_only:
        headers["X-MCP-Readonly"] = "true"
    return {
        "transport": "http",
        "url": REMOTE_MCP_URL,
        "headers": headers,
        "bearer": token,
        "oauth": False,
    }


def mcp_server_config(
    token: str,
    *,
    toolsets: Optional[List[str]] = None,
    transport: str = "remote",
    read_only: bool = False,
) -> dict:
    """The ``mcpServers["github"]`` block for **Claude Code** specifically.

    Kept for call sites / tests that want the concrete Claude entry; new code
    should use :func:`canonical_server` + ``mcp_targets``.
    """
    canonical = canonical_server(
        token, toolsets=toolsets, transport=transport, read_only=read_only
    )
    return mcp_targets.render_entry(mcp_targets.target("claude"), _SERVER_NAME, canonical) or {}


def _resolve_keys(agent_keys: Optional[List[str]], agent_command: Optional[str]) -> List[str]:
    if agent_keys:
        return [k for k in (_key_of(k) for k in agent_keys) if k]
    k = _key_of(agent_command)
    return [k] if k else ["claude"]


def _target_keys_for(keys: List[str], canonical: dict) -> List[str]:
    need_oauth = bool(canonical.get("oauth"))
    http = canonical.get("transport") == "http"
    out: List[str] = []
    for k in keys:
        c = mcp_targets.caps(k)
        if not c.get("mcp"):
            continue
        if need_oauth and not c.get("mcp_oauth"):
            continue
        if not need_oauth and http and not c.get("mcp_remote_headers"):
            continue
        if k not in out:
            out.append(k)
    return out


def _path_override(agent_key: str, config_paths, claude_config):
    if config_paths and agent_key in config_paths:
        return Path(config_paths[agent_key])
    if agent_key == "claude" and claude_config:
        return Path(claude_config)
    return None


def cleanup_legacy_mcp_json(folder: str | Path) -> bool:
    """Delete a ``<folder>/.mcp.json`` an older AgentDeck build wrote.

    Pre-2026-09 the GitHub server (with its bearer token) went into a
    ``.mcp.json`` file in the working folder. That folder is often a git repo, so
    the token could be committed. Remove that file if -- and only if -- it holds
    just our managed ``github`` server. Returns True if a file was deleted.
    """
    p = Path(folder) / ".mcp.json"
    data, existed = mcp_io.load(p, "json")
    if not existed:
        return False
    servers = data.get("mcpServers") if hasattr(data, "get") else None
    if not isinstance(servers, dict):
        return False
    gh = servers.get(_SERVER_NAME)
    if not (isinstance(gh, dict) and gh.get(_MANAGED)):
        return False
    del servers[_SERVER_NAME]
    try:
        if not servers and list(data.keys()) == ["mcpServers"]:
            p.unlink()
            return True
    except OSError:
        return False
    return mcp_io.dump(p, data, "json")


def inject(
    folder: str | Path | None,
    token: str,
    connection: PluginConnection,
    *,
    agent_keys: Optional[List[str]] = None,
    agent_command: Optional[str] = None,
    claude_config: Optional[str | Path] = None,
    config_paths: Optional[dict] = None,
) -> bool:
    """Add the GitHub MCP server to each target agent's **user-scope** config.

    ``agent_keys`` -- the agents to wire (``["claude", "codex", …]``). If omitted,
    falls back to ``agent_command`` (a resolved command string) and finally to
    ``["claude"]``. Non-MCP agents (aider) and, for a header-auth server, agents
    with no header path are silently skipped.

    ``folder`` is used only to clean up a ``.mcp.json`` an older build wrote.
    ``claude_config`` / ``config_paths`` (``{key: Path}``) redirect a target's file
    for tests.

    * No-op (returns False) when the token is empty or no target could be written.
    * Idempotent per agent.
    * Refuses to touch a ``github`` server the user configured themselves.
    """
    if not token:
        return False
    if folder:
        try:
            cleanup_legacy_mcp_json(folder)
        except OSError:
            pass

    canonical = canonical_server(
        token,
        toolsets=toolsets_for(connection.capabilities),
        transport=connection.transport,
        read_only=connection.capabilities == ["read"],
    )

    ledger = McpLedger()
    changed = False
    for key in _target_keys_for(_resolve_keys(agent_keys, agent_command), canonical):
        tgt = mcp_targets.target(key)
        if tgt is None:
            continue
        did, wrote_root_extra = mcp_targets.write_server(
            tgt, _SERVER_NAME, canonical,
            path_override=_path_override(key, config_paths, claude_config),
            ledger_managed=ledger.has(_PROVIDER, key),
        )
        if did:
            ledger.record(_PROVIDER, key, _SERVER_NAME, wrote_root_extra=wrote_root_extra)
            changed = True
    return changed


def remove(
    folder: str | Path | None = None,
    *,
    agent_keys: Optional[List[str]] = None,
    claude_config: Optional[str | Path] = None,
    config_paths: Optional[dict] = None,
) -> bool:
    """Drop the AgentDeck-managed GitHub server from every agent config we wrote it
    into (per the ledger), plus any legacy per-project entries in ``~/.claude.json``.
    Leaves everything else alone. ``folder`` is ignored; kept for call-site
    compatibility."""
    ledger = McpLedger()
    ledger.backfill_claude(
        _PROVIDER, _SERVER_NAME, _MANAGED,
        claude_path=_path_override("claude", config_paths, claude_config),
    )
    keys = agent_keys or list(ledger.agents_for(_PROVIDER).keys()) or ["claude"]

    changed = False
    for key in keys:
        tgt = mcp_targets.target(key)
        if tgt is None:
            continue
        did = mcp_targets.remove_server(
            tgt, _SERVER_NAME,
            path_override=_path_override(key, config_paths, claude_config),
            ledger_managed=ledger.has(_PROVIDER, key),
            drop_root_extra=ledger.wrote_root_extra(_PROVIDER, key),
        )
        ledger.forget(_PROVIDER, key)
        changed = changed or did
    return changed


#: Kept so existing call sites (``github_controller.unwire_all``) stay readable.
remove_all = remove


# ---------------------------------------------------------------------------
# GitHub review -- the first capability
# ---------------------------------------------------------------------------

_EVENTS = {"comment": "COMMENT", "request_changes": "REQUEST_CHANGES", "approve": "APPROVE"}

_FOCUS_LINES = {
    "bugs": "Correctness bugs, edge cases, error handling, race conditions.",
    "security": "Injection, authz/authn gaps, secret handling, unsafe deserialisation.",
    "style": "Naming, dead code, consistency with the surrounding file.",
    "tests": "Missing or weak test coverage for the changed behaviour.",
    "perf": "Needless work in hot paths, N+1 queries, unbounded allocations.",
}


def build_review_brief(repo: str, pr_number: int, options: Optional[dict] = None) -> str:
    """The Markdown brief written to ``.agentdeck/review-<pr>.md`` and handed to
    the agent as its task."""
    options = options or {}
    focus = [f for f in (options.get("focus") or ["bugs", "security", "tests"]) if f in _FOCUS_LINES]
    if not focus:
        focus = ["bugs", "security", "tests"]
    post = bool(options.get("post"))
    event = _EVENTS.get(str(options.get("event") or "comment").lower(), "COMMENT")

    focus_block = "\n".join(f"- **{f}** — {_FOCUS_LINES[f]}" for f in focus)

    if post:
        post_block = (
            f"When done, post the review to GitHub with the `github` tools as a "
            f"single review, event `{event}`, with inline comments anchored to the "
            f"exact changed lines plus a short summary.\n"
            f"- Do NOT approve or request changes unless the event above says so.\n"
            f"- If any finding would involve a destructive or irreversible action, "
            f"stop and show it here first."
        )
    else:
        post_block = (
            "Do NOT post anything to GitHub. Print the review here: a summary, then "
            "each finding as `path:line — issue` with a suggested fix."
        )

    return f"""# GitHub review — {repo}#{pr_number}

You are reviewing pull request **#{pr_number}** in **{repo}**.

## Steps
1. Use the `github` MCP tools to fetch the PR: its metadata, the full diff, the
   list of changed files, and the current check/CI status.
2. Read enough surrounding code (also via the `github` tools, or the local
   checkout if this folder is that repo) to judge each change in context.
3. Review for:
{focus_block}
4. {post_block}

## Rules
- Be specific: cite `file:line`. No vague "consider refactoring".
- Flag only real problems. An empty review with "looks good" is a valid result.
- Never push commits, close the PR, or merge. Review only.
"""


def write_review_brief(
    folder: str | Path, repo: str, pr_number: int, options: Optional[dict] = None
) -> Path:
    """Write the brief under ``<folder>/.agentdeck/`` and return its path."""
    folder = Path(folder)
    out_dir = folder / AGENTDECK_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"review-{pr_number}.md"
    path.write_text(build_review_brief(repo, pr_number, options), encoding="utf-8")
    _git_exclude(folder, f"{AGENTDECK_DIR}/")
    return path


def _git_exclude(folder: Path, pattern: str) -> None:
    """Add ``pattern`` to ``.git/info/exclude`` if ``folder`` is a git work tree.

    Local-only (never committed), so it keeps AgentDeck's scratch files out of
    ``git status`` without touching a tracked ``.gitignore``. Best-effort.
    """
    exclude = folder / ".git" / "info" / "exclude"
    try:
        if not exclude.parent.is_dir():
            return
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if pattern in existing.split():
            return
        with exclude.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"{pattern}\n")
    except OSError:
        pass


def review_startup_command(
    agent_command: str, repo: str, pr_number: int, brief_path: str | Path
) -> str:
    """The command typed into the review pane once the shell is up.

    Kept shell-agnostic (no ``$(cat …)``): the agent is pointed at the brief
    file, which it reads itself."""
    brief = Path(brief_path)
    rel = brief.name
    try:
        rel = str(brief.relative_to(Path(brief).parents[1]))
    except (ValueError, IndexError):
        rel = f"{AGENTDECK_DIR}/{brief.name}"
    rel = rel.replace("\\", "/")
    task = (
        f"Review pull request #{pr_number} in {repo}. "
        f"Follow the brief in {rel} exactly."
    )
    base = (agent_command or "claude").strip() or "claude"
    key = _key_of(base)
    if key in _REVIEW_AGENTS:  # claude / codex take an initial prompt as an arg
        return f'{base} "{task}"'
    return base
