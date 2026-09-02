"""Panel layout regression tests: relayout, expand/restore, and pty geometry.

These need a real ``QApplication`` and real shells, so they run as a script
rather than under a test runner:

    .venv/Scripts/python.exe test_panel.py

Every bug these cover was invisible to a screen-model test, because each one is
about *when* Qt shows and sizes a widget:

* A tree rebuilt while the window is on screen is not shown by the layout on its
  own, so adding, closing or expanding a pane left a blank panel.
* A pane mid-reparent reports a 0-pixel width, which floors to a single column;
  resizing the screen to one column truncates every line in the pane.
* Detaching a pane hands its space to its neighbour for an instant, so a pane
  about to be hidden was told it had the expanded pane's width.
"""
import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from terminal_panel import _EXPAND_GLYPH, _RESTORE_GLYPH, TerminalPanel

# The run ends in os._exit, which skips the stdio flush -- line buffering makes
# sure everything printed has already left the process.
sys.stdout.reconfigure(line_buffering=True)

fails = []
state = {}


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got  = {got!r}")
        print(f"        want = {want!r}")
        fails.append(name)


def visible():
    return [p.isVisible() for p in panel._panes]


def alive():
    return [p.is_alive() for p in panel._panes]


def screen_geom(pane):
    s = pane.view._screen
    return (s.lines, s.columns)


def pty_geom(pane):
    return (pane.view.session.rows, pane.view.session.cols)


def text(pane):
    return "\n".join(line.rstrip() for line in pane.view._screen.display)


app = QApplication(sys.argv)


def _pro_account(cfg):
    """This suite exercises pane mechanics, not the Free/Pro gate -- run it as
    Pro so the Free pane/workspace caps never get in the way."""
    from account import AccountController

    acc = AccountController(cfg)
    acc._plan = "pro"
    return acc


_p_cfg = {"default_count": 4, "default_shell": "cmd", "font_size": 11, "layout": "grid"}
panel = TerminalPanel(
    _p_cfg,
    persist_settings=False,
    account=_pro_account(_p_cfg),
)
panel.resize(1400, 880)
panel.show()

steps = []


def step(fn):
    steps.append(fn)
    return fn


# -- 1. a relayout while the window is on screen keeps the panel visible ------

@step
def _():
    print("== 1. panel starts with every pane visible and running ==")
    check("four panes", len(panel._panes), 4)
    check("all visible", visible(), [True] * 4)
    check("all running", alive(), [True] * 4)
    check("nothing expanded", panel._zoomed, None)


@step
def _():
    print("== 2. adding a pane at runtime does not blank the panel ==")
    panel._add_pane()


@step
def _():
    check("root visible", panel._root.isVisible(), True)
    check("root laid out", panel._root.width() > 100, True)
    check("five panes, all visible", visible(), [True] * 5)


@step
def _():
    print("== 3. changing the layout mode does not blank the panel ==")
    panel._layout_mode = "columns"
    panel._relayout()


@step
def _():
    check("all still visible", visible(), [True] * 5)
    check("root laid out", panel._root.width() > 100, True)
    panel._layout_mode = "grid"
    panel._relayout()


@step
def _():
    print("== 4. closing a pane does not blank the panel ==")
    panel._close_pane(panel._panes[-1])


@step
def _():
    check("four panes left", len(panel._panes), 4)
    check("all visible", visible(), [True] * 4)
    check("badges renumbered", [p.index for p in panel._panes], [0, 1, 2, 3])
    state["tiled"] = [screen_geom(p) for p in panel._panes]


# -- 2. expand / restore -----------------------------------------------------

@step
def _():
    print("== 5. expanding a pane ==")
    # Something for the hidden pane to do while it cannot be seen.
    panel._panes[1].view.session.write("echo HIDDEN_WHILE_ZOOMED\r\n")


@step
def _():
    panel._toggle_zoom(panel._panes[0])


