"""Tkinter UI, used when PyGObject/GTK4 isn't available.

This is the window Windows users get: ``tkinter`` ships with CPython, so
``python main.py`` works with nothing else installed. It mirrors
:mod:`ui.window` feature for feature — count, emulator, preview, auto-tile and
shared-window toggles, the running list with per-row close, Close All, a status
line in place of ``Adw.Toast`` — and shares the palette in :mod:`ui.theme`.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from config import load_config, save_config
from launcher import (
    get_available_emulators,
    get_default_emulator,
    launch_terminals,
    single_window_labels,
    supports_single_window,
)
from ui.terminal_manager import TerminalManager
from ui.theme import MONO_FONTS, PALETTE, UI_FONTS, first_available_font
from ui.tk_preview import GridPreview

POLL_INTERVAL_MS = 2000
STATUS_CLEAR_MS = 4000


class MultiTerminalTkWindow:
    """The whole app, as a single ``tk.Tk`` window."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Multi-Terminal Launcher")
        self.root.configure(background=PALETTE["bg"])
        self.root.minsize(520, 620)

        self._scale = self._apply_dpi_scaling()
        self.ui_font, self.mono_font = self._pick_fonts()

        self.config = load_config()
        self.available = get_available_emulators()
        if not self.available:
            self.available = [("No terminal found", "")]

        self.manager = TerminalManager()
        self.manager.on_change(self._on_terminals_changed)

        self._single_supported = supports_single_window()
        self._single_title, self._single_subtitle = single_window_labels()

        # Launches run on a worker thread; results come back through this queue.
        self._results: queue.Queue = queue.Queue()
        self._launching = False
        self._selected_info = None
        self._status_job = None

        self._build_style()
        self._build_ui()
        self._sync_preview()

        self.root.after(POLL_INTERVAL_MS, self._poll)
        self.root.after(100, self._drain_results)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_request)

    # -- platform chrome ---------------------------------------------------

    def _apply_dpi_scaling(self) -> float:
        """Match Tk's scaling to the display.

        The backend opts the process into per-monitor DPI awareness so that
        window placement uses real pixels. That also stops Windows from
        bitmap-scaling our own window, so Tk has to be told the scale factor or
        everything renders tiny on a HiDPI screen.
        """
        if sys.platform != "win32":
            return 1.0
        try:
            from platforms import win32

            scale = win32.dpi_scale()
        except (ImportError, OSError):
            return 1.0
        if scale and abs(scale - 1.0) > 0.01:
            self.root.tk.call("tk", "scaling", scale * 96.0 / 72.0)
            return scale
        return 1.0

    def _pick_fonts(self) -> tuple[str, str]:
        families = tkfont.families(self.root)
        return (
            first_available_font(UI_FONTS, families),
            first_available_font(MONO_FONTS, families),
        )

    def _px(self, value: int) -> int:
        return int(round(value * self._scale))

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        # 'clam' is the only built-in theme that honours background colours on
        # Windows; 'vista' ignores them and would leave grey widgets.
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        body = (self.ui_font, 10)
        small = (self.ui_font, 9)

        style.configure(
            ".",
            background=PALETTE["bg"],
            foreground=PALETTE["fg"],
            fieldbackground=PALETTE["bg_alt"],
            font=body,
        )
        style.configure("TFrame", background=PALETTE["bg"])
        style.configure("Panel.TFrame", background=PALETTE["bg_alt"])
        style.configure("Bar.TFrame", background=PALETTE["panel"])
        style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["fg"])
        style.configure(
            "Heading.TLabel",
            background=PALETTE["bg"],
            foreground=PALETTE["fg"],
            font=(self.ui_font, 11, "bold"),
        )
        style.configure(
            "Dim.TLabel",
            background=PALETTE["bg"],
            foreground=PALETTE["fg_dim"],
            font=small,
        )
        style.configure(
            "Status.TLabel",
            background=PALETTE["panel"],
            foreground=PALETTE["fg_faint"],
            font=(self.mono_font, 9),
        )

        style.configure(
            "TCheckbutton",
            background=PALETTE["bg"],
            foreground=PALETTE["fg"],
            focuscolor=PALETTE["bg"],
        )
        style.map(
            "TCheckbutton",
            background=[("active", PALETTE["bg"])],
            foreground=[("disabled", PALETTE["fg_faint"])],
        )

        style.configure(
            "TButton",
            background=PALETTE["border"],
            foreground=PALETTE["fg"],
            borderwidth=0,
            focuscolor=PALETTE["bg"],
            padding=(self._px(12), self._px(6)),
        )
        style.map(
            "TButton",
            background=[("active", PALETTE["hover"]), ("disabled", PALETTE["bg_alt"])],
            foreground=[("disabled", PALETTE["fg_faint"])],
        )
        style.configure(
            "Accent.TButton",
            background=PALETTE["accent"],
            foreground="#ffffff",
            padding=(self._px(18), self._px(7)),
            font=(self.ui_font, 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", PALETTE["accent_hover"]),
                ("disabled", PALETTE["stopped"]),
            ],
            foreground=[("disabled", PALETTE["fg_faint"])],
        )
        style.configure(
            "Danger.TButton",
            background=PALETTE["bg_alt"],
            foreground=PALETTE["fg_faint"],
            padding=(self._px(10), self._px(3)),
            font=(self.mono_font, 9),
        )
        style.map(
            "Danger.TButton",
            background=[("active", PALETTE["hover"])],
            foreground=[("active", PALETTE["danger"])],
        )

        style.configure(
            "TSpinbox",
            fieldbackground=PALETTE["bg_alt"],
            background=PALETTE["border"],
            foreground=PALETTE["fg"],
            arrowcolor=PALETTE["fg_dim"],
            borderwidth=0,
        )
        style.configure(
            "TCombobox",
            fieldbackground=PALETTE["bg_alt"],
            background=PALETTE["border"],
            foreground=PALETTE["fg"],
            arrowcolor=PALETTE["fg_dim"],
            borderwidth=0,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PALETTE["bg_alt"])],
            foreground=[("readonly", PALETTE["fg"])],
        )
        # The dropdown list is a plain Tk listbox, styled through the option DB.
        self.root.option_add("*TCombobox*Listbox.background", PALETTE["bg_alt"])
        self.root.option_add("*TCombobox*Listbox.foreground", PALETTE["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        style.configure(
            "TSeparator", background=PALETTE["border_dim"]
        )

    # -- layout ------------------------------------------------------------

    def _build_ui(self) -> None:
        pad = self._px(14)

        outer = ttk.Frame(self.root, style="TFrame")
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="TFrame")
        header.pack(fill="x", padx=pad, pady=(pad, 0))
        ttk.Label(header, text="Multi-Terminal Launcher", style="Heading.TLabel").pack(
            side="left"
        )
        ttk.Label(
            header,
            text=f"{len(self.available)} terminal(s) detected",
            style="Dim.TLabel",
        ).pack(side="right")

        self._build_config_section(outer, pad)
        self._build_preview_section(outer, pad)
        self._build_options_section(outer, pad)
        self._build_terminal_section(outer)
        self._build_action_bar(outer, pad)

    def _build_config_section(self, parent, pad: int) -> None:
        group = ttk.Frame(parent, style="TFrame")
        group.pack(fill="x", padx=pad, pady=(pad, 0))
        group.columnconfigure(1, weight=1)

        ttk.Label(group, text="Terminal count").grid(
            row=0, column=0, sticky="w", pady=(0, self._px(4))
        )
        self.count_var = tk.IntVar(value=int(self.config.get("terminal_count", 4)))
        spin = ttk.Spinbox(
            group,
            from_=1,
            to=20,
            textvariable=self.count_var,
            width=6,
            command=self._sync_preview,
        )
        spin.grid(row=0, column=1, sticky="e", pady=(0, self._px(4)))
        # Typing into the box has to be caught separately from the arrows.
        spin.bind("<KeyRelease>", lambda _e: self._sync_preview())
        spin.bind("<FocusOut>", lambda _e: self._sync_preview())

        ttk.Label(group, text="Terminal emulator").grid(row=1, column=0, sticky="w")
        labels = [label for label, _ in self.available]
        self.emulator_var = tk.StringVar()
        combo = ttk.Combobox(
            group,
            values=labels,
            textvariable=self.emulator_var,
            state="readonly",
            width=26,
        )
        combo.grid(row=1, column=1, sticky="e")

        target = self.config.get("terminal_emulator") or get_default_emulator()
        self.emulator_var.set(target if target in labels else labels[0])

    def _build_preview_section(self, parent, pad: int) -> None:
        ttk.Label(parent, text="Grid preview", style="Dim.TLabel").pack(
            anchor="w", padx=pad, pady=(pad, self._px(4))
        )
        self.preview = GridPreview(parent, height=self._px(190))
        self.preview.pack(fill="both", expand=True, padx=pad)
        self.preview.set_single_label(self._single_title)

    def _build_options_section(self, parent, pad: int) -> None:
        group = ttk.Frame(parent, style="TFrame")
        group.pack(fill="x", padx=pad, pady=(pad, 0))

        self.tile_var = tk.BooleanVar(value=bool(self.config.get("auto_tile", True)))
        ttk.Checkbutton(
            group,
            text="Auto-tile across the screen",
            variable=self.tile_var,
            command=self._sync_preview,
        ).pack(anchor="w")

        self.single_var = tk.BooleanVar(
            value=bool(self.config.get("single_window", True)) and self._single_supported
        )
        single_check = ttk.Checkbutton(
            group,
            text=self._single_title,
            variable=self.single_var,
            command=self._sync_preview,
        )
        single_check.pack(anchor="w", pady=(self._px(2), 0))

        subtitle = self._single_subtitle
        if not self._single_supported:
            single_check.state(["disabled"])
            self.single_var.set(False)
            subtitle = "Not available: no tmux / Windows Terminal found"
        ttk.Label(group, text=subtitle, style="Dim.TLabel", wraplength=self._px(460)).pack(
            anchor="w", padx=(self._px(20), 0)
        )

    def _build_terminal_section(self, parent) -> None:
        self.term_frame = ttk.Frame(parent, style="Panel.TFrame")

        body = ttk.Frame(self.term_frame, style="Panel.TFrame")
        body.pack(fill="both", expand=True)

        self.tab_canvas = tk.Canvas(
            body,
            background=PALETTE["panel"],
            highlightthickness=0,
            borderwidth=0,
            height=self._px(120),
        )
        self.tab_canvas.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(
            body, orient="vertical", command=self.tab_canvas.yview
        )
        scrollbar.pack(side="left", fill="y")
        self.tab_canvas.configure(yscrollcommand=scrollbar.set)

        self.tab_holder = ttk.Frame(self.tab_canvas, style="Bar.TFrame")
        self._tab_window = self.tab_canvas.create_window(
            (0, 0), window=self.tab_holder, anchor="nw"
        )
        self.tab_holder.bind(
            "<Configure>",
            lambda _e: self.tab_canvas.configure(
                scrollregion=self.tab_canvas.bbox("all")
            ),
        )
        self.tab_canvas.bind(
            "<Configure>",
            lambda e: self.tab_canvas.itemconfigure(self._tab_window, width=e.width),
        )

        self.detail = tk.Text(
            body,
            width=34,
            height=9,
            background=PALETTE["bg_alt"],
            foreground=PALETTE["fg"],
            insertbackground=PALETTE["fg"],
            relief="flat",
            highlightthickness=0,
            borderwidth=0,
            font=(self.mono_font, 9),
            wrap="none",
        )
        self.detail.pack(side="left", fill="both", padx=(1, 0))
        self.detail.configure(state="disabled")

        bar = ttk.Frame(self.term_frame, style="Bar.TFrame")
        bar.pack(fill="x")
        self.list_count_label = ttk.Label(
            bar, text="● 0 running", style="Status.TLabel"
        )
        self.list_count_label.pack(
            side="left", padx=self._px(10), pady=self._px(4)
        )
        self.close_all_btn = ttk.Button(
            bar,
            text="Close All",
            style="Danger.TButton",
            command=self._on_close_all,
            state="disabled",
        )
        self.close_all_btn.pack(side="right", padx=self._px(8), pady=self._px(3))

    def _build_action_bar(self, parent, pad: int) -> None:
        bar = ttk.Frame(parent, style="TFrame")
        bar.pack(fill="x", padx=pad, pady=pad)

        self.status_label = ttk.Label(bar, text="", style="Dim.TLabel")
        self.status_label.pack(side="left")

        self.launch_btn = ttk.Button(
            bar, text="Launch", style="Accent.TButton", command=self._on_launch
        )
        self.launch_btn.pack(side="right")

    # -- state sync --------------------------------------------------------

    def _count(self) -> int:
        try:
            value = int(self.count_var.get())
        except (tk.TclError, ValueError):
            return 1
        return max(1, min(value, 20))

    def _sync_preview(self, *_args) -> None:
        self.preview.set_count(self._count())
        self.preview.set_auto_tile(self.tile_var.get())
        self.preview.set_single_window(self.single_var.get())

    def _set_status(self, message: str, transient: bool = False) -> None:
        self.status_label.configure(text=message)
        if self._status_job is not None:
            try:
                self.root.after_cancel(self._status_job)
            except tk.TclError:
                pass
            self._status_job = None
        if transient and message:
            self._status_job = self.root.after(
                STATUS_CLEAR_MS, lambda: self.status_label.configure(text="")
            )

    # -- launching ---------------------------------------------------------

    def _on_launch(self) -> None:
        if self._launching:
            return

        count = self._count()
        emulator = self.emulator_var.get()
        auto_tile = bool(self.tile_var.get())
        single_window = bool(self.single_var.get()) and count > 1

        self.config.update(
            {
                "terminal_count": count,
                "terminal_emulator": emulator,
                "auto_tile": auto_tile,
                "single_window": bool(self.single_var.get()),
            }
        )
        save_config(self.config)

        self._launching = True
        self.launch_btn.state(["disabled"])
        self._set_status("Launching…")

        # Launching blocks for a second or more (window discovery on Windows,
        # tmux setup on Linux), so it must not run on the UI thread.
        thread = threading.Thread(
            target=self._launch_worker,
            args=(count, emulator, auto_tile, single_window),
            daemon=True,
        )
        thread.start()

    def _launch_worker(
        self, count: int, emulator: str, auto_tile: bool, single_window: bool
    ) -> None:
        try:
            result = launch_terminals(
                count,
                emulator,
                auto_tile=auto_tile,
                use_tmux=single_window,
                on_status=lambda msg: self._results.put(("status", msg)),
            )
        except Exception as exc:  # noqa: BLE001 - never lose the worker silently
            self._results.put(("error", str(exc)))
            return
        self._results.put(("result", result))

    def _drain_results(self) -> None:
        try:
            while True:
                kind, payload = self._results.get_nowait()
                if kind == "status":
                    self._set_status(str(payload))
                elif kind == "result":
                    self._on_launch_finished(payload)
                elif kind == "error":
                    self._finish_launch()
                    self._set_status(f"Launch failed: {payload}", transient=True)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_results)

    def _on_launch_finished(self, result) -> None:
        self._finish_launch()

        if not result.success:
            self._set_status(
                result.error or "No terminals launched — check the emulator setting",
                transient=True,
            )
            return

        self.manager.add_result(result)
        plural = "s" if result.count != 1 else ""
        if result.pane_count > 1 and result.mode != "standalone":
            message = f"Opened {result.count} panes in one window"
        else:
            message = f"Launched {result.count} terminal{plural}"
        if result.warning:
            message = f"{message} — {result.warning}"
        self._set_status(message, transient=True)

    def _finish_launch(self) -> None:
        self._launching = False
        self.launch_btn.state(["!disabled"])

    # -- terminal list -----------------------------------------------------

    def _on_terminals_changed(self, terminals) -> None:
        for child in self.tab_holder.winfo_children():
            child.destroy()

        for info in terminals:
            self._build_row(info)

        running = sum(1 for t in terminals if t.status == "running")
        self.list_count_label.configure(
            text=f"● {running} running  |  {len(terminals)} total"
        )
        self.close_all_btn.configure(
            state="normal" if terminals else "disabled"
        )

        if terminals:
            if not self.term_frame.winfo_ismapped():
                self.term_frame.pack(fill="both", expand=False, before=None)
            if self._selected_info not in terminals:
                self._selected_info = terminals[0]
            self._show_detail(self._selected_info)
        else:
            self.term_frame.pack_forget()
            self._selected_info = None
            self._show_detail(None)

    def _build_row(self, info) -> None:
        running = info.status == "running"
        row = tk.Frame(
            self.tab_holder,
            background=PALETTE["panel"],
            highlightthickness=0,
        )
        row.pack(fill="x", pady=1)

        marker = tk.Frame(
            row,
            background=PALETTE["running"] if running else PALETTE["stopped"],
            width=self._px(3),
        )
        marker.pack(side="left", fill="y")

        label = tk.Label(
            row,
            text=info.display_name,
            background=PALETTE["panel"],
            foreground=PALETTE["fg"] if running else PALETTE["fg_faint"],
            font=(self.mono_font, 9),
            anchor="w",
            padx=self._px(8),
        )
        label.pack(side="left", fill="x", expand=True)

        pid_label = tk.Label(
            row,
            text=str(info.pid),
            background=PALETTE["panel"],
            foreground=PALETTE["fg_faint"],
            font=(self.mono_font, 8),
        )
        pid_label.pack(side="left", padx=(0, self._px(6)))

        close = tk.Label(
            row,
            text="×",
            background=PALETTE["panel"],
            foreground=PALETTE["fg_faint"],
            font=(self.ui_font, 11, "bold"),
            cursor="hand2",
            padx=self._px(6),
        )
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e, i=info: self._close_one(i))
        close.bind(
            "<Enter>", lambda _e, w=close: w.configure(foreground=PALETTE["danger"])
        )
        close.bind(
            "<Leave>", lambda _e, w=close: w.configure(foreground=PALETTE["fg_faint"])
        )

        for widget in (row, label, pid_label, marker):
            widget.bind("<Button-1>", lambda _e, i=info: self._select(i))
            widget.bind("<Double-Button-1>", lambda _e, i=info: self._focus(i))
            widget.bind("<Button-3>", lambda e, i=info: self._row_menu(e, i))

    def _select(self, info) -> None:
        self._selected_info = info
        self._show_detail(info)

    def _focus(self, info) -> None:
        if info.status != "running":
            return
        if not self.manager.focus_terminal(info):
            self._set_status("Could not raise that window", transient=True)

    def _close_one(self, info, force: bool = False) -> None:
        self.manager.close_terminal(info, force=force)

    def _on_close_all(self) -> None:
        self.manager.close_all()
        self._set_status("Closed all terminals", transient=True)

    def _row_menu(self, event, info) -> None:
        self._select(info)
        menu = tk.Menu(
            self.root,
            tearoff=0,
            background=PALETTE["bg"],
            foreground=PALETTE["fg"],
            activebackground=PALETTE["hover"],
            activeforeground=PALETTE["fg"],
            borderwidth=0,
        )
        menu.add_command(label="Close", command=lambda: self._close_one(info))
        menu.add_command(
            label="Force kill", command=lambda: self._close_one(info, force=True)
        )
        menu.add_command(label="Focus window", command=lambda: self._focus(info))
        menu.add_command(label="Copy PID", command=lambda: self._copy_pid(info.pid))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_pid(self, pid: int) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(str(pid))
        self._set_status(f"PID {pid} copied", transient=True)

    def _show_detail(self, info) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if info is not None:
            lines = [
                f"┌── {info.display_name}",
                f"│ PID:      {info.pid}",
                f"│ Mode:     {info.mode}",
                f"│ Status:   ● {info.status}",
                f"│ Emulator: {info.emulator}",
            ]
            if info.hwnd:
                lines.append(f"│ Window:   {info.hwnd:#x}")
            if info.detached:
                lines.append("│ Window:   untracked")
            if info.session_name:
                lines.append(f"│ Session:  {info.session_name}")
            if info.pane_id:
                lines.append(f"│ Pane ID:  {info.pane_id}")
            if info.pane_count > 1:
                lines.append(f"│ Panes:    {info.pane_count}")
            lines.append(f"│ Uptime:   {int(time.time() - info.launched_at)}s")
            lines.append("└── right-click for options")
            self.detail.insert("1.0", "\n".join(lines))
        self.detail.configure(state="disabled")

    # -- polling -----------------------------------------------------------

    def _poll(self) -> None:
        self.manager.update_statuses()
        if self._selected_info is not None:
            self._show_detail(self._selected_info)
        self.root.after(POLL_INTERVAL_MS, self._poll)

    def _on_close_request(self) -> None:
        # Terminals outlive the launcher on purpose: closing the app shouldn't
        # take the user's shells with it.
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run() -> int:
    return MultiTerminalTkWindow().run()
