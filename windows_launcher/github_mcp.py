"""Wire a connected GitHub account into the coding agent running in a pane.

Agents in AgentDeck are external CLIs we launch in a ConPTY pane -- we can't add
tools to them at runtime. What we *can* do (``agents.pretrust_folder`` already
does it for the trust prompt) is write the agent's config. This module adds a
**GitHub MCP server** to it, authenticated with the user's connected token and
scoped to the capabilities they opted into, so the agent gets
``create_pull_request_review`` / ``run_workflow`` / … as native tools.

Qt-free. v1 supports **Claude Code**; :func:`supports_agent` gates everything, so
an unsupported agent is simply left untouched.

**Where the server is written:** the **root** ``mcpServers`` block inside
``~/.claude.json`` -- i.e. Claude Code's *user* scope -- *not* a
``<folder>/.mcp.json`` file and *not* a per-project entry. So a Claude Code pane
has the GitHub tools whatever folder it's `cd`'d to, which is how the user thinks
about it ("I connected GitHub"). A user-scope server is trusted automatically
(no ``/mcp`` approval prompt), the bearer token never lands in a project tree,
and there's no clash with ``pretrust_folder`` refusing folders with a
``.mcp.json``.

Transport:
* ``remote`` (default) -- GitHub's hosted MCP server, token as a bearer header,
  no local binary;
* ``local`` -- the ``github-mcp-server`` binary on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from plugin_store import PluginConnection, toolsets_for

__all__ = [
    "AGENTDECK_DIR",
    "REMOTE_MCP_URL",
    "supports_agent",
    "mcp_server_config",
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
_MANAGED = "x-agentdeck-managed"
_SERVER_NAME = "github"


def _is_claude(agent_command: str) -> bool:
    try:
        from agents import is_claude_command

        return is_claude_command(agent_command)
    except Exception:  # noqa: BLE001
        first = (agent_command or "").strip().split()[:1]
        return bool(first) and Path(first[0].strip('"\'')).stem.lower() == "claude"


def supports_agent(agent_command: Optional[str]) -> bool:
    """True when AgentDeck knows how to inject MCP config for this agent (v1: Claude Code)."""
    return _is_claude(agent_command or "")


# ---------------------------------------------------------------------------
# Server config
# ---------------------------------------------------------------------------

def mcp_server_config(
    token: str,
    *,
    toolsets: Optional[List[str]] = None,
    transport: str = "remote",
    read_only: bool = False,
) -> dict:
    """The ``mcpServers["github"]`` block written into Claude Code's config."""
    toolsets = toolsets or ["context", "repos", "pull_requests"]
    ts = ",".join(toolsets)
    if transport == "local":
        cfg: Dict[str, object] = {
            "command": shutil.which("github-mcp-server") or "github-mcp-server",
            "args": ["stdio"],
            "env": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": token,
                "GITHUB_TOOLSETS": ts,
                **({"GITHUB_READ_ONLY": "1"} if read_only else {}),
            },
        }
    else:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-MCP-Toolsets": ts,
        }
        if read_only:
            headers["X-MCP-Readonly"] = "true"
        cfg = {"type": "http", "url": REMOTE_MCP_URL, "headers": headers}
    cfg[_MANAGED] = True
    return cfg


def _claude_config_path() -> Path:
    return Path.home() / ".claude.json"


def _load_json(path: Path) -> tuple[dict, bool]:
    """Return ``(config, existed)``. A malformed file reads as empty-but-existed
    so we never blow away something we can't parse."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, False
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}, True
    return (data if isinstance(data, dict) else {}), True


def _atomic_write_json(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.adk{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def cleanup_legacy_mcp_json(folder: str | Path) -> bool:
    """Delete a ``<folder>/.mcp.json`` an older AgentDeck build wrote.

    Pre-2026-09 the GitHub server (with its bearer token) went into a
    ``.mcp.json`` file in the working folder. That folder is often a git repo, so
    the token could be committed. Remove that file if -- and only if -- it holds
    just our managed ``github`` server. Returns True if a file was deleted.
    """
    p = Path(folder) / ".mcp.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    gh = servers.get(_SERVER_NAME)
    if not (isinstance(gh, dict) and gh.get(_MANAGED)):
        return False
    del servers[_SERVER_NAME]
    try:
        if not servers and list(data.keys()) == ["mcpServers"]:
            p.unlink()
        else:
            _atomic_write_json(p, data)
        return True
    except OSError:
        return False


def _strip_managed(config: dict) -> bool:
    """Remove our managed ``github`` server from the root and every project.
    Mutates ``config``; returns True if anything was removed."""
    changed = False
    scopes = [config]
    projects = config.get("projects")
    if isinstance(projects, dict):
        scopes += [e for e in projects.values() if isinstance(e, dict)]
    for scope in scopes:
        servers = scope.get("mcpServers")
        if not isinstance(servers, dict):
            continue
        gh = servers.get(_SERVER_NAME)
        if isinstance(gh, dict) and gh.get(_MANAGED):
            del servers[_SERVER_NAME]
            changed = True
    return changed


def inject(
    folder: str | Path | None,
    token: str,
    connection: PluginConnection,
    *,
    agent_command: Optional[str] = None,
    claude_config: Optional[str | Path] = None,
) -> bool:
    """Add the GitHub MCP server to Claude Code's **user-scope** config.

    Writes the root ``mcpServers.github`` block in ``~/.claude.json`` -- so a
    Claude Code pane has the GitHub tools whatever folder it runs in, which is
    how the user thinks about it ("I connected GitHub"). A user-scope server is
    trusted automatically (no ``/mcp`` approval prompt), and the bearer token
    never lands in a project tree.

    ``folder`` is used only for a sanity check and to clean up a ``.mcp.json`` an
    older build wrote; pass ``None`` if there is no folder.

    * No-op (returns False) when the agent isn't Claude Code or the token is empty.
    * Idempotent.
    * Refuses to touch a root ``github`` server the user configured themselves.
    """
    if agent_command is not None and not supports_agent(agent_command):
        return False
    if not token:
        return False
    if folder:
        try:
            cleanup_legacy_mcp_json(folder)
        except OSError:
            pass

    read_only = connection.capabilities == ["read"]
    desired = mcp_server_config(
        token,
        toolsets=toolsets_for(connection.capabilities),
        transport=connection.transport,
        read_only=read_only,
    )

    path = Path(claude_config) if claude_config else _claude_config_path()
    config, _existed = _load_json(path)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        config["mcpServers"] = servers

    existing = servers.get(_SERVER_NAME)
    if isinstance(existing, dict) and not existing.get(_MANAGED):
        return False  # a github server the user set up themselves
    if existing == desired:
        return False

    servers[_SERVER_NAME] = desired
    return _atomic_write_json(path, config)


def remove(folder: str | Path | None = None, *, claude_config: Optional[str | Path] = None) -> bool:
    """Drop the AgentDeck-managed GitHub server from ``~/.claude.json`` -- the
    root entry plus any per-project entries an older build wrote. Leaves
    everything else (trust flags, other servers) alone. ``folder`` is ignored;
    kept for call-site compatibility."""
    path = Path(claude_config) if claude_config else _claude_config_path()
    config, existed = _load_json(path)
    if not existed:
        return False
    return _atomic_write_json(path, config) if _strip_managed(config) else False


#: Same behaviour as :func:`remove` now that the server is user-scoped; kept so
#: existing call sites (``github_controller.unwire_all``) stay readable.
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
    if _is_claude(base):
        return f'{base} "{task}"'
    return base
