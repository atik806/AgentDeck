"""Alternate screen buffer, REP, and scrollback regression tests."""
import sys
from vt_screen import TerminalScreen, TerminalStream

ESC = "\x1b"
fails = []

def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got  = {got!r}")
        print(f"        want = {want!r}")
        fails.append(name)

def new(cols=40, lines=8, sb=500):
    s = TerminalScreen(cols, lines, scrollback=sb)
    return s, TerminalStream(s)

print("== 1. full-screen program restores the shell screen ==")
s, st = new()
for i in range(6):
    st.feed(f"C:\> command {i}\r\n")
before = [l.rstrip() for l in s.display]
hist_before = s.history_length
st.feed(ESC + "[?1049h")
for frame in range(5):
    st.feed(ESC + "[H" + ESC + "[2J")
    st.feed("\u250c" + "\u2500" * (COLS := 38) + "\u2510\r\n")
    st.feed(f"\u2502 frame {frame:<31}\u2502\r\n")
    st.feed("\u2500" * 40 + "\r\n")
    st.feed(f"status {frame}\r\n" * 4)   # enough to scroll the alt screen
check("shell screen restored on exit", None, None) if False else None
alt_hist = s.history_length
st.feed(ESC + "[?1049l")
check("screen restored after 1049l", [l.rstrip() for l in s.display], before)
check("alt screen reports no scrollback", alt_hist, 0)
check("alt frames did not pollute scrollback", s.history_length, hist_before)

print("== 2. cursor is saved and restored across the swap ==")
s, st = new()
st.feed("abc\r\n\r\n")
st.feed(ESC + "[5;7H")               # park the cursor somewhere specific
pos = (s.cursor.x, s.cursor.y)
st.feed(ESC + "[?1049h")
st.feed(ESC + "[H" + "junk")
st.feed(ESC + "[?1049l")
check("cursor restored", (s.cursor.x, s.cursor.y), pos)

print("== 3. alternate buffer starts blank and hides the primary ==")
s, st = new()
st.feed("PRIMARY TEXT\r\n")
st.feed(ESC + "[?1049h")
check("alt buffer blank on entry", [l.rstrip() for l in s.display], [""] * 8)
check("alternate_screen flag", s.alternate_screen, True)
st.feed(ESC + "[?1049l")
check("flag cleared on exit", s.alternate_screen, False)
check("primary text back", s.display[0].rstrip(), "PRIMARY TEXT")

print("== 4. repeated 1049h must not lose the primary buffer ==")
s, st = new()
st.feed("KEEP ME\r\n")
st.feed(ESC + "[?1049h")
st.feed("frame")
st.feed(ESC + "[?1049h")             # a second enter
st.feed(ESC + "[?1049l")
check("primary survived double entry", s.display[0].rstrip(), "KEEP ME")

print("== 5. 47 and 1047 also swap ==")
for code in ("47", "1047"):
    s, st = new()
    st.feed("SHELL\r\n")
    st.feed(f"{ESC}[?{code}h")
    st.feed("tui")
    st.feed(f"{ESC}[?{code}l")
    check(f"mode {code} restores", s.display[0].rstrip(), "SHELL")

print("== 6. resize while the alt screen is up ==")
s, st = new(cols=40, lines=8)
for i in range(4):
    st.feed(f"line {i}\r\n")
st.feed(ESC + "[?1049h")
st.feed("fullscreen app")
s.resize(lines=6, columns=30)        # user drags a splitter
st.feed(ESC + "[?1049l")
# Shrinking clips from the top, so 8->6 drops the two oldest rows -- they
# must land in scrollback, not vanish, even though the alt screen was up.
check("primary clipped from the top", s.display[0].rstrip(), "line 2")
check("clipped lines went to scrollback", s.history_length, 2)
check("oldest clipped line preserved",
      "".join(c.data for _, c in s.row(0)).rstrip(), "line 0")
check("primary reshaped to new geometry", (s.lines, s.columns), (6, 30))
check("no stale rows past the new height", len(s.display), 6)

print("== 7. REP repeats the previous character ==")
s, st = new(cols=40, lines=4)
st.feed("\u2500" + ESC + "[19b")     # one dash + 19 more = 20
check("REP drew 20 dashes", s.display[0].rstrip(), "\u2500" * 20)
s, st = new(cols=10, lines=2)
st.feed("x" + ESC + "[99b")          # must clamp at the line end, not wrap
check("REP clamps at line end", s.display[0], "x" * 10)
check("REP did not wrap to next row", s.display[1].strip(), "")

print("== 8. normal scrollback still works ==")
s, st = new(cols=20, lines=4, sb=100)
for i in range(20):
    st.feed(f"row {i}\r\n")
check("scrollback captured", s.history_length, 20 - 4 + 1)
check("oldest scrollback line", "".join(c.data for _, c in s.row(0)).rstrip(), "row 0")
check("total_rows", s.total_rows(), s.history_length + 4)

