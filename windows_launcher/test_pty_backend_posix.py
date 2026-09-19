"""Tests for _pty_backend_posix — shell discovery, /proc child-probe, and a real
spawn/write/read/resize/close/exit-code round trip against /bin/sh.

POSIX only (imports ptyprocess, which isn't installed on Windows — see
requirements.txt's sys_platform markers); skips cleanly everywhere else.

    python test_pty_backend_posix.py
"""

import os
import subprocess
import sys
import time

if sys.platform == "win32":
    print("skipped: POSIX only (see test_pty_backend_win equivalent coverage in "
          "test_vt_screen.py / test_panel.py, which exercise pty_backend via the "
          "Windows dispatch)")
    sys.exit(0)

from unittest.mock import patch

import _pty_backend_posix as pb

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


#: How long to wait for a real shell to answer. Generous on purpose: the loop
#: exits the moment the text arrives, so a bigger number costs nothing on a
#: healthy machine and only buys patience on a loaded CI runner. Overridable
#: for anyone debugging a genuinely stuck spawn.
WAIT_SECONDS = float(os.environ.get("ADK_PTY_TEST_WAIT", "20"))

#: What every round trip below asks the shell to echo.
#:
#: The ``E''ND`` is deliberate, not a typo. A pty echoes the command line
#: back before the shell has run anything, so a sentinel spelled plainly
#: appears in that echo -- and ``wait_for(out, "END")`` then returns on the
#: echo alone, with the answer still in flight. Under CI load that is
#: exactly what happened: "the shell answered the probe" passed against
#: nothing but the echo, the ``"_internal" not in joined`` checks passed
#: *falsely* (an unexpanded ``$LD_LIBRARY_PATH`` cannot contain it either),
#: and only the one check that reads the resolved value failed.
#:
#: The shell concatenates ``E`` + ``''`` + ``ND`` into ``END``, so the
#: marker exists in the shell's *output* and nowhere in the echoed text.
_PROBE = "echo LLP=$LD_LIBRARY_PATH=E''ND\n"


def wait_for(chunks, needle, *, seconds=WAIT_SECONDS):
    """Pump the event loop until ``needle`` appears in the *joined* output.

    Two things here are load-bearing, and both were bugs:

    * It joins first. The old predicate asked for the whole marker inside a
      **single** chunk, but a pty hands back whatever happened to be in the
      buffer -- so a line split across two reads spun the full timeout even
      though every byte had arrived.
    * It returns whether the text turned up, so the caller can assert *that*
      before asserting anything about its content. A timeout used to surface as
      "LD_LIBRARY_PATH is poisoned" -- a failure message about the wrong thing
      entirely, which is what made this file flake in CI under two different
      names on the same commit.
    """
    deadline = time.time() + seconds
    while time.time() < deadline and needle not in "".join(chunks):
        app.processEvents()
        time.sleep(0.05)
    return needle in "".join(chunks)


# ---------------------------------------------------------------------------
print("[1] available_shells / resolve_shell")

with patch.object(pb.os, "environ", {**pb.os.environ, "SHELL": "/bin/bash"}), \
     patch.object(pb.Path, "is_file", lambda self: str(self) == "/bin/bash"), \
     patch.object(pb.shutil, "which", lambda name: {"zsh": "/usr/bin/zsh"}.get(name)):
    shells = pb.available_shells()
    check("$SHELL comes first", shells[0][0] == "bash")
    check("$SHELL argv is login+interactive", shells[0][2] == ["/bin/bash", "-l", "-i"])
    check("other found shells follow, not duplicated",
          [k for k, _, _ in shells] == ["bash", "zsh"])

# Every current distro is usr-merged: /bin is a symlink to /usr/bin, so $SHELL's
# /bin/bash and `which bash`'s /usr/bin/bash are one binary spelled two ways.
# Comparing the raw strings listed Bash twice in the shell picker.
with patch.object(pb.os, "environ", {**pb.os.environ, "SHELL": "/bin/bash"}), \
     patch.object(pb.Path, "is_file", lambda self: str(self) == "/bin/bash"), \
     patch.object(pb.os.path, "realpath",
                  lambda p: p.replace("/bin/", "/usr/bin/") if p.startswith("/bin/") else p), \
     patch.object(pb.shutil, "which",
                  lambda name: f"/usr/bin/{name}" if name == "bash" else None):
    shells = pb.available_shells()
    check("the same binary under /bin and /usr/bin is listed once",
          [k for k, _, _ in shells] == ["bash"])
    check("...and it is the $SHELL entry that survives",
          shells[0][1].endswith("(login shell)"))

with patch.object(pb.os, "environ", {}), \
     patch.object(pb.shutil, "which", lambda name: None):
    check("nothing found -> empty list", pb.available_shells() == [])
    label, argv = pb.resolve_shell()
    check("resolve_shell falls back to /bin/sh", (label, argv) == ("POSIX Shell", ["/bin/sh", "-i"]))

with patch.object(pb.os, "environ", {}), \
     patch.object(pb.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "fish" else None):
    label, argv = pb.resolve_shell("fish")
    check("resolve_shell finds a specific key", label == "Fish" and argv[0] == "/usr/bin/fish")
    label, argv = pb.resolve_shell("bash")  # not installed -> falls back to best found
    check("resolve_shell falls back when the key isn't found", label == "Fish")


# ---------------------------------------------------------------------------
print("[2] _has_child_process (/proc scan) against real processes")

