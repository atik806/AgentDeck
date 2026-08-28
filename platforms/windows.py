"""Windows backend.

Two things work differently from Linux and drive the design here:

**There is no tmux.** Windows Terminal has native split panes instead, driven
from its command line, so "one shared window" is built by emitting a
``new-tab ; split-pane ... ; move-focus ...`` command sequence.
:func:`build_wt_grid_args` produces that sequence and is pure, so the pane tree
can be tested without opening a terminal.

**A launched pid is not a reliable handle on the window.** ``wt.exe`` is a stub
that forwards to an existing ``WindowsTerminal.exe`` and exits immediately, and a
``cmd.exe`` console window is owned by ``conhost.exe`` rather than by ``cmd``
itself. So each launch is paired with its top-level window: we snapshot window
handles, launch, and poll for the new one. Liveness, focus and graceful close all
go through that handle.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

from gridmath import column_counts, even_split_fraction, grid_shape, tile_rects
from models import MODE_PANES, MODE_STANDALONE, LaunchResult, TerminalInfo
from platforms import process, win32
from platforms.base import StatusCallback, TerminalBackend

WINDOWS_TERMINAL = "Windows Terminal"

#: ``(label, command)`` in preference order.
WINDOWS_EMULATORS: list[tuple[str, str]] = [
    (WINDOWS_TERMINAL, "wt"),
    ("PowerShell 7", "pwsh"),
    ("Windows PowerShell", "powershell"),
    ("Command Prompt", "cmd"),
    ("WezTerm", "wezterm-gui"),
    ("Alacritty", "alacritty"),
    ("Git Bash", "git-bash"),
    ("ConEmu", "ConEmu64"),
    ("Cmder", "Cmder"),
    ("Hyper", "Hyper"),
]

#: Fallback absolute paths for terminals that are often not on PATH.
#: ``%VAR%`` placeholders are expanded at lookup time.
EXTRA_PROBES: dict[str, list[str]] = {
    "wt": [r"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe"],
    "pwsh": [
        r"%ProgramFiles%\PowerShell\7\pwsh.exe",
        r"%ProgramFiles(x86)%\PowerShell\7\pwsh.exe",
    ],
    "git-bash": [
        r"%ProgramFiles%\Git\git-bash.exe",
        r"%ProgramW6432%\Git\git-bash.exe",
        r"%ProgramFiles(x86)%\Git\git-bash.exe",
        r"%LOCALAPPDATA%\Programs\Git\git-bash.exe",
    ],
    "wezterm-gui": [
        r"%ProgramFiles%\WezTerm\wezterm-gui.exe",
        r"%LOCALAPPDATA%\Programs\WezTerm\wezterm-gui.exe",
    ],
    "alacritty": [
        r"%ProgramFiles%\Alacritty\alacritty.exe",
        r"%LOCALAPPDATA%\Programs\Alacritty\alacritty.exe",
    ],
    "ConEmu64": [
        r"%ProgramFiles%\ConEmu\ConEmu64.exe",
        r"%ProgramFiles(x86)%\ConEmu\ConEmu64.exe",
    ],
    "Cmder": [r"%ProgramFiles%\cmder\Cmder.exe"],
    "Hyper": [r"%LOCALAPPDATA%\hyper\app-*\Hyper.exe"],
}

#: Emulators that draw their own window rather than opening a console.
GUI_EMULATORS = {"wt", "wezterm-gui", "git-bash", "ConEmu64", "Cmder", "Hyper"}

#: How long to wait for a launched terminal's window to appear.
WINDOW_DISCOVERY_TIMEOUT = 3.0
WINDOW_DISCOVERY_INTERVAL = 0.1


def normalize_command_name(command: str) -> str:
    """Bare, lowercased program name: ``C:\\...\\wt.exe`` -> ``wt``."""
    base = os.path.basename(command or "")
    stem, ext = os.path.splitext(base)
    if ext.lower() in (".exe", ".com", ".bat", ".cmd"):
        return stem.lower()
    return base.lower()


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def _probe_paths(command: str) -> str | None:
    """First existing path from ``EXTRA_PROBES[command]``, globs included."""
    import glob

    for raw in EXTRA_PROBES.get(command, []):
        candidate = _expand(raw)
        if "%" in candidate:
            # An unset variable was left behind; the path can't be valid.
            continue
        if "*" in candidate:
            matches = sorted(glob.glob(candidate))
            if matches:
                return matches[-1]
        elif os.path.isfile(candidate):
            return candidate
    return None


def find_emulator(command: str) -> str | None:
    """Resolve an emulator command to an executable path, or None."""
    found = shutil.which(command)
    if found:
        return found
    return _probe_paths(command)


def get_available_emulators() -> list[tuple[str, str]]:
    """Installed terminals as ``(label, resolved path)``."""
    available = []
    for label, command in WINDOWS_EMULATORS:
        path = find_emulator(command)
        if path:
            available.append((label, path))
    return available


def resolve_emulator(label: str) -> tuple[str, str] | None:
    """Map a UI label to ``(command, resolved path)``."""
    for known_label, command in WINDOWS_EMULATORS:
        if known_label == label:
            path = find_emulator(command)
            if path:
                return (command, path)
            return None
    # Also accept a raw command name, which is what --emulator on the CLI takes.
    for known_label, command in WINDOWS_EMULATORS:
        if normalize_command_name(label) == normalize_command_name(command):
            path = find_emulator(command)
            if path:
                return (command, path)
    return None


def _git_bash_shell(git_bash_path: str) -> list[str] | None:
    """``bash.exe`` that sits alongside ``git-bash.exe``, for use as a wt pane."""
    root = os.path.dirname(git_bash_path)
    candidate = os.path.join(root, "bin", "bash.exe")
    if os.path.isfile(candidate):
        return [candidate, "-l", "-i"]
    return None


def pane_command_for(command: str, path: str) -> list[str]:
    """Command line to run inside a Windows Terminal pane for this emulator.

    Empty means "use Windows Terminal's default profile".
    """
    if command == "wt":
        return []
    if command in ("cmd", "powershell", "pwsh"):
        return [path]
    if command == "git-bash":
        return _git_bash_shell(path) or []
    return []


def _format_size(fraction: float) -> str:
    """Compact decimal for ``wt --size``: 0.5 not 0.5000."""
    text = f"{fraction:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def build_wt_grid_args(count: int, pane_command: list[str] | None = None) -> list[str]:
    """Windows Terminal command sequence for ``count`` panes in a tiled grid.

    The pane tree is built column-first and then filled right-to-left:

    1. ``cols - 1`` vertical splits carve equal-width columns. ``--size`` sets
       the *new* pane's share of the pane being split, and focus follows the new
       pane, so the splits chain rightwards and focus ends on the last column.
    2. Each column is then split into its rows with horizontal splits, walking
       right to left. Going in that direction keeps the column to the left
       unsplit, so ``move-focus left`` always lands on a single full-height pane
       and never has to guess between stacked ones.

    Returns the argument list after the executable, ``;`` separators included as
    their own elements (correct when passed to ``Popen`` as a list, since
    ``wt`` parses its own argv).
    """
    shell = list(pane_command or [])
    if count <= 1:
        return ["new-tab", *shell]

    cols = min(grid_shape(count)[0], count)
    per_column = column_counts(count, cols)

    args: list[str] = ["new-tab", *shell]

    # 1. Equal-width columns, left to right.
    for index in range(1, cols):
        fraction = even_split_fraction(cols - index + 1)
        args += [";", "split-pane", "-V", "--size", _format_size(fraction), *shell]

    # 2. Rows within each column, right to left.
    for column in range(cols - 1, -1, -1):
        rows_here = per_column[column]
        for index in range(1, rows_here):
            fraction = even_split_fraction(rows_here - index + 1)
            args += [";", "split-pane", "-H", "--size", _format_size(fraction), *shell]
        if column > 0:
            args += [";", "move-focus", "left"]

    return args


class WindowsBackend(TerminalBackend):
    name = "windows"
    default_emulator = WINDOWS_TERMINAL
    single_window_title = "Split panes in one window"
    single_window_subtitle = (
        "Use Windows Terminal's split panes instead of opening separate windows "
        "(recommended)"
    )

    def __init__(self) -> None:
        win32.enable_dpi_awareness()

    # -- discovery ---------------------------------------------------------

    def available_emulators(self) -> list[tuple[str, str]]:
        return get_available_emulators()

    def supports_single_window(self) -> bool:
        return find_emulator("wt") is not None

    def work_area(self) -> tuple[int, int, int, int]:
        return win32.work_area()

    # -- launching ---------------------------------------------------------

    def launch(
        self,
        count: int,
        emulator: str,
        auto_tile: bool = True,
        single_window: bool = True,
        on_status: StatusCallback | None = None,
    ) -> LaunchResult:
        resolved = resolve_emulator(emulator)
        if resolved is None:
            msg = f"Terminal '{emulator}' not found"
            if on_status:
                on_status(msg)
            return LaunchResult(
                success=False,
                count=0,
                mode=MODE_STANDALONE,
                emulator=emulator,
                error=msg,
            )
        command, path = resolved

        if single_window and count > 1:
            wt_path = find_emulator("wt")
            if wt_path is None:
                if on_status:
                    on_status(
                        "Windows Terminal not found — opening separate windows instead"
                    )
            else:
                return self._launch_panes(
                    count, emulator, command, path, wt_path, on_status
                )

        return self._launch_standalone(
            count, emulator, command, path, auto_tile, on_status
        )

    def _launch_panes(
        self,
        count: int,
        emulator: str,
        command: str,
        path: str,
        wt_path: str,
        on_status: StatusCallback | None,
    ) -> LaunchResult:
        """One maximized Windows Terminal window holding ``count`` panes."""
        shell = pane_command_for(command, path)
        warning = None
        if command != "wt" and not shell:
            warning = (
                f"{emulator} can't run inside a Windows Terminal pane; "
                "using the default profile"
            )
            if on_status:
                on_status(warning)

        # -w -1 forces a new window regardless of the user's windowingBehavior.
        args = [wt_path, "-w", "-1", "-M"] + build_wt_grid_args(count, shell)

        before = _window_snapshot()
        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=win32.DETACHED_PROCESS,
                close_fds=True,
            )
        except (FileNotFoundError, OSError) as exc:
            if on_status:
                on_status(f"Error: {exc}")
            return LaunchResult(
                success=False,
                count=0,
                mode=MODE_PANES,
                emulator=emulator,
                error=str(exc),
            )

        hwnd = _await_new_window(before)
        pid = _effective_pid(proc, hwnd)

        if hwnd is None and warning is None:
            warning = "Opened, but the terminal window could not be tracked"

        return LaunchResult(
            success=True,
            count=count,
            mode=MODE_PANES,
            emulator=emulator,
            pids=[pid],
            windows=[{"pid": pid, "hwnd": hwnd, "index": 0}],
            pane_count=count,
            warning=warning,
        )

    def _launch_standalone(
        self,
        count: int,
        emulator: str,
        command: str,
        path: str,
        auto_tile: bool,
        on_status: StatusCallback | None,
    ) -> LaunchResult:
        """``count`` separate windows, optionally tiled across the work area."""
        if auto_tile:
            rects = tile_rects(count, *self.work_area())
        else:
            rects = []

        windows: list[dict] = []
        untracked = 0

        for index in range(count):
            rect = rects[index] if index < len(rects) else None
            args = [path] + self._standalone_args(command, rect)

            before = _window_snapshot()
            try:
                proc = self._spawn(command, args)
            except FileNotFoundError:
                msg = f"Command not found: {path}"
                if on_status:
                    on_status(msg)
                break
            except OSError as exc:
                if on_status:
                    on_status(f"Error: {exc}")
                break

            hwnd = _await_new_window(before)
            if hwnd is None:
                untracked += 1
            elif rect is not None:
                win32.move_window(hwnd, *rect)

            windows.append(
                {"pid": _effective_pid(proc, hwnd), "hwnd": hwnd, "index": index}
            )

        warning = None
        if untracked:
            warning = (
                f"{untracked} of {len(windows)} window(s) could not be tracked; "
                "they were launched but won't appear in the list"
            )

        return LaunchResult(
            success=bool(windows),
            count=len(windows),
            mode=MODE_STANDALONE,
            emulator=emulator,
            pids=[entry["pid"] for entry in windows],
            windows=windows,
            warning=warning,
        )

    @staticmethod
    def _standalone_args(command: str, rect: tuple[int, int, int, int] | None) -> list[str]:
        """Per-emulator size/position flags.

        Only Windows Terminal gets them: every other window is placed with
        ``SetWindowPos`` after it appears, which works uniformly and in real
        pixels. Passing ``--pos`` to ``wt`` as well just avoids a visible jump.
        """
        if command == "wt" and rect is not None:
            x, y, _width, _height = rect
            return ["--pos", f"{x},{y}", "new-tab"]
        if command == "wt":
            return ["new-tab"]
        return []

    @staticmethod
    def _spawn(command: str, args: list[str]) -> subprocess.Popen:
        """Start a terminal, detached from this process's console.

        Console shells need their own console and must keep their stdio: piping
        it to DEVNULL leaves the user staring at a blank window.
        """
        if command in GUI_EMULATORS:
            return subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=win32.DETACHED_PROCESS,
                close_fds=True,
            )
        return subprocess.Popen(
            args,
            creationflags=win32.CREATE_NEW_CONSOLE | win32.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )

    # -- lifecycle ---------------------------------------------------------

    def is_alive(self, info: TerminalInfo) -> bool:
        if info.hwnd:
            return win32.is_window(info.hwnd)
        return process.pid_alive(info.pid)

    def kill(self, info: TerminalInfo, force: bool = False) -> None:
        """Close the terminal.

        Graceful close posts WM_CLOSE to the window, which is exactly what the
        title bar's X does and lets the shell shut down cleanly.

        Force additionally terminates the process tree we spawned — but never the
        window's *owning* process unless it is that same process. On Windows a
        single ``WindowsTerminal.exe`` hosts many windows, so killing the owner
        would take down unrelated terminals the user has open.
        """
        closed = False
        if info.hwnd:
            closed = win32.close_window(info.hwnd)

        if force:
            if info.pid > 0 and process.pid_alive(info.pid):
                process.kill_pid(info.pid, force=True)
            return

        if not closed and info.pid > 0:
            process.kill_pid(info.pid, force=False)

    def focus(self, info: TerminalInfo) -> bool:
        if info.hwnd:
            return win32.focus_window(info.hwnd)
        return False


# -- window discovery -----------------------------------------------------


def _window_snapshot() -> set[int]:
    return set(win32.enum_windows())


def _await_new_window(
    before: set[int],
    timeout: float = WINDOW_DISCOVERY_TIMEOUT,
    interval: float = WINDOW_DISCOVERY_INTERVAL,
) -> int | None:
    """First visible top-level window to appear that wasn't in ``before``."""
    deadline = time.monotonic() + timeout
    while True:
        for hwnd in win32.enum_windows():
            if hwnd not in before:
                return hwnd
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def _effective_pid(proc: subprocess.Popen, hwnd: int | None) -> int:
    """The pid worth remembering for this terminal.

    Prefer the process we started. If it has already exited — which is the normal
    case for ``wt.exe``, a stub that hands off to a long-lived
    ``WindowsTerminal.exe`` — fall back to whoever owns the window.
    """
    if proc.poll() is None:
        return proc.pid
    if hwnd:
        owner = win32.window_pid(hwnd)
        if owner:
            return owner
    return proc.pid
