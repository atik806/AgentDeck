# Terminal performance — Phase 1

Status of the "Performance — the list" work. Split out because several other
sessions were editing this tree concurrently (repeated `git reset` was wiping
tracked-file edits mid-write), so the tracked-file changes are delivered as a
patch rather than left loose in the working tree.

## Delivered as new files (already in place, untracked)

| File | What |
|---|---|
| `perf.py` | Per-pane meters (`PaneMetrics`: frame/parse/flush ms, bytes/s, backlog, dropped) + `PerfHUD` translucent overlay. Near-zero cost while `perf.enabled()` is False. |
| `bench_terminal.py` | Head-less repeatable benchmark. `.venv\Scripts\python.exe bench_terminal.py` — feeds canned workloads (plain dump, colour `ls`, spinner, alt-screen redraw) through `TerminalStream`/`TerminalScreen` and times parse + a simulated paint. Compare two runs to judge a change. |

## Delivered as a patch — `phase1-perf.patch`

Applies cleanly against **pristine HEAD** copies of the three files:

```
git checkout -- windows_launcher/terminal_view.py windows_launcher/vt_screen.py windows_launcher/terminal_panel.py
git apply phase1-perf.patch      # from repo root
```

(If `terminal_view.py` / `vt_screen.py` already carry the change from this
session, `git checkout --` them first so the patch applies as one unit.
`phase1-perf-terminal_panel-only.patch` is just the HUD-wiring hunks, generated
against the *current* working tree in case only that file needs re-doing.)

### What the patch does

- **#2 Repaint only dirty rows.** `_flush` now consumes pyte's `screen.dirty`
  and calls `canvas.invalidate_rows({abs rows})` → `update(QRect)` for the
  narrow band that changed; a structural change (lines into history, alt-screen
  swap, view scroll) still does a full `invalidate_all()`. `paintEvent` also
  clamps its row loop to `event.rect()` instead of walking every visible row.
- **#3 Cache row→runs.** `TerminalCanvas._run_cache` keyed by absolute row →
  `(width, runs, cells)`; populated in paint, invalidated per-row by the dirty
  set, wiped wholesale on resize/theme/font/structural scroll. `vt_screen.row()`
  skips the `sorted(line.keys())` when the line is already in column order
  (the normal case), so it is O(n) not O(n log n).
- **#4 Hoist invariants.** `visible_cols()` computed once per frame, not per row.
- **#5 Bound the per-frame parse.** `_MAX_FEED_CHARS = 96 KB`; the remainder
  goes back on `_pending` and the still-running flush timer drains it next
  tick. A `dir /s` burst scrolls fast instead of freezing the window. (pyte
  parses ~0.5–1 MB/s pure-Python — see the benchmark — so this is a
  responsiveness cap, not a throughput one.)
- **#6 Bounded backlog.** `_MAX_BACKLOG_CHARS = 8 MB` high-water mark in
  `_on_output`; oldest held chunks are shed back to ¾ and counted
  (`PaneMetrics.dropped_bytes`, shown on the HUD).
- **#7 CSI regex pre-check.** `TerminalStream.feed` skips both regex passes
  entirely when the chunk contains no `\x1b` — the common case for a file dump.
- **HUD wiring** (`terminal_panel.py`): `Ctrl+Shift+P` toggles `PerfHUD`.

### Not done here (needs its own review)

- **#1** perf HUD + benchmark — **done** (the two new files).
- **#8** per-pane worker-thread parser / GPU grid — structural rewrite, deferred.
- **#9** `load_profile_blocking()` on the startup path — deferred to Phase 2.

## Verification

`test_vt_screen.py`, `test_wheel.py`, `test_theme.py`, `test_plugins_panel.py`,
`test_voice_overlay.py` all pass with the changes applied. `test_panel.py` shows
the same 3 pre-existing "shell started in the working folder" failures as
pristine HEAD in this environment — no new failures.