child = subprocess.Popen(["sleep", "5"])
try:
    # /proc entries can lag a beat right after fork(); give it a moment.
    deadline = time.time() + 2
    while time.time() < deadline and pb._ppid_of(child.pid) != os.getpid():
        time.sleep(0.05)
    check("_ppid_of(child) == our pid", pb._ppid_of(child.pid) == os.getpid())
    check("_has_child_process(our pid) is True while child runs",
          pb._has_child_process(os.getpid()))
finally:
    child.terminate()
    child.wait(timeout=5)

check("a reaped child no longer reports our pid as parent",
      pb._ppid_of(child.pid) != os.getpid())
check("_has_child_process(0) is False", pb._has_child_process(0) is False)


# ---------------------------------------------------------------------------
print("[3] PtySession spawn/write/read/resize/close round trip against /bin/sh")

from PySide6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication([])

session = pb.PtySession(shell="sh", rows=24, cols=80, cwd="/tmp")
check("spawn succeeded", session.error is None)
check("is_alive after spawn", session.is_alive())
check("pid is set", session.pid > 0)

collected = []
session.output.connect(collected.append)

session.write("echo hello-agentdeck\n")
check("echoed output arrives", wait_for(collected, "hello-agentdeck"))

session.resize(30, 100)
check("resize updates rows/cols", (session.rows, session.cols) == (30, 100))

exited = []
session.exited.connect(exited.append)
session.write("exit 7\n")
deadline = time.time() + WAIT_SECONDS
while time.time() < deadline and not exited:
    app.processEvents()
    time.sleep(0.05)
check("shell exit reaches exited signal with the real exit code", exited == [7])

session.close()
check("close() is idempotent", session.close() is None)


# ---------------------------------------------------------------------------
print("[3.5] LD_LIBRARY_PATH_ORIG (PyInstaller's Linux bootloader) is restored, "
      "not inherited, in spawned shells")

with patch.object(pb.os, "environ", {
    **os.environ,
    "LD_LIBRARY_PATH": "/tmp/.mount_AgentDeckXXXXXX/usr/bin/_internal",
    "LD_LIBRARY_PATH_ORIG": "/usr/lib/custom-real-path",
}):
    restored = pb.PtySession(shell="sh", rows=24, cols=80, cwd="/tmp")
    out = []
    restored.output.connect(out.append)
    restored.write(_PROBE)
    answered = wait_for(out, "END")
    joined = "".join(out)
    check("the shell answered the probe", answered)
    check("bundled LD_LIBRARY_PATH is not inherited",
          answered and "_internal" not in joined)
    check("original LD_LIBRARY_PATH (from _ORIG) is restored",
          answered and "LLP=/usr/lib/custom-real-path=END" in joined)
    restored.close()

env_no_orig = {**os.environ, "LD_LIBRARY_PATH": "/tmp/.mount_AgentDeckXXXXXX/usr/bin/_internal"}
env_no_orig.pop("LD_LIBRARY_PATH_ORIG", None)
with patch.object(pb.os, "environ", env_no_orig):
    dropped = pb.PtySession(shell="sh", rows=24, cols=80, cwd="/tmp")
    out2 = []
    dropped.output.connect(out2.append)
    dropped.write(_PROBE)
    answered2 = wait_for(out2, "END")
    joined2 = "".join(out2)
    check("the shell answered the probe (no _ORIG)", answered2)
    check("no _ORIG to restore -> LD_LIBRARY_PATH is dropped entirely, not left poisoned",
          answered2 and "LLP==END" in joined2)
    dropped.close()

# When AgentDeck itself runs from inside a *mounted AppImage*, the AppImage's
# own AppRun has already exported LD_LIBRARY_PATH (pointing at the squashfs
# mount, APPDIR, and this bundle's _internal dir) before ever exec'ing this
# binary -- so LD_LIBRARY_PATH_ORIG, which PyInstaller's bootloader captures
# as "the value before I touched it", is *already* that polluted value, not
# a real pre-AppImage one. Restoring it verbatim would still hand spawned
# commands the bundle's own libssl/libcrypto (the exact flatpak/systemd-cat
# version-mismatch failures this exists to prevent).
appdir = "/tmp/.mount_AgentDeckXXXXXX"
env_nested = {
    **os.environ,
    "APPDIR": appdir,
    "LD_LIBRARY_PATH": f"{appdir}/usr/bin/_internal",
    "LD_LIBRARY_PATH_ORIG": f"{appdir}/usr/lib:/usr/lib/custom-real-path",
}
with patch.object(pb.os, "environ", env_nested):
    nested = pb.PtySession(shell="sh", rows=24, cols=80, cwd="/tmp")
    out3 = []
    nested.output.connect(out3.append)
    nested.write(_PROBE)
    answered3 = wait_for(out3, "END")
    joined3 = "".join(out3)
    check("the shell answered the probe (nested AppImage)", answered3)
    check("AppImage-mount entries in _ORIG itself are also stripped",
          answered3 and "_internal" not in joined3 and appdir not in joined3)
    check("a genuine non-bundle entry alongside them is still kept",
          answered3 and "LLP=/usr/lib/custom-real-path=END" in joined3)
    nested.close()


# ---------------------------------------------------------------------------
print("[4] a spawn failure surfaces on .error instead of raising")

with patch.object(pb, "resolve_shell",
                   lambda key=pb.DEFAULT_SHELL: ("Bogus", ["/no/such/executable-agentdeck-test"])):
    bad = pb.PtySession(shell="auto", cwd="/tmp")
check("bad argv sets .error", bad.error is not None)
check("bad argv leaves is_alive False", not bad.is_alive())
bad.close()


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
