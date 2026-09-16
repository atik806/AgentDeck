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
deadline = time.time() + 5
while time.time() < deadline and not any("hello-agentdeck" in chunk for chunk in collected):
    app.processEvents()
    time.sleep(0.05)
check("echoed output arrives", any("hello-agentdeck" in chunk for chunk in collected))

session.resize(30, 100)
check("resize updates rows/cols", (session.rows, session.cols) == (30, 100))

exited = []
session.exited.connect(exited.append)
session.write("exit 7\n")
deadline = time.time() + 5
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
    restored.write("echo LLP=$LD_LIBRARY_PATH=END\n")
    deadline = time.time() + 5
    while time.time() < deadline and not any("LLP=" in c and "END" in c for c in out):
        app.processEvents()
        time.sleep(0.05)
    joined = "".join(out)
    check("bundled LD_LIBRARY_PATH is not inherited",
          "_internal" not in joined)
    check("original LD_LIBRARY_PATH (from _ORIG) is restored",
          "LLP=/usr/lib/custom-real-path=END" in joined)
    restored.close()

env_no_orig = {**os.environ, "LD_LIBRARY_PATH": "/tmp/.mount_AgentDeckXXXXXX/usr/bin/_internal"}
env_no_orig.pop("LD_LIBRARY_PATH_ORIG", None)
with patch.object(pb.os, "environ", env_no_orig):
    dropped = pb.PtySession(shell="sh", rows=24, cols=80, cwd="/tmp")
    out2 = []
    dropped.output.connect(out2.append)
    dropped.write("echo LLP=$LD_LIBRARY_PATH=END\n")
    deadline = time.time() + 5
    while time.time() < deadline and not any("LLP=" in c and "END" in c for c in out2):
        app.processEvents()
        time.sleep(0.05)
    joined2 = "".join(out2)
    check("no _ORIG to restore -> LD_LIBRARY_PATH is dropped entirely, not left poisoned",
          "LLP==END" in joined2)
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
    nested.write("echo LLP=$LD_LIBRARY_PATH=END\n")
    deadline = time.time() + 5
    while time.time() < deadline and not any("LLP=" in c and "END" in c for c in out3):
        app.processEvents()
        time.sleep(0.05)
    joined3 = "".join(out3)
    check("AppImage-mount entries in _ORIG itself are also stripped",
          "_internal" not in joined3 and appdir not in joined3)
    check("a genuine non-bundle entry alongside them is still kept",
          "LLP=/usr/lib/custom-real-path=END" in joined3)
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