@step
def _():
    check("zoomed is pane 1", panel._zoomed is panel._panes[0], True)
    check("only pane 1 visible", visible(), [True, False, False, False])
    check("every shell still running", alive(), [True] * 4)
    check("its button offers restore",
          panel._panes[0]._expand_btn.text(), _RESTORE_GLYPH)
    check("the others still offer expand",
          [panel._panes[i]._expand_btn.text() for i in (1, 2, 3)],
          [_EXPAND_GLYPH] * 3)
    check("status bar names the expanded pane",
          "expanded 1" in panel._status_label.text(), True)
    check("it fills the panel body",
          panel._panes[0].width() > panel._ws_stack.width() - 20, True)

    print("== 6. the expanded pane's pty grew; the hidden ones did not ==")
    check("expanded pty is wider than tiled",
          screen_geom(panel._panes[0])[1] > state["tiled"][0][1], True)
    check("expanded screen and pty agree",
          pty_geom(panel._panes[0]), screen_geom(panel._panes[0]))
    check("hidden pane kept its own pty size",
          screen_geom(panel._panes[1]), state["tiled"][1])
    check("hidden pane ran its command",
          "HIDDEN_WHILE_ZOOMED" in text(panel._panes[1]), True)


@step
def _():
    print("== 7. switching panes while expanded moves the expansion ==")
    panel._focus_index(2)


@step
def _():
    check("zoom moved to pane 3", panel._zoomed is panel._panes[2], True)
    check("only pane 3 visible", visible(), [False, False, True, False])
    check("every shell still running", alive(), [True] * 4)


@step
def _():
    print("== 8. Ctrl+Shift+E restores the layout ==")
    panel._toggle_zoom_active()


@step
def _():
    check("nothing expanded", panel._zoomed, None)
    check("all visible again", visible(), [True] * 4)
    check("every shell still running", alive(), [True] * 4)
    check("all buttons offer expand",
          [p._expand_btn.text() for p in panel._panes], [_EXPAND_GLYPH] * 4)
    check("no pane is left marked expanded",
          [p.expanded for p in panel._panes], [False] * 4)
    check("pane widths back to tiled",
          screen_geom(panel._panes[0])[1], state["tiled"][0][1])
    check("output written while hidden survived",
          "HIDDEN_WHILE_ZOOMED" in text(panel._panes[1]), True)


@step
def _():
    print("== 9. adding a pane leaves the expanded view ==")
    panel._toggle_zoom(panel._panes[0])


@step
def _():
    check("expanded", panel._zoomed is panel._panes[0], True)
    panel._add_pane()


@step
def _():
    check("nothing expanded", panel._zoomed, None)
    check("five panes, all visible", visible(), [True] * 5)


@step
def _():
    print("== 10. closing the expanded pane restores the layout ==")
    panel._toggle_zoom(panel._panes[4])


@step
def _():
    check("expanded the last pane", panel._zoomed is panel._panes[4], True)
    panel._close_pane(panel._panes[4])


@step
def _():
    check("nothing expanded", panel._zoomed, None)
    check("four panes, all visible", visible(), [True] * 4)
    check("every shell still running", alive(), [True] * 4)


# -- 3. ordinary resizes still reach the pty ---------------------------------

@step
def _():
    print("== 11. a window resize reaches every pty ==")
    state["before"] = [screen_geom(p) for p in panel._panes]
    panel.resize(1000, 600)


@step
def _():
    now = [screen_geom(p) for p in panel._panes]
    check("screens shrank", now[0][0] < state["before"][0][0], True)
    for i, pane in enumerate(panel._panes):
        check(f"pane {i + 1} pty followed its screen", pty_geom(pane), now[i])


@step
def _():
    print("== 12. dragging a splitter reaches the pty ==")
    panel._layout_mode = "columns"
    panel._relayout()


@step
def _():
    panel._root.setSizes([2000, 8000, 8000, 8000])


@step
def _():
    widths = [screen_geom(p)[1] for p in panel._panes]
    check("the dragged pane is narrower", widths[0] < widths[1], True)
    for i, pane in enumerate(panel._panes):
        check(f"pane {i + 1} pty followed the drag",
              pty_geom(pane), screen_geom(pane))


@step
def _():
    print("== 13. a font change reaches the pty ==")
    state["rows"] = screen_geom(panel._panes[0])[0]
    panel._panes[0].set_font_size(17)


@step
def _():
    now = screen_geom(panel._panes[0])
    check("a bigger font means fewer rows", now[0] < state["rows"], True)
    check("pty followed the font change", pty_geom(panel._panes[0]), now)


# -- 4. dropping files types their quoted paths -----------------------------

