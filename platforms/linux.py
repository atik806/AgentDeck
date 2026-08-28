"""Linux/POSIX backend: X11-style terminals, tmux for shared windows.

This is the original ``launcher.py`` behaviour, moved behind the backend
interface. Two things changed in the move:

* the ``gi``/``Gdk`` import is gone from the launch path, so the core works
  headless (and on Windows) — monitor geometry is resolved lazily in
  :meth:`LinuxBackend.work_area`;
* the tmux helper script goes to ``tempfile.gettempdir()`` rather than a
  hard-coded ``/tmp``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time

from gridmath import tile_rects
from models import MODE_STANDALONE, MODE_TMUX, LaunchResult, TerminalInfo
from platforms import process
from platforms.base import StatusCallback, TerminalBackend

TERMINAL_EMULATORS = [
    ("ptyxis", ["ptyxis"]),
    ("gnome-terminal", ["gnome-terminal"]),
    ("xterm", ["xterm"]),
    ("konsole", ["konsole"]),
    ("kitty", ["kitty"]),
    ("alacritty", ["alacritty"]),
    ("tilix", ["tilix"]),
    ("terminator", ["terminator"]),
    ("xfce4-terminal", ["xfce4-terminal"]),
    ("lxterminal", ["lxterminal"]),
    ("urxvt", ["urxvt"]),
]

KNOWN_TERMINAL_NAMES = {name for name, _ in TERMINAL_EMULATORS}

SYSTEM_DEFAULT = "System Default"

# Rough monospace cell size, used to turn pixel tiles into the column/row counts
# that X11 --geometry actually expects.
CELL_WIDTH_PX = 8
CELL_HEIGHT_PX = 17

MIN_COLUMNS = 20
MIN_ROWS = 5


def resolve_symlinks(path: str) -> str:
    return os.path.realpath(path)


def detect_default_terminal() -> str | None:
    candidate = shutil.which("x-terminal-emulator")
    if candidate:
        return resolve_symlinks(candidate)
    env = os.environ.get("TERMINAL")
    if env:
        found = shutil.which(env)
        if found:
            return resolve_symlinks(found)
    return None


def resolve_emulator(name: str) -> list[str] | None:
    if name == SYSTEM_DEFAULT:
        path = detect_default_terminal()
        if path:
            return [path]
        return None
    for label, cmd in TERMINAL_EMULATORS:
        if label == name:
            if shutil.which(cmd[0]):
                return cmd
            return None
    return None


def get_terminal_real_name(cmd_base: str) -> str:
    return os.path.basename(resolve_symlinks(cmd_base))


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def build_tmux_script(session_name: str, count: int) -> str:
    """A shell script that opens ``count`` tiled panes and attaches to them."""
    lines = [
        "#!/bin/bash",
        f"tmux new-session -d -s {session_name}",
        f"tmux set-option -t {session_name} mouse on",
    ]
    lines.append(
        f'while [ "$(tmux list-panes -t {session_name} | wc -l)" -lt {count} ]; do'
    )
    lines.append(
        f'  target=$(tmux list-panes -t {session_name} -F "#{{pane_index}} #{{pane_height}}" '
        "| sort -k2 -rn | head -1 | cut -d' ' -f1)"
    )
    lines.append(f"  tmux split-window -t {session_name}:0.\"$target\"")
    lines.append("done")
    lines.append(f"tmux select-layout -t {session_name} tiled 2>/dev/null")
    lines.append(f"tmux set-option -t {session_name} remain-on-exit off 2>/dev/null")
    lines.append(f"tmux attach -t {session_name}")
    lines.append(f"tmux kill-session -t {session_name} 2>/dev/null")
    return "\n".join(lines) + "\n"


def get_exec_flags(term_bin: str, command: str, maximize: bool = False) -> list[str]:
    name = get_terminal_real_name(term_bin)
    if name == "ptyxis":
        flags = []
        if maximize:
            flags.append("--maximize")
        flags.extend(["-x", command])
        return flags
    if name == "gnome-terminal":
        return ["--", "bash", "-c", command]
    if name in ("xterm", "urxvt", "konsole", "kitty", "alacritty", "terminator"):
        return ["-e", command]
    return ["-e", command]


def px_to_cells(width: int, height: int) -> tuple[int, int]:
    """Convert a pixel tile to approximate character columns and rows."""
    cols = max(MIN_COLUMNS, int(width) // CELL_WIDTH_PX)
    rows = max(MIN_ROWS, int(height) // CELL_HEIGHT_PX)
    return cols, rows


def get_geometry_flags(cmd_base: str, x: int, y: int, w: int, h: int) -> list[str]:
    """Size/position flags for one emulator, given a pixel tile.

    X11 ``--geometry`` takes ``COLSxROWS+X+Y`` for terminals — character cells,
    not pixels — so pixel tiles have to be converted first. Qt's ``-geometry``
    (konsole) and kitty's ``initial_window_*`` are in pixels, and alacritty wants
    its values through ``-o`` config overrides.
    """
    name = get_terminal_real_name(cmd_base)
    cols, rows = px_to_cells(w, h)

    if name in ("gnome-terminal", "terminator", "lxterminal", "xfce4-terminal"):
        return [f"--geometry={cols}x{rows}+{x}+{y}"]
    if name in ("xterm", "urxvt"):
        return ["-geometry", f"{cols}x{rows}+{x}+{y}"]
    if name == "konsole":
        # Konsole is a Qt app: -geometry is in pixels.
        return ["-geometry", f"{w}x{h}+{x}+{y}"]
    if name == "kitty":
        # kitty has no positioning flag; size only.
        return [
            "-o",
            f"initial_window_width={w}",
            "-o",
            f"initial_window_height={h}",
        ]
    if name == "alacritty":
        return [
            "-o",
            f"window.dimensions.columns={cols}",
            "-o",
            f"window.dimensions.lines={rows}",
            "-o",
            f"window.position.x={x}",
            "-o",
            f"window.position.y={y}",
        ]
    return []


class LinuxBackend(TerminalBackend):
    name = "linux"
    default_emulator = "gnome-terminal"
    single_window_title = "Use tmux"
    single_window_subtitle = (
        "Launch terminals as tmux panes in one window instead of separate "
        "windows (recommended)"
    )

    # -- discovery ---------------------------------------------------------

    def available_emulators(self) -> list[tuple[str, str]]:
        available = []
        for label, cmd in TERMINAL_EMULATORS:
            if shutil.which(cmd[0]):
                available.append((label, cmd[0]))
        default = detect_default_terminal()
        if default and os.path.basename(default) not in KNOWN_TERMINAL_NAMES:
            available.insert(0, (SYSTEM_DEFAULT, default))
        return available

    def supports_single_window(self) -> bool:
        return tmux_available()

    def work_area(self) -> tuple[int, int, int, int]:
        geometry = self._gdk_geometry() or self._xrandr_geometry()
        if geometry:
            return geometry
        return (0, 0, 1920, 1080)

    @staticmethod
    def _gdk_geometry() -> tuple[int, int, int, int] | None:
        try:
            import gi

            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk
        except (ImportError, ValueError):
            return None
        display = Gdk.Display.get_default()
        if display is None:
            return None
        try:
            monitors = list(display.get_monitors())
        except (AttributeError, TypeError):
            return None
        if not monitors:
            return None
        geo = monitors[0].get_geometry()
        if geo.width <= 0 or geo.height <= 0:
            return None
        return (geo.x, geo.y, geo.width, geo.height)

    @staticmethod
    def _xrandr_geometry() -> tuple[int, int, int, int] | None:
        if not shutil.which("xrandr"):
            return None
        try:
            result = subprocess.run(
                ["xrandr", "--current"], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if " connected" not in line:
                continue
            for token in line.split():
                if "x" in token and "+" in token:
                    try:
                        size, x_off, y_off = token.split("+")
                        width, height = size.split("x")
                        return (int(x_off), int(y_off), int(width), int(height))
                    except ValueError:
                        continue
        return None

    # -- launching ---------------------------------------------------------

    def launch(
        self,
        count: int,
        emulator: str,
        auto_tile: bool = True,
        single_window: bool = True,
        on_status: StatusCallback | None = None,
    ) -> LaunchResult:
        cmd_base = resolve_emulator(emulator)
        if cmd_base is None:
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

        if single_window and tmux_available():
            return self._launch_tmux(count, emulator, cmd_base, on_status)
        return self._launch_standalone(count, emulator, cmd_base, auto_tile, on_status)

    def _launch_tmux(
        self,
        count: int,
        emulator: str,
        cmd_base: list[str],
        on_status: StatusCallback | None,
    ) -> LaunchResult:
        session_name = f"multi-terminal-{os.getpid()}"
        script = build_tmux_script(session_name, count)

        fd, script_path = tempfile.mkstemp(
            suffix=".sh", prefix="multi-terminal-", dir=tempfile.gettempdir()
        )
        with os.fdopen(fd, "w") as handle:
            handle.write(script)
        os.chmod(script_path, 0o755)

        term_bin = cmd_base[0]
        cmd = [term_bin] + get_exec_flags(term_bin, script_path, maximize=True)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError) as exc:
            os.unlink(script_path)
            if on_status:
                on_status(f"Error: {exc}")
            return LaunchResult(
                success=False,
                count=0,
                mode=MODE_TMUX,
                emulator=emulator,
                error=str(exc),
            )

        time.sleep(0.5)

        panes = []
        result = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-t",
                session_name,
                "-F",
                "#{pane_id} #{pane_index} #{pane_pid}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    panes.append(
                        {
                            "pane_id": parts[0],
                            "index": int(parts[1]),
                            "pid": int(parts[2]),
                        }
                    )

        return LaunchResult(
            success=True,
            count=count,
            mode=MODE_TMUX,
            emulator=emulator,
            pids=[proc.pid],
            session_name=session_name,
            panes=panes,
            pane_count=count,
        )

    def _launch_standalone(
        self,
        count: int,
        emulator: str,
        cmd_base: list[str],
        auto_tile: bool,
        on_status: StatusCallback | None,
    ) -> LaunchResult:
        term_name = get_terminal_real_name(cmd_base[0])
        launched = 0
        launched_pids = []

        if auto_tile:
            area_x, area_y, area_w, area_h = self.work_area()
            rects = tile_rects(count, area_x, area_y, area_w, area_h)
        else:
            rects = []

        for idx in range(count):
            cmd = list(cmd_base)
            if term_name == "ptyxis":
                cmd.append("--new-window")
            elif rects:
                x, y, width, height = rects[idx]
                cmd.extend(get_geometry_flags(cmd_base[0], x, y, width, height))

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                launched += 1
                launched_pids.append(proc.pid)
            except FileNotFoundError:
                if on_status:
                    on_status(f"Command not found: {cmd_base[0]}")
                break
            except OSError as exc:
                if on_status:
                    on_status(f"Error: {exc}")
                break

        return LaunchResult(
            success=launched > 0,
            count=launched,
            mode=MODE_STANDALONE,
            emulator=emulator,
            pids=launched_pids,
        )

    # -- lifecycle ---------------------------------------------------------

    def is_alive(self, info: TerminalInfo) -> bool:
        return process.pid_alive(info.pid)

    def kill(self, info: TerminalInfo, force: bool = False) -> None:
        process.kill_pid(info.pid, force)

    def focus(self, info: TerminalInfo) -> bool:
        pid = info.pid
        if shutil.which("wmctrl"):
            result = subprocess.run(
                ["wmctrl", "-l", "-p"], capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                parts = line.split(None, 3)
                if len(parts) >= 3:
                    try:
                        if int(parts[2]) == pid:
                            subprocess.Popen(["wmctrl", "-i", "-a", parts[0]])
                            return True
                    except ValueError:
                        pass
        if shutil.which("xdotool"):
            result = subprocess.run(
                ["xdotool", "search", "--pid", str(pid)],
                capture_output=True,
                text=True,
            )
            for wid in result.stdout.strip().splitlines():
                subprocess.Popen(["xdotool", "windowactivate", wid])
                return True
        return False

    # -- tmux sessions -----------------------------------------------------

    def session_alive(self, session_name: str) -> bool:
        if not session_name or not tmux_available():
            return False
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name], capture_output=True
        )
        return result.returncode == 0

    def session_pane_ids(self, session_name: str) -> list[str]:
        if not session_name or not tmux_available():
            return []
        result = subprocess.run(
            ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_id}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def kill_session(self, session_name: str) -> None:
        if not session_name or not tmux_available():
            return
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name], capture_output=True
        )

    def kill_pane(self, pane_id: str) -> None:
        if not pane_id or not tmux_available():
            return
        subprocess.run(["tmux", "kill-pane", "-t", pane_id], capture_output=True)
