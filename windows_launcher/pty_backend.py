"""Real pseudo-console sessions -- one per pane.

A Windows console is not a pipe, and that difference is the whole reason this
module exists. Hand a shell a pipe and it knows it isn't talking to a terminal:
it drops colour, stops paging, and its line editor never sees the arrow keys.
``cls`` clears nothing, ``vim`` has nowhere to draw. ConPTY
(``CreatePseudoConsole``, Windows 10 1809+) is the supported way to put a real
terminal on the other end of a shell, and ``pywinpty`` is a thin wrapper over
it.

Each session owns one blocking reader thread. ``PtyProcess.read`` parks on a
socket ``recv`` until bytes arrive, which is exactly where we want that wait --
off the GUI thread. It also stitches together UTF-8 sequences that straddle a
read boundary, so the parser upstream never sees half a codepoint.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

try:
    from winpty import PtyProcess
except ImportError as exc:  # pragma: no cover - dependency guard
    raise ImportError(
        "pywinpty is required for real terminal support.\n"
        "Install the dependencies with:  pip install -r requirements.txt"
    ) from exc


__all__ = ["PtySession", "available_shells", "resolve_shell", "DEFAULT_SHELL"]


DEFAULT_SHELL = "auto"

# How many characters to pull per read. Big enough that a flood of output
# (``dir /s``) comes through in a handful of chunks rather than thousands.
_READ_SIZE = 65536


# ---------------------------------------------------------------------------
# Shell discovery
# ---------------------------------------------------------------------------

def _git_bash() -> Optional[Path]:
    """Locate Git for Windows' bash, which isn't usually on PATH."""
    on_path = shutil.which("bash")
    if on_path and "System32" not in on_path:
        # Skip the WSL stub in System32: launching it opens a distro, not bash.
        return Path(on_path)

    for root in (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ):
        if not root:
            continue
        candidate = Path(root) / "Git" / "bin" / "bash.exe"
        if candidate.exists():
            return candidate
    return None


def available_shells() -> list[tuple[str, str, list[str]]]:
    """Installed shells as ``(key, label, argv)``, best first.

    Only shells that actually exist on this machine are returned, so the UI
    never offers something that will fail to spawn.
    """
    shells: list[tuple[str, str, list[str]]] = []

    pwsh = shutil.which("pwsh")
    if pwsh:
        shells.append(("pwsh", "PowerShell 7", [pwsh, "-NoLogo"]))

    powershell = shutil.which("powershell")
    if powershell:
        shells.append(("powershell", "Windows PowerShell", [powershell, "-NoLogo"]))

    cmd = shutil.which("cmd") or os.environ.get("COMSPEC")
    if cmd:
        shells.append(("cmd", "Command Prompt", [cmd]))

    bash = _git_bash()
    if bash:
        # -i so the profile loads and we get a prompt, --login for a sane PATH.
        shells.append(("bash", "Git Bash", [str(bash), "--login", "-i"]))

    return shells


def resolve_shell(key: str = DEFAULT_SHELL) -> tuple[str, list[str]]:
    """Turn a shell key into ``(label, argv)``, falling back to the best found.

    ``"auto"`` picks the first entry from :func:`available_shells`, which is
    ordered pwsh > powershell > cmd. If nothing at all was detected we still
    return ``cmd.exe`` -- it is present on every Windows install, and failing
    loudly at spawn time is more useful than refusing to open a pane.
    """
    shells = available_shells()

    if key and key != DEFAULT_SHELL:
        for candidate_key, label, argv in shells:
            if candidate_key == key:
                return label, argv

    if shells:
        _, label, argv = shells[0]
        return label, argv

    return "Command Prompt", [os.environ.get("COMSPEC", "cmd.exe")]


# ---------------------------------------------------------------------------
# Child-process probe
# ---------------------------------------------------------------------------

_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def _has_child_process(pid: int) -> bool:
    """True if any process currently reports ``pid`` as its parent.

    A plain Toolhelp snapshot walk -- no handles to the target, so it works
    regardless of privileges. A recycled parent PID can only ever make this
    read high (we stay on the alternate screen a little longer), never low.
    """
    if not pid:
        return False
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snapshot = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == _INVALID_HANDLE:
            return False
        try:
            entry = _PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
            if not k32.Process32First(snapshot, ctypes.byref(entry)):
                return False
            while True:
                if entry.th32ParentProcessID == pid:
                    return True
                if not k32.Process32Next(snapshot, ctypes.byref(entry)):
                    return False
        finally:
            k32.CloseHandle(snapshot)
    except Exception:  # noqa: BLE001 - a probe failure must not break the pane
        return False


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class PtySession(QObject):
    """One shell running behind a pseudo-console.

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
        env = dict(os.environ)
        # Programs that check TERM (vim, less, git, anything ncurses-ish under
        # Git Bash) need to be told the pty speaks 256 colours; ConPTY itself
        # does not set this.
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
                self.output.emit(data)

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
            self._proc.write(data)
        except Exception:  # noqa: BLE001 - typing into a dead shell is not fatal
            pass

    def resize(self, rows: int, cols: int) -> None:
        """Tell the pseudo-console its new size.

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

        # The reader is blocked in recv; terminating the pty makes that return
        # empty, which raises EOFError and unwinds the thread. It is a daemon,
        # so a stubborn one can never hold up shutdown.
        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None