@step
def _():
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDropEvent

    canvas = panel._panes[0].view.canvas
    typed = []
    canvas.input_requested.connect(typed.append)

    def drop(mime):
        canvas.dropEvent(
            QDropEvent(
                QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
            )
        )
        return typed[-1] if typed else None

    print("== 14. dropping files onto a pane types their quoted paths ==")
    files = QMimeData()
    files.setUrls(
        [
            QUrl.fromLocalFile(r"C:\tmp\a.txt"),
            QUrl.fromLocalFile(r"C:\my dir\b (old).log"),
        ]
    )
    got = drop(files)
    check("plain path bare, spaced/parenthesised path quoted",
          got, r'C:\tmp\a.txt "C:\my dir\b (old).log"')
    check("no Enter appended", (got or "").endswith(("\r", "\n")), False)
    check("the drop moved focus to the pane", canvas.hasFocus(), True)

    print("== 15. dropping plain text pastes it ==")
    words = QMimeData()
    words.setText("echo hi\r\nsecond line")
    check("newlines normalised to CR, no trailing Enter",
          drop(words), "echo hi\rsecond line")

    canvas.input_requested.disconnect(typed.append)


# -- 5. workspaces ---------------------------------------------------------

def ws_names():
    rows = panel._sidebar._list
    return [
        rows.itemAt(i).widget()._name.text()
        for i in range(rows.count() - 1)  # last item is the stretch
    ]


def ws_badges():
    rows = panel._sidebar._list
    return [
        rows.itemAt(i).widget()._badge.text()
        for i in range(rows.count() - 1)
    ]


@step
def _():
    print("== 16. the window starts with one workspace, listed in the sidebar ==")
    check("one workspace", len(panel._workspaces), 1)
    check("sidebar shows it", ws_names(), ["Workspace 1"])
    check("badge is the pane count", ws_badges(), [str(len(panel._panes))])
    check("stack holds one page", panel._ws_stack.count(), 1)
    state["ws1_panes"] = list(panel._panes)


@step
def _():
    print("== 17. adding a workspace switches to it, shells keep running ==")
    panel._add_workspace(pane_count=2)
    # Give the background workspace something to chew on while it is off screen;
    # step 20 checks the output landed.
    panel._workspaces[1]._panes[0].view.session.write("echo WS2_ALIVE\r\n")


@step
def _():
    check("two workspaces", len(panel._workspaces), 2)
    check("names", ws_names(), ["Workspace 1", "Workspace 2"])
    check("active is the new one", panel._active_ws is panel._workspaces[1], True)
    check("new workspace has two panes", len(panel._panes), 2)
    check("its badge says 2", ws_badges(), ["4", "2"])
    check("workspace 1's shells still alive",
          [p.is_alive() for p in state["ws1_panes"]], [True] * 4)
    check("workspace 1's panes are hidden now",
          any(p.isVisible() for p in state["ws1_panes"]), False)


@step
def _():
    print("== 18. switching back shows workspace 1, hides workspace 2 ==")
    panel._select_workspace(panel._workspaces[0])


@step
def _():
    check("active is workspace 1", panel._active_ws is panel._workspaces[0], True)
    check("its four panes are visible again", visible(), [True] * 4)
    check("workspace 2 panes hidden",
          any(p.isVisible() for p in panel._workspaces[1]._panes), False)


@step
def _():
    print("== 19. renaming a workspace updates its row ==")
    rows = panel._sidebar._list
    row0 = rows.itemAt(0).widget()
    check("the row has a rename (edit) button", hasattr(row0, "_edit"), True)
    # the edit button drives the same inline rename as a double-click
    row0._begin_rename()
    check("edit button opened the name field", not row0._name.isReadOnly(), True)
    row0._name.setText("BridgeMind")
    row0._commit_rename()


@step
def _():
    check("row shows the new name", ws_names(), ["BridgeMind", "Workspace 2"])
    check("workspace object was renamed too",
          panel._workspaces[0].name, "BridgeMind")


@step
def _():
    print("== 20. the hidden workspace kept running the whole time ==")
    body = "\n".join(panel._workspaces[1]._panes[0].view._screen.display)
    check("its shell echoed while off screen", "WS2_ALIVE" in body, True)


@step
def _():
    print("== 21. a font change reaches every workspace ==")
    panel._set_font(8)


@step
def _():
    check("visible workspace re-rowed",
          panel._workspaces[0]._panes[0].view.font_size, 8)
    check("hidden workspace's panes took the new size too",
          [p.view.font_size for p in panel._workspaces[1]._panes], [8, 8])


