"""Offline tests for voice_download.ModelDownloadController.

urllib.request.urlretrieve is stubbed -- no network, no files leave a temp dir.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_voice_download.py
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import voice_download

app = QApplication(sys.argv)

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


def pump(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    app.processEvents()
    return pred()


# --- stub the network + registry ------------------------------------------
_scratch = os.environ["TEMP"] if "TEMP" in os.environ else "."


def fake_retrieve(url, filename, reporthook=None):
    if reporthook:
        reporthook(0, 1024, 4096)
        reporthook(2, 1024, 4096)
        reporthook(4, 1024, 4096)
    with open(filename, "wb") as f:
        f.write(b"ggml-fake")


voice_download.urllib.request.urlretrieve = fake_retrieve
voice_download._url_for = lambda name: "http://example/ggml-test.bin"
voice_download.cache_path = lambda name: __import__("pathlib").Path(_scratch) / f"ggml-{name}.bin"
voice_download.model_is_downloaded = lambda name: voice_download.cache_path(name).is_file()

# clean slate
p = voice_download.cache_path("unit.en")
if p.exists():
    p.unlink()


# ---------------------------------------------------------------------------
print("[1] a successful download emits monotonic progress then finished")
c = voice_download.ModelDownloadController()
prog, done, busy = [], [], []
c.progress.connect(prog.append)
c.finished.connect(done.append)
c.busy_changed.connect(busy.append)

import glob as _glob

c.download("unit.en")
check("finished fired", pump(lambda: done == ["unit.en"]))
check("progress is non-empty and ends at 100", prog and prog[-1] == 100)
check("progress is monotonic", prog == sorted(prog))
check("busy went True then False", busy[:1] == [True] and busy[-1] is False)
check("file landed at the cache path", p.is_file())
check("no leftover .part file",
      not _glob.glob(str(p.parent / "ggml-unit.en.bin.part*")))
p.unlink()


# ---------------------------------------------------------------------------
print("[2] already-downloaded -> finished immediately, no progress")
p.write_bytes(b"x")
c2 = voice_download.ModelDownloadController()
d2, pr2 = [], []
c2.finished.connect(d2.append)
c2.progress.connect(pr2.append)
c2.download("unit.en")
check("finished without downloading", pump(lambda: d2 == ["unit.en"]))
check("no progress emitted", pr2 == [])
p.unlink()


# ---------------------------------------------------------------------------
print("[3] a failing fetch surfaces on failed(), cleans the temp file")
def boom(url, filename, reporthook=None):
    with open(filename, "wb") as f:
        f.write(b"partial")
    raise OSError("connection reset")

voice_download.urllib.request.urlretrieve = boom
c3 = voice_download.ModelDownloadController()
f3, done3 = [], []
c3.failed.connect(f3.append)
c3.finished.connect(done3.append)
c3.download("unit.en")
check("failed fired with the message", pump(lambda: f3 and "connection reset" in f3[0]))
check("finished did NOT fire", done3 == [])
check("temp .part cleaned up",
      not _glob.glob(str(p.parent / "ggml-unit.en.bin.part*")))


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