print("== 8b. a lost 1049l can be recovered with exit_alternate_screen ==")
s, st = new(cols=20, lines=4, sb=100)
for i in range(20):
    st.feed(f"row {i}\r\n")
shell_hist = s.history_length
st.feed(ESC + "[?1049h")
for i in range(30):
    st.feed(f"tui redraw {i}\r\n")   # a full-screen app churning
check("scrollback hidden while alt is up", s.history_length, 0)
# the program crashes here -- no 1049l ever arrives
s.exit_alternate_screen()
check("alt flag cleared on recovery", s.alternate_screen, False)
check("shell scrollback is back", s.history_length, shell_hist)
check("shell text restored", "".join(c.data for _, c in s.row(0)).rstrip(), "row 0")
check("recovery is idempotent", (s.exit_alternate_screen(), s.alternate_screen)[1], False)
# and a normal exit still works afterwards
st.feed(ESC + "[?1049h")
st.feed(ESC + "[?1049l")
check("normal swap still works after a recovery", s.alternate_screen, False)

print("== 9. CSI sequences with a <=> private prefix are not SGR ==")
# pyte skips the '>' without noting it and dispatches on the final byte, so
# XTMODKEYS lands in select_graphic_rendition and pins underline on forever.
for seq, label in [
    ("[>4m", "XTMODKEYS reset"),
    ("[>4;2m", "XTMODKEYS modifyOtherKeys=2"),
    ("[>1u", "kitty keyboard push"),
    ("[=4m", "'=' prefix"),
]:
    s, st = new(cols=10, lines=2)
    st.feed(ESC + seq + "A")
    check(f"{label} leaves underline off", s.buffer[0][0].underscore, False)
    check(f"{label} leaves text intact", s.display[0].rstrip(), "A")

# '<' is not in pyte's skip set at all: the sequence aborts at the prefix and
# its real final byte gets drawn as text.
s, st = new(cols=10, lines=2)
st.feed(ESC + "[<u" + "A")
check("kitty keyboard pop draws nothing", s.display[0].rstrip(), "A")

# A private sequence split across two pty reads must not leak either half.
for cut in range(1, 6):
    seq = ESC + "[>4;2m"
    s, st = new(cols=10, lines=2)
    st.feed(seq[:cut])
    st.feed(seq[cut:] + "A")
    check(f"split at {cut} leaves underline off", s.buffer[0][0].underscore, False)
    check(f"split at {cut} leaves text intact", s.display[0].rstrip(), "A")

# The '?' prefix must still reach pyte -- DECSET 1049 depends on it.
s, st = new(cols=10, lines=2)
st.feed(ESC + "[?1049h")
check("'?' prefix still reaches pyte", s.alternate_screen, True)

# A '?' sequence split mid-way must not be swallowed either.
s, st = new(cols=10, lines=2)
st.feed(ESC)
st.feed("[?1049h")
check("split '?' prefix still works", s.alternate_screen, True)

# Real SGR underline must keep working.
s, st = new(cols=10, lines=2)
st.feed(ESC + "[4mA")
check("SGR 4 still underlines", s.buffer[0][0].underscore, True)
st.feed(ESC + "[24mB")
check("SGR 24 still clears it", s.buffer[0][1].underscore, False)

print("== 10. an early shrink over a blank screen makes no scrollback ==")
# A pane is created at 24 rows and its first real geometry is usually smaller
# (a 2x2 grid pane is ~21 rows). That first resize clips the top of a screen
# the shell has not drawn on yet -- pushing those empty rows would wedge blank
# lines into the scrollback above the prompt, and the pane would open scrolled
# up over the dead space.
s, st = new(cols=80, lines=24)
s.resize(lines=21, columns=63)
check("blank shrink adds no history", s.history_length, 0)
for h, w in ((22, 70), (19, 55), (25, 90), (30, 110)):   # splitter still settling
    s.resize(lines=h, columns=w)
check("blank wobble adds no history", s.history_length, 0)
st.feed("PS C:\\> ")
check("prompt lands at the top row", s.display[0].rstrip(), "PS C:\\>")
check("nothing scrolled above it", s.history_length, 0)

# ...but a shrink over real content still preserves it as scrollback.
s, st = new(cols=40, lines=8)
for i in range(4):
    st.feed(f"line {i}\r\n")
s.resize(lines=6, columns=30)
check("content shrink keeps history", s.history_length, 2)
check("content shrink oldest line", "".join(c.data for _, c in s.row(0)).rstrip(), "line 0")

# A blank line that scrolls off *naturally* is still kept -- spacing matters
# in the scrollback; it is only the resize clip that skips blanks.
s, st = new(cols=20, lines=3)
st.feed("a\r\n\r\n\r\nb\r\n")
check("natural scroll keeps blank lines", s.history_length, 2)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