@step
def _():
    print("== 22. closing a workspace stops its shells and drops the row ==")
    doomed = panel._workspaces[1]._panes
    panel._close_workspace(panel._workspaces[1], force=True)
    state["doomed"] = doomed


@step
def _():
    check("one workspace left", len(panel._workspaces), 1)
    check("sidebar has one row", ws_names(), ["BridgeMind"])
    check("stack has one page", panel._ws_stack.count(), 1)
    check("the closed workspace's shells were stopped",
          any(p.is_alive() for p in state["doomed"]), False)


@step
def _():
    print("== 23. the last workspace cannot be closed ==")
    panel._close_workspace(panel._workspaces[0])
    check("still one workspace", len(panel._workspaces), 1)


@step
def _():
    print("== 24. the sidebar toggles ==")
    check("visible by default", panel._sidebar.isVisible(), True)
    panel._toggle_sidebar()
    check("hidden after toggle", panel._sidebar.isVisible(), False)
    panel._toggle_sidebar()
    check("shown again", panel._sidebar.isVisible(), True)


@step
def _():
    print("== 24b. the Plugins nav item swaps in the plugins panel ==")
    check("workspaces view on screen", panel._main_stack.currentWidget() is panel._ws_stack, True)
    panel._sidebar.plugins_selected.emit()
    check("plugins panel now on screen",
          panel._main_stack.currentWidget() is panel._plugins_panel, True)
    check("panel tracks it", panel._plugins_active, True)
    check("nav button checked", panel._sidebar._plugins_btn.isChecked(), True)
    check("no workspace row highlighted",
          any(panel._sidebar._list.itemAt(i).widget().property("active") == "true"
              for i in range(panel._sidebar._list.count() - 1)), False)
    check("shells kept running behind it",
          panel._workspaces[0].running_count() > 0, True)
    panel._select_workspace(panel._workspaces[0])
    check("back to the workspaces view",
          panel._main_stack.currentWidget() is panel._ws_stack, True)
    check("panel cleared the flag", panel._plugins_active, False)
    check("nav button unchecked", panel._sidebar._plugins_btn.isChecked(), False)


# -- 6. the voice overlay --------------------------------------------------
#
# The engine itself is never started here -- that would open the mic and pull a
# Whisper model. These only exercise the widget and the panel wiring that does
# not touch audio.

def overlay_in_bounds():
    ov = panel._voice_overlay
    b = panel._overlay_bounds()
    return (b.left() <= ov.x() and ov.x() + ov.width() <= b.right() + 1
            and b.top() <= ov.y() and ov.y() + ov.height() <= b.bottom() + 1)


@step
def _():
    print("== 25. the voice overlay floats over the terminal area ==")
    ov = panel._voice_overlay
    check("overlay is parented to the window (reliable stacking)",
          ov.parentWidget() is panel, True)
    check("visible on startup", ov.isVisible(), True)
    check("but idle -- not listening", panel._voice_engine.is_listening, False)
    check("toolbar button reflects visibility", panel._voice_btn.isChecked(), True)
    check("sits fully inside the terminal area", overlay_in_bounds(), True)


@step
def _():
    print("== 26. a transcription is typed into the active pane, no Enter ==")
    pane = panel._active or panel._panes[0]
    panel._set_active(pane)
    before = text(pane)
    panel._voice_engine.transcription.emit("echo VOICE_TYPED")


@step
def _():
    pane = panel._active or panel._panes[0]
    body = text(pane)
    # A narrow pane hard-wraps the prompt line, so match against the screen with
    # its line breaks removed -- what matters is the text landed at the prompt,
    # not which column the terminal happened to wrap it at.
    flat = body.replace("\n", "")
    check("spoken text reached the prompt", "echo VOICE_TYPED" in flat, True)
    check("it was not run (no command output line)",
          flat.count("VOICE_TYPED"), 1)
    check("overlay flashed the same text",
          "VOICE_TYPED" in panel._voice_overlay.caption_text(), True)


@step
def _():
    print("== 26b. only the active pane carries the focus glow ==")
    panes = panel._panes
    if len(panes) < 2:
        panel._active_ws.add_pane()
        panes = panel._panes
    panel._set_active(panes[0])
    check("active pane glows", panes[0]._glow.isEnabled(), True)
    check("the others do not", any(p._glow.isEnabled() for p in panes[1:]), False)
    panel._set_active(panes[1])
    check("the glow follows the active pane",
          (panes[0]._glow.isEnabled(), panes[1]._glow.isEnabled()), (False, True))


