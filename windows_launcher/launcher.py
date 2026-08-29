"""
Windows Terminal Launcher Backend.

Launches and tiles terminal windows on Windows using Windows Terminal, PowerShell, or cmd.exe.
"""

import ctypes
import math
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# Windows API constants
SW_SHOW = 5
SW_RESTORE = 9
SW_MAXIMIZE = 3
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

# Load Windows API functions. Pin argument/return types: without these ctypes
# assumes C int for every argument, which truncates a 64-bit HWND.
user32 = ctypes.windll.user32
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.EnumWindows.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

@dataclass
class LaunchResult:
    success: bool
    count: int
    terminal: str
    pids: List[int] = field(default_factory=list)
    hwnds: List[int] = field(default_factory=list)
    error: Optional[str] = None

# ---------------------------------------------------------------------------
# Terminal Detection
# ---------------------------------------------------------------------------

def detect_windows_terminal() -> Optional[Path]:
    """Detect Windows Terminal (wt.exe)."""
    wt_path = shutil.which("wt")
    if wt_path:
        return Path(wt_path)

    common_paths = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "wt.exe",
    ]

    for path in common_paths:
        if path.exists():
            return path

    return None

def detect_powershell() -> Optional[Path]:
    """Detect PowerShell (pwsh.exe or powershell.exe)."""
    pwsh_path = shutil.which("pwsh")
    if pwsh_path:
        return Path(pwsh_path)

    ps_path = shutil.which("powershell")
    if ps_path:
        return Path(ps_path)

    return None

def detect_cmd() -> Optional[Path]:
    """Detect cmd.exe."""
    cmd_path = shutil.which("cmd")
    if cmd_path:
        return Path(cmd_path)
    return None

def detect_available_terminals() -> List[Tuple[str, Path]]:
    """Detect all available terminals on the system."""
    terminals = []

    wt = detect_windows_terminal()
    if wt:
        terminals.append(("Windows Terminal", wt))

    ps = detect_powershell()
    if ps:
        name = "PowerShell 7" if ps.name == "pwsh.exe" else "PowerShell"
        terminals.append((name, ps))

    cmd = detect_cmd()
    if cmd:
        terminals.append(("Command Prompt", cmd))

    return terminals

# ---------------------------------------------------------------------------
# Window Management
# ---------------------------------------------------------------------------

def get_screen_dimensions() -> Tuple[int, int]:
    """Get primary monitor dimensions."""
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

def compute_grid(count: int, screen_width: int, screen_height: int,
                 padding: int = 10, margin: int = 50) -> List[Tuple[int, int, int, int]]:
    """
    Compute window positions for tiled layout.

    Returns:
        List of (x, y, width, height) tuples.
    """
    if count <= 0:
        return []

    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)

    available_width = screen_width - (2 * margin) - (cols - 1) * padding
    available_height = screen_height - (2 * margin) - (rows - 1) * padding

    cell_width = available_width // cols
    cell_height = available_height // rows

    positions = []
    for i in range(count):
        col = i % cols
        row = i // cols

        x = margin + col * (cell_width + padding)
        y = margin + row * (cell_height + padding)

        positions.append((x, y, cell_width, cell_height))

    return positions

def set_window_position(hwnd: int, x: int, y: int, width: int, height: int) -> bool:
    """Position a window using Windows API."""
    try:
        user32.SetWindowPos(
            hwnd, None, x, y, width, height,
            SWP_NOZORDER | SWP_NOACTIVATE
        )
        return True
    except Exception as e:
        print(f"[ERROR] Failed to position window {hwnd}: {e}")
        return False

def find_window_by_pid(target_pid: int, timeout: float = 2.0) -> Optional[int]:
    """
    Find window handle (HWND) by process ID.

    Polls for up to `timeout` seconds.
    """
    start_time = time.time()
    found_hwnds: List[int] = []

    def enum_windows_callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        if pid.value == target_pid:
            found_hwnds.append(hwnd)

        return True

    # HWND and LPARAM are pointer-width: c_int would truncate both on 64-bit.
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    enum_func = WNDENUMPROC(enum_windows_callback)

    while time.time() - start_time < timeout:
        found_hwnds.clear()
        user32.EnumWindows(enum_func, 0)
        if found_hwnds:
            return found_hwnds[0]
        time.sleep(0.1)

    return None

# ---------------------------------------------------------------------------
# Terminal Launching
# ---------------------------------------------------------------------------

def launch_terminals(
    count: int,
    terminal_name: str = "Windows Terminal",
    terminal_path: Optional[Path] = None,
    auto_tile: bool = True,
    padding: int = 10,
    margin: int = 50,
) -> LaunchResult:
    """
    Launch multiple terminal windows.

    Args:
        count: Number of terminals to launch
        terminal_name: Terminal type name
        terminal_path: Path to terminal executable (auto-detect if None)
        auto_tile: Whether to tile windows
        padding: Pixels between windows
        margin: Pixels from screen edges

    Returns:
        LaunchResult with success status and launched process info
    """
    if terminal_path is None:
        terminals = detect_available_terminals()
        if not terminals:
            return LaunchResult(
                success=False, count=0, terminal=terminal_name,
                error="No terminals detected"
            )

        for name, path in terminals:
            if terminal_name.lower() in name.lower():
                terminal_path = path
                break

        if terminal_path is None:
            terminal_path = terminals[0][1]

    if not terminal_path.exists():
        return LaunchResult(
            success=False, count=0, terminal=terminal_name,
            error=f"Terminal not found: {terminal_path}"
        )

    screen_w, screen_h = get_screen_dimensions()
    positions = compute_grid(count, screen_w, screen_h, padding, margin) if auto_tile else []

    launched_pids = []
    launched_hwnds = []

    for i in range(count):
        try:
            if "wt" in terminal_path.name.lower():
                cmd = [str(terminal_path), "new-tab"]
            elif "powershell" in terminal_path.name.lower() or "pwsh" in terminal_path.name.lower():
                cmd = [str(terminal_path), "-NoExit"]
            else:
                cmd = [str(terminal_path)]

            proc = subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            launched_pids.append(proc.pid)

            if auto_tile and i < len(positions):
                time.sleep(0.3)
                hwnd = find_window_by_pid(proc.pid, timeout=2.0)
                if hwnd:
                    launched_hwnds.append(hwnd)
                    x, y, w, h = positions[i]
                    set_window_position(hwnd, x, y, w, h)

        except Exception as e:
            print(f"[ERROR] Failed to launch terminal {i+1}: {e}")
            break

    return LaunchResult(
        success=len(launched_pids) > 0,
        count=len(launched_pids),
        terminal=terminal_name,
        pids=launched_pids,
        hwnds=launched_hwnds,
    )
