"""Read / modify / write an agent's on-disk config file, safely.

Every coding agent AgentDeck knows stores its MCP servers in a config file, but
in different formats and places -- Claude Code's ``~/.claude.json`` (JSON), Codex's
``~/.codex/config.toml`` (TOML), Goose's ``config.yaml`` (YAML). This module is the
one place that knows how to load such a file into a plain mapping, hand it back for
mutation, and write it out atomically without clobbering what the user put there.

Qt-free. No knowledge of *which* servers or agents exist -- that's ``mcp_targets``.

* **JSON** -- stdlib ``json``. Always available.
* **TOML** -- read via stdlib ``tomllib`` (3.11+); write via ``tomlkit`` (round-trip,
  keeps the user's comments). If ``tomlkit`` is missing, :func:`dump` for TOML is a
  no-op that returns ``False`` -- the caller degrades gracefully, exactly like the
  optional voice deps.
* **YAML** -- ``ruamel.yaml`` (round-trip). Missing -> :func:`dump` for YAML no-ops.

The atomic-write convention matches the rest of the codebase: write
``<name>.adk<pid>.tmp`` in the target dir, then ``os.replace``. A malformed source
file is treated as "exists but empty" so a modify-write never destroys bytes we
couldn't parse.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Tuple

__all__ = ["Fmt", "load", "dump", "locked", "get_in", "as_item", "toml_ok", "yaml_ok"]

Fmt = str  # "json" | "toml" | "yaml"

# --- stdlib TOML read -------------------------------------------------------
try:  # 3.11+
    import tomllib as _tomllib  # type: ignore
except Exception:  # noqa: BLE001 - pragma: no cover
    _tomllib = None  # type: ignore

# --- round-trip TOML write -------------------------------------------------
try:
    import tomlkit as _tomlkit  # type: ignore
except Exception:  # noqa: BLE001
    _tomlkit = None  # type: ignore

# --- round-trip YAML -----------------------------------------------------
try:
    from ruamel.yaml import YAML as _RuamelYAML  # type: ignore

    _yaml = _RuamelYAML()
    _yaml.preserve_quotes = True
    _yaml.indent(mapping=2, sequence=4, offset=2)
except Exception:  # noqa: BLE001
    _yaml = None  # type: ignore


def toml_ok() -> bool:
    """Whether this build can *write* TOML (read needs only stdlib ``tomllib``)."""
    return _tomlkit is not None


def yaml_ok() -> bool:
    """Whether this build can read/write YAML."""
    return _yaml is not None


# ---------------------------------------------------------------------------
# Per-path lock -- serialises the whole read-modify-write for one file
# ---------------------------------------------------------------------------

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve() if _resolvable(path) else path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


def _resolvable(path: Path) -> bool:
    try:
        path.resolve()
        return True
    except OSError:
        return False


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold a process-wide re-entrant lock for ``path`` for the block's duration.

    Wrap a full load -> mutate -> :func:`dump` in this so two writers targeting the
    same file (e.g. the GitHub and Vercel controllers both touching
    ``~/.claude.json``) can't interleave and lose an entry. Re-entrant, so nested
    ``locked(same_path)`` is fine.
    """
    lock = _lock_for(Path(path))
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Load / dump
# ---------------------------------------------------------------------------

def _empty(fmt: Fmt) -> Any:
    if fmt == "toml" and _tomlkit is not None:
        return _tomlkit.document()
    if fmt == "yaml" and _yaml is not None:
        from ruamel.yaml.comments import CommentedMap  # type: ignore

        return CommentedMap()
    return {}


def load(path: Path, fmt: Fmt) -> Tuple[Any, bool]:
    """``(data, existed)``.

    ``data`` is a mutable mapping in the format's native type (dict for JSON, a
    ``tomlkit`` document for TOML, a ruamel ``CommentedMap`` for YAML). A file that
    doesn't exist -> ``({}, False)``. A file that exists but won't parse ->
    ``(empty, True)`` so the caller can still add a key and write a clean file
    rather than blow away bytes it couldn't read.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _empty(fmt), False

    if not text.strip():
        return _empty(fmt), True

    try:
        if fmt == "json":
            data = json.loads(text)
        elif fmt == "toml":
            if _tomlkit is not None:
                data = _tomlkit.parse(text)
            elif _tomllib is not None:
                data = _tomllib.loads(text)
            else:
                return _empty(fmt), True
        elif fmt == "yaml":
            if _yaml is None:
                return _empty(fmt), True
            data = _yaml.load(text)
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown fmt {fmt!r}")
    except Exception:  # noqa: BLE001 - any parse error
        return _empty(fmt), True

    if data is None or not hasattr(data, "get"):
        return _empty(fmt), True
    return data, True


def dump(path: Path, data: Any, fmt: Fmt) -> bool:
    """Atomically write ``data`` to ``path`` in ``fmt``. Never raises.

    Returns ``False`` (a no-op) when the writer for ``fmt`` isn't available in this
    build -- ``tomlkit`` for TOML, ``ruamel.yaml`` for YAML.
    """
    path = Path(path)
    try:
        if fmt == "json":
            text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        elif fmt == "toml":
            if _tomlkit is None:
                return False
            text = _tomlkit.dumps(data)
        elif fmt == "yaml":
            if _yaml is None:
                return False
            import io

            buf = io.StringIO()
            _yaml.dump(data, buf)
            text = buf.getvalue()
        else:  # pragma: no cover
            return False
    except Exception:  # noqa: BLE001
        return False

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.adk{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Nested-map access
# ---------------------------------------------------------------------------

def get_in(data: Any, keypath: Tuple[str, ...], create: bool) -> Optional[dict]:
    """Walk ``data`` down ``keypath`` and return the mapping there.

    With ``create=True`` any missing intermediate mapping is created (using the
    same mapping type as ``data`` so TOML/YAML round-tripping is preserved) and the
    final mapping is returned. With ``create=False`` a missing or non-mapping node
    returns ``None``.

    A single ``keypath`` element that itself contains a dot -- e.g. Amp's literal
    ``"amp.mcpServers"`` key inside a flat ``settings.json`` -- is treated as one
    key, not a path, so pass it as ``("amp.mcpServers",)``.
    """
    node: Any = data
    for i, key in enumerate(keypath):
        child = node.get(key) if hasattr(node, "get") else None
        if not hasattr(child, "get"):
            if not create:
                return None
            child = _new_map_like(node)
            node[key] = child
        node = child
    return node if hasattr(node, "get") else None


def _new_map_like(node: Any) -> Any:
    mod = type(node).__module__ or ""
    if mod.startswith("tomlkit") and _tomlkit is not None:
        return _tomlkit.table()
    if mod.startswith("ruamel"):
        try:
            from ruamel.yaml.comments import CommentedMap  # type: ignore

            return CommentedMap()
        except Exception:  # noqa: BLE001
            return {}
    return {}


def as_item(value: Any, fmt: Fmt) -> Any:
    """Wrap a plain dict/list so it serialises correctly when stored into a
    ``fmt`` container -- notably a TOML sub-table (a bare ``dict`` assigned into a
    ``tomlkit`` table renders as an empty section). No-op for JSON / YAML.
    """
    if fmt == "toml" and _tomlkit is not None:
        try:
            return _tomlkit.item(value)
        except Exception:  # noqa: BLE001
            return value
    return value