@step
def _():
    print("== 27. state changes reach the overlay ==")
    panel._on_voice_state("listening")
    check("overlay shows listening", panel._voice_overlay._state, "listening")
    panel._on_voice_state("idle")
    check("overlay back to idle", panel._voice_overlay._state, "idle")


@step
def _():
    print("== 28. the overlay hides/shows and remembers its place ==")
    ov = panel._voice_overlay
    panel._toggle_overlay_visible()
    check("hidden after toggle", ov.isVisible(), False)
    check("config records it", panel.config["voice_overlay_visible"], False)
    panel._toggle_overlay_visible()
    check("shown again", ov.isVisible(), True)

    b = panel._overlay_bounds()
    ov.move(b.left() + 50, b.top() + 40)
    panel._on_voice_moved(ov.pos())
    check("moved position persisted as an in-area offset",
          (panel.config["voice_overlay_x"], panel.config["voice_overlay_y"]),
          (50, 40))
    panel.resize(1180, 760)


@step
def _():
    check("still inside the terminal area after a resize", overlay_in_bounds(), True)


@step
def _():
    print("== 28b. running a line (bare Enter) stops a live voice session ==")
    eng = panel._voice_engine
    eng._listening = True  # stand in for an open dictation session
    pane = panel._active or panel._panes[0]
    pane.view.submitted.emit()  # what a bare Return/Enter emits
    check("voice session was stopped", eng.is_listening, False)
    eng._listening = False


# -- 7. wizard startup: working folder + agent command --------------------
#
# A second panel built the way main.py builds it after the setup wizard:
# startup={folder, count, agent_command}. Checks the folder reaches every
# pane's shell and the agent command auto-runs.

def flat(pane):
    """Pane text with all whitespace removed -- a narrow pane wraps the prompt,
    so the folder path can straddle a line break."""
    return "".join(text(pane).split())


@step
def _():
    print("== 29. startup= opens panes in the folder and runs the agent ==")
    from terminal_panel import TerminalPanel

    folder = os.path.dirname(os.path.abspath(__file__))
    state["startup_folder"] = folder
    _p2_cfg = {"default_count": 2, "default_shell": "cmd", "font_size": 11,
               "layout": "columns"}
    state["panel2"] = TerminalPanel(
        _p2_cfg,
        persist_settings=False,
        startup={"folder": folder, "count": 2, "agent_command": "echo AGENT_OK"},
        account=_pro_account(_p2_cfg),
    )
    state["panel2"].resize(1100, 640)
    state["panel2"].show()


@step
def _():
    pass  # let panel2's two shells start, cd into the folder, run the command


@step
def _():
    pass  # ...


@step
def _():
    pass  # ...and echo the output


@step
def _():
    pass


@step
def _():
    p2 = state["panel2"]
    folder = state["startup_folder"]
    check("panel2 has two panes", len(p2._panes), 2)
    check("window title shows the folder",
          os.path.basename(folder) in p2.windowTitle(), True)
    for i, pane in enumerate(p2._panes):
        body = flat(pane)
        check(f"pane {i + 1} shell started in the working folder",
              folder in body, True)
        check(f"pane {i + 1} auto-ran the agent command",
              "AGENT_OK" in body, True)
    p2._panes[0].restart()


@step
def _():
    pass  # the restarted shell needs to spawn, then run its startup command


@step
def _():
    pass


@step
def _():
    pass


@step
def _():
    pass


@step
def _():
    pass


@step
def _():
    p2 = state["panel2"]
    check("restart re-ran the agent command in the same pane",
          "AGENT_OK" in flat(p2._panes[0]), True)
    p2._active_ws.add_pane()


@step
def _():
    pass


@step
def _():
    pass


@step
def _():
    pass


@step
def _():
    pass


@step
def _():
    p2 = state["panel2"]
    added = p2._panes[-1]
    check("a pane added after startup is a plain shell (no agent)",
          "AGENT_OK" not in flat(added), True)
    check("but it still opens in the working folder",
          state["startup_folder"] in flat(added), True)


# -- 30. conversation handoff -------------------------------------------------

@step
def _():
    print("== 30. handoff: Pro gate + resume / transcript pane spawn ==")
    import tempfile
    import agent_sessions

    p2 = state["panel2"]
    tmp = tempfile.mkdtemp(prefix="adk-handoff-test-")
    state["handoff_tmp"] = tmp
    fake = agent_sessions.AgentSession(
        agent_key="claude", session_id="deadbeef-0000", path=None, cwd=tmp,
        title="prior work",
    )
    state["_real_locate"] = agent_sessions.locate_latest
    state["_real_md"] = agent_sessions.transcript_markdown
    state["_real_write"] = agent_sessions.write_handoff_doc
    import pathlib

    def _fake_write(folder, md, **k):
        p = pathlib.Path(tmp) / "handoff.md"
        p.write_text(md, encoding="utf-8")
        return p

    agent_sessions.locate_latest = lambda *a, **k: fake
    agent_sessions.transcript_markdown = lambda *a, **k: "# handoff\n\n## User\n\nhi\n"
    agent_sessions.write_handoff_doc = _fake_write

    # Free plan: the button is gated before any pane is spawned.
    hits = []
    state["_real_upsell"] = p2._prompt_upgrade
    p2._prompt_upgrade = lambda *a, **k: hits.append(a)
    p2.account._plan = "free"
    before = p2._active_ws.pane_count
    p2._start_handoff(p2._active_ws.panes[0])
    check("Free plan shows the upsell", bool(hits), True)
    check("Free plan spawned no pane", p2._active_ws.pane_count, before)
    p2.account._plan = "pro"
    p2._prompt_upgrade = state["_real_upsell"]


@step
def _():
    p2 = state["panel2"]
    tmp = state["handoff_tmp"]
    before = p2._active_ws.pane_count
    # Same-agent -> native resume command.
    p2._do_handoff(p2._active_ws.panes[0], {
        "source_key": "claude", "source_dir": tmp,
        "target_key": "claude", "target_command": "claude",
        "fork": True, "include_thinking": False, "any_cwd": False,
    })
    check("same-agent handoff added a pane", p2._active_ws.pane_count, before + 1)
    check("new pane runs the fork/resume command",
          p2._active_ws.panes[-1].startup_command,
          "claude --fork-session --resume deadbeef-0000")


@step
def _():
    p2 = state["panel2"]
    tmp = state["handoff_tmp"]
    before = p2._active_ws.pane_count
    # Cross-agent -> transcript file + initial-prompt-arg command.
    p2._do_handoff(p2._active_ws.panes[0], {
        "source_key": "claude", "source_dir": tmp,
        "target_key": "codex", "target_command": "codex",
        "fork": False, "include_thinking": False, "any_cwd": False,
    })
    check("cross-agent handoff added a pane", p2._active_ws.pane_count, before + 1)
    cmd = p2._active_ws.panes[-1].startup_command
    check("cross-agent pane runs codex with the transcript path as a prompt",
          cmd.startswith('codex "Read the file ') and "handoff.md" in cmd, True)
    check("handoff doc was written",
          os.path.exists(os.path.join(tmp, "handoff.md")), True)


@step
def _():
    import agent_sessions
    p2 = state["panel2"]
    agent_sessions.locate_latest = state["_real_locate"]
    agent_sessions.transcript_markdown = state["_real_md"]
    agent_sessions.write_handoff_doc = state["_real_write"]
    p2._voice_engine.shutdown()
    for ws in p2._workspaces:
        ws.shutdown()
    p2.close()


@step
def _():
    print()
    print("ALL PASS" if not fails
          else f"{len(fails)} FAILURES: {', '.join(fails)}")
    # os._exit: the pty reader threads are blocked in a read that never returns.
    os._exit(1 if fails else 0)


# Each step gets its own turn of the event loop plus time for the shells to
# answer and for the resize coalescing timer to settle. The steps are *chained*
# -- each schedules the next only once it has returned -- rather than all queued
# up front at fixed offsets: a step whose body outruns its slice (spawning a
# workspace, say) must not let the one after it fire first.
_next = [0]


def _pump():
    i = _next[0]
    if i >= len(steps):
        return
    _next[0] = i + 1
    try:
        steps[i]()
    finally:
        QTimer.singleShot(700, _pump)


QTimer.singleShot(2000, _pump)
app.exec()
