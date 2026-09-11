"""Remember the open workspaces so a restart lands you back where you were.

Workspaces are otherwise session-only -- close AgentDeck with four of them set
up and the next launch drops you at the wizard with a blank one. This module
writes a light snapshot of the list to
``%APPDATA%\\multi-terminal\\workspaces.json`` (beside ``config.json`` /
``notes.json`` / ``worktrees.json``) and reads it back at startup.

    {
      "version": 1,
      "folder": "E:\\\\code\\\\myapp",     # the shared working folder
      "layout": "grid",                    # grid | columns | rows
      "active": 1,                         # index of the workspace on screen
      "workspaces": [
        {"name": "API work", "panes": 4,
         "agent_key": "claude", "agent_command": "claude"}
      ]
    }

Only the *shape* is stored -- names, pane counts, the chosen agent, the layout.
Shell scrollback and running processes are not restored (nor could they be);
each pane just re-launches its agent in the folder, exactly as a fresh
workspace would.

Qt-free (same rule as :mod:`worktree_store` / :mod:`routines_store`): a pure
data layer that unit-tests offline. **Machine-local, not cloud-synced** -- the
folder path only means something on this disk.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "WorkspaceSnapshot",
    "SessionSnapshot",
    "WorkspacesStore",
    "default_workspaces_path",
]

STORE_VERSION = 1

_VALID_LAYOUTS = ("grid", "columns", "rows")


def default_workspaces_path() -> Path:
    """``workspaces.json`` beside the app's ``config.json``.

    ``ADK_WORKSPACES_FILE`` overrides it (tests).
    """
    override = os.environ.get("ADK_WORKSPACES_FILE")
    if override:
        return Path(override)
    try:
        from config import CONFIG_DIR

        return Path(CONFIG_DIR) / "workspaces.json"
    except Exception:  # noqa: BLE001
        base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") \
            or str(Path.home() / ".config")
        return Path(base) / "multi-terminal" / "workspaces.json"


@dataclass
class WorkspaceSnapshot:
    name: str = ""
    panes: int = 4
    agent_key: str = ""
    agent_command: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "WorkspaceSnapshot":
        try:
            panes = int(data.get("panes", 4))
        except (TypeError, ValueError):
            panes = 4
        return cls(
            name=str(data.get("name") or "").strip(),
            panes=max(1, min(16, panes)),
            agent_key=str(data.get("agent_key") or ""),
            agent_command=str(data.get("agent_command") or ""),
        )


@dataclass
class SessionSnapshot:
    folder: str = ""
    layout: str = "grid"
    active: int = 0
    workspaces: list = field(default_factory=list)
    saved_at: float = 0.0

    def is_usable(self) -> bool:
        return bool(self.workspaces)

    @classmethod
    def from_dict(cls, data: dict) -> "SessionSnapshot":
        raw = data.get("workspaces")
        items = [
            WorkspaceSnapshot.from_dict(it)
            for it in (raw if isinstance(raw, list) else [])
            if isinstance(it, dict)
        ]
        layout = str(data.get("layout") or "grid")
        if layout not in _VALID_LAYOUTS:
            layout = "grid"
        try:
            active = int(data.get("active", 0))
        except (TypeError, ValueError):
            active = 0
        active = max(0, min(active, max(0, len(items) - 1)))
        try:
            saved_at = float(data.get("saved_at", 0.0))
        except (TypeError, ValueError):
            saved_at = 0.0
        return cls(
            folder=str(data.get("folder") or ""),
            layout=layout,
            active=active,
            workspaces=items,
            saved_at=saved_at,
        )


class WorkspacesStore:
    """Read / write the one session-snapshot file.

    Construct with no argument for the real file; pass ``path=`` in tests.
    """

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self._path = Path(path) if path is not None else default_workspaces_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> SessionSnapshot:
        """The saved session, or an empty one -- never raises."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return SessionSnapshot()
        try:
            data = json.loads(raw)
        except ValueError:
            return SessionSnapshot()
        if not isinstance(data, dict):
            return SessionSnapshot()
        return SessionSnapshot.from_dict(data)

    def save(self, session: SessionSnapshot) -> None:
        """Write the snapshot back atomically. A read-only disk is not fatal."""
        payload = {
            "version": STORE_VERSION,
            "folder": session.folder,
            "layout": session.layout if session.layout in _VALID_LAYOUTS else "grid",
            "active": max(0, int(session.active)),
            "saved_at": time.time(),
            "workspaces": [
                asdict(w) if isinstance(w, WorkspaceSnapshot) else dict(w)
                for w in session.workspaces
            ],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass
