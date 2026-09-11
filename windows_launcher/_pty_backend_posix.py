"""Real pseudo-terminal sessions -- one per pane. POSIX (Linux/macOS) implementation.

Same reasoning as the Windows sibling (``_pty_backend_win.py``): a shell handed
a plain pipe doesn't believe it's talking to a terminal and degrades itself
accordingly, so every pane gets a real pty. On POSIX that's the kernel's own
pty subsystem, reached here through ``ptyprocess`` -- a small, long-used
library (the engine under ``pexpect``) that already solves non-blocking reads,
zombie reaping, and resize ioctls correctly, so this module is glue rather
than a from-scratch fork/exec implementation.

Two real differences from the Windows backend, both handled here:

* ``ptyprocess.read()`` returns raw ``bytes``; ``pywinpty.read()`` returns
  pre-decoded, UTF-8-boundary-safe ``str``. An incremental decoder (one
  persistent instance per session) reproduces that boundary-safety here --
  decoding each chunk independently would corrupt a codepoint split across two
  reads.
* There is no Win32 Toolhelp32 snapshot API. ``has_child_process`` instead
  walks ``/proc/*/stat`` for a process reporting our pid as its parent, which
  is the same "can only ever read high, never low" semantics the Windows walk
  relies on (a recycled PID cannot make this return a false positive for the
  *wrong* reason, only linger a little late).

Selected by ``pty_backend.py`` on any non-``win32`` platform -- see that module
for the dispatch and the full public contract shared with ``_pty_backend_win.py``.
"""

from __future__ import annotations

import codecs
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

try:
    from ptyprocess import PtyProcess
except ImportError as exc:  # pragma: no cover - dependency guard
    raise ImportError(
        "ptyprocess is required for real terminal support.\n"
        "Install the dependencies with:  pip install -r requirements.txt"
    ) from exc


__all__ = ["PtySession", "available_shells", "resolve_shell", "DEFAULT_SHELL"]


DEFAULT_SHELL = "auto"

# How many bytes to pull per read. Matches the Windows backend's _READ_SIZE.
_READ_SIZE = 65536

# key -> (label, login+interactive argv flags). "sh" is deliberately just "-i":
# not every /bin/sh (dash in particular) accepts a "-l" flag the way bash/zsh/
# fish document, so it's left off rather than risk an "illegal option" spawn
# failure on the one shell that's guaranteed to exist.
_SHELL_FLAGS: dict[str, tuple[str, list[str]]] = {
    "bash": ("Bash", ["-l", "-i"]),
    "zsh": ("Zsh", ["-l", "-i"]),
    "fish": ("Fish", ["-l", "-i"]),
    "sh": ("POSIX Shell", ["-i"]),
}
# Probe order when nothing else picks a winner.
_PROBE_ORDER = ["bash", "zsh", "fish", "sh"]


# ---------------------------------------------------------------------------
# Shell discovery
# ---------------------------------------------------------------------------

def available_shells() -> list[tuple[str, str, list[str]]]:
    """Installed shells as ``(key, label, argv)``, best first.

    ``$SHELL`` (the user's login shell, set by the OS/login manager) comes
    first when it points at something real and recognised; the rest of
    :data:`_PROBE_ORDER` fills in behind it via ``PATH``, skipping whatever
    ``$SHELL`` already contributed so nothing is offered twice.
    """
    shells: list[tuple[str, str, list[str]]] = []
    seen_paths: set[str] = set()

    env_shell = os.environ.get("SHELL", "")
    if env_shell and Path(env_shell).is_file():
        key = Path(env_shell).name
        label, flags = _SHELL_FLAGS.get(key, (key.capitalize() or "Shell", ["-i"]))
        shells.append((key, f"{label} (login shell)", [env_shell, *flags]))
        seen_paths.add(env_shell)

    for key in _PROBE_ORDER:
        path = shutil.which(key)
        if not path or path in seen_paths:
            continue
        label, flags = _SHELL_FLAGS[key]
        shells.append((key, label, [path, *flags]))
        seen_paths.add(path)

    return shells


def resolve_shell(key: str = DEFAULT_SHELL) -> tuple[str, list[str]]:
    """Turn a shell key into ``(label, argv)``, falling back to the best found.

    ``"auto"`` picks the first entry from :func:`available_shells` (the login
    shell if recognised, else the first of bash/zsh/fish/sh found on PATH). If
    nothing at all was detected, ``/bin/sh`` is the POSIX-guaranteed last
    resort -- failing loudly at spawn time is more useful than refusing to
    open a pane.
    """
    shells = available_shells()

    if key and key != DEFAULT_SHELL:
        for candidate_key, label, argv in shells:
            if candidate_key == key:
                return label, argv

    if shells:
        _, label, argv = shells[0]
        return label, argv

    return "POSIX Shell", ["/bin/sh", "-i"]


# ---------------------------------------------------------------------------
# Child-process probe
# ---------------------------------------------------------------------------

_PPID_RE = re.compile(rb"^\d+ \([^)]*\) \S (\d+)")


def _ppid_of(pid: int) -> Optional[int]:
    """Parent pid of ``pid``, read from ``/proc/<pid>/stat``, or ``None``."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        return None
    m = _PPID_RE.match(raw)
    return int(m.group(1)) if m else None


def _has_child_process(pid: int) -> bool:
    """True if any process currently reports ``pid`` as its parent.

    A full ``/proc`` scan -- no special capabilities needed, works regardless
    of privileges. A recycled parent pid can only ever make this read high (we
    stay on the alternate screen a little longer), never low, matching the
    Windows Toolhelp walk's semantics.
    """
    if not pid:
        return False
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            if _ppid_of(int(entry)) == pid:
                return True
        return False
    except Exception:  # noqa: BLE001 - a probe failure must not break the pane
        return False


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class PtySession(QObject):
    """One shell running behind a pty.

    Output arrives on the :attr:`output` signal as decoded text, emitted from
    the reader thread. Qt turns that into a queued connection automatically, so
    slots run on the GUI thread and no locking is needed on the receiving side.
    """

    #: Decoded text read from the pty.
    output = Signal(str)

    #: Emitted once, with the exit code, when the shell goes away.
    exited = Signal(int)

    def __init__(
        self,
        shell: str = DEFAULT_SHELL,
        rows: int = 24,
        cols: int = 80,
        cwd: Optional[str] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)

        self.label, self._argv = resolve_shell(shell)
        self.rows = max(1, rows)
        self.cols = max(1, cols)

        self._proc: Optional[PtyProcess] = None
        self._reader: Optional[threading.Thread] = None
        self._closing = False
        self._error: Optional[str] = None

        self._spawn(cwd)

    # -- lifecycle ---------------------------------------------------------

    def _spawn(self, cwd: Optional[str]) -> None:
        # ptyprocess forks then execs *inside the child*: a bad executable
        # fails there, not in this call, so without this check .error would
        # stay None and the caller would instead see a fork-then-immediately-
        # exit pane (plus whatever the child managed to write to the pty
        # before exec failed). Checking here keeps the contract identical to
        # the Windows backend, where a bad command raises synchronously in
        # CreateProcess and is caught below.
        exe = self._argv[0] if self._argv else ""
        if not (shutil.which(exe) or (os.path.isfile(exe) and os.access(exe, os.X_OK))):
            self._error = f"[Errno 2] No such file or directory: '{exe}'"
            self._proc = None
            return

        env = dict(os.environ)
        # Programs that check TERM (vim, less, git, anything ncurses-ish) need
        # to be told the pty speaks 256 colours; the raw pty itself does not
        # set this.
        env["TERM"] = "xterm-256color"
        env.setdefault("COLORTERM", "truecolor")

        try:
            self._proc = PtyProcess.spawn(
                self._argv,
                cwd=cwd or str(Path.home()),
                env=env,
                dimensions=(self.rows, self.cols),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the pane instead
            self._error = str(exc)
            self._proc = None
            return

        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"pty-reader-{self._proc.pid}",
            daemon=True,
        )
        self._reader.start()

    def _read_loop(self) -> None:
        """Pump the pty until it closes. Runs on its own thread."""
        proc = self._proc
        assert proc is not None

        # One persistent decoder: a multi-byte UTF-8 codepoint split across two
        # `_READ_SIZE` chunks must not come out as replacement characters.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        while True:
            try:
                data = proc.read(_READ_SIZE)
            except EOFError:
                break
            except OSError:
                # The pty was torn down under us, which is what close() does.
                break
            except Exception:  # noqa: BLE001 - a dead pty must not kill the app
                break

            if data:
                text = decoder.decode(data)
                if text:
                    self.output.emit(text)

        tail = decoder.decode(b"", final=True)
        if tail:
            self.output.emit(tail)

        if not self._closing:
            self.exited.emit(self._exit_code())

    def _exit_code(self) -> int:
        proc = self._proc
        if proc is None:
            return -1
        code = proc.exitstatus
        if code is None:
            try:
                code = proc.wait()
            except Exception:  # noqa: BLE001
                code = 0
        return int(code or 0)

    # -- state -------------------------------------------------------------

    @property
    def error(self) -> Optional[str]:
        """Why the spawn failed, or ``None`` if it didn't."""
        return self._error

    @property
    def pid(self) -> int:
        return self._proc.pid if self._proc else 0

    def is_alive(self) -> bool:
        if self._proc is None:
            return False
        try:
            return bool(self._proc.isalive())
        except Exception:  # noqa: BLE001
            return False

    def has_child_process(self) -> bool:
        """Whether the shell currently has a child process running.

        The terminal view uses this to spot a full-screen program that
        switched to the alternate screen and then died without switching
        back: once the shell is alone at its prompt again, the scrollback
        can safely be restored.
        """
        if self._proc is None:
            return False
        try:
            return _has_child_process(self._proc.pid)
        except Exception:  # noqa: BLE001
            return False

    # -- io ----------------------------------------------------------------

    def write(self, data: str) -> None:
        """Send keystrokes to the shell. Silently ignored once it has exited."""
        if not data or self._proc is None:
            return
        try:
            self._proc.write(data.encode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001 - typing into a dead shell is not fatal
            pass

    def resize(self, rows: int, cols: int) -> None:
        """Tell the pty its new size.

        Without this the shell keeps formatting for the old width -- prompts
        wrap in the wrong place and full-screen programs draw outside the pane.
        """
        rows = max(1, rows)
        cols = max(1, cols)
        if (rows, cols) == (self.rows, self.cols):
            return
        self.rows, self.cols = rows, cols

        if self._proc is None:
            return
        try:
            self._proc.setwinsize(rows, cols)
        except Exception:  # noqa: BLE001
            pass

    # -- teardown ----------------------------------------------------------

    def close(self) -> None:
        """Kill the shell and stop the reader. Safe to call more than once."""
        if self._closing:
            return
        self._closing = True

        proc, self._proc = self._proc, None
        if proc is None:
            return

        try:
            proc.terminate(force=True)
        except Exception:  # noqa: BLE001 - it may already be gone
            pass

        # The reader is blocked in a read(); terminating the pty makes that
        # return empty, which raises EOFError and unwinds the thread. It is a
        # daemon, so a stubborn one can never hold up shutdown.
        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None
