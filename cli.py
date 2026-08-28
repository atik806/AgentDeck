"""Headless mode: launch terminals from the command line, no GUI toolkit needed."""

from __future__ import annotations

import sys

from config import load_config, save_config
from launcher import (
    get_available_emulators,
    get_default_emulator,
    launch_terminals,
    screen_geometry,
    single_window_labels,
    supports_single_window,
)
from platforms import get_backend


def list_emulators() -> int:
    emulators = get_available_emulators()
    if not emulators:
        print("No terminal emulators found.", file=sys.stderr)
        return 1

    backend = get_backend()
    default = get_default_emulator()
    width = max(len(label) for label, _ in emulators)
    print(f"Terminals available on this system ({backend.name}):")
    for label, command in emulators:
        marker = "*" if label == default else " "
        print(f" {marker} {label.ljust(width)}  {command}")
    if any(label == default for label, _ in emulators):
        print("\n* = default")

    title, _subtitle = single_window_labels()
    state = "yes" if supports_single_window() else "no"
    print(f"\nShared-window mode ({title}): {state}")
    x, y, w, h = screen_geometry()
    print(f"Work area: {w}x{h} at ({x}, {y})")
    return 0


def launch(
    count: int,
    emulator: str | None,
    auto_tile: bool,
    single_window: bool,
    remember: bool = False,
) -> int:
    config = load_config()
    name = emulator or config.get("terminal_emulator") or get_default_emulator()

    result = launch_terminals(
        count,
        name,
        auto_tile=auto_tile,
        use_tmux=single_window and count > 1,
        on_status=lambda msg: print(msg, file=sys.stderr),
    )

    if not result.success:
        print(
            result.error or "No terminals launched — check the emulator name",
            file=sys.stderr,
        )
        available = ", ".join(label for label, _ in get_available_emulators())
        if available:
            print(f"Available: {available}", file=sys.stderr)
        return 1

    if result.pane_count > 1 and result.mode != "standalone":
        print(f"Opened {result.count} panes in one {result.emulator} window")
    else:
        plural = "s" if result.count != 1 else ""
        print(f"Launched {result.count} {result.emulator} terminal{plural}")
    if result.warning:
        print(f"Note: {result.warning}", file=sys.stderr)

    if remember:
        config.update(
            {
                "terminal_count": count,
                "terminal_emulator": name,
                "auto_tile": auto_tile,
                "single_window": single_window,
            }
        )
        save_config(config)

    return 0
