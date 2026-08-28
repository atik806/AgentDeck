"""Offline tests for the voice engine's state machine and signal plumbing.

No microphone, no Whisper model, no network: ``AudioCapture`` /
``VADProcessor`` / ``TranscriptionEngine`` are swapped for stubs before
``VoiceEngine`` is built. Run:

    .venv\\Scripts\\python.exe test_voice_engine.py
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import voice_engine
from voice_engine import VoiceEngine

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


def pump(predicate, timeout=5.0):
    """Spin the event loop until ``predicate()`` or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


# --- stubs ------------------------------------------------------------------

class StubVAD:
    def __init__(self, **kw):
        self.kw = kw


class StubTranscriber:
    def __init__(self, **kw):
        self.kw = kw
        self.loaded = False

    def ensure_loaded(self):
        self.loaded = True


class StubCapture:
    instances = []

    def __init__(self, **kw):
        self.kw = kw
        self.started = False
        self.stopped = False
        StubCapture.instances.append(self)

    def start(self):
        self.started = True
        # Simulate the worker thread pushing a level then an utterance.
        self.kw["on_level"](0.25)
        self.kw["on_transcription"]("hello world")

    def stop(self):
        self.stopped = True


class BoomTranscriber(StubTranscriber):
    def ensure_loaded(self):
        raise RuntimeError("no network")


def install_stubs(transcriber=StubTranscriber):
    voice_engine.VADProcessor = StubVAD
    voice_engine.TranscriptionEngine = transcriber
    voice_engine.AudioCapture = StubCapture
    voice_engine.AudioDeviceManager = type(
        "D", (), {"resolve_device": staticmethod(lambda spec: None)}
    )
    voice_engine._IMPORT_OK = True


# ---------------------------------------------------------------------------
print("[1] start -> loading -> listening, text + level delivered")
install_stubs()
StubCapture.instances.clear()
eng = VoiceEngine({"voice_model": "tiny.en"})
states, texts, levels = [], [], []
eng.state.connect(states.append)
eng.transcription.connect(texts.append)
eng.level.connect(levels.append)

eng.start()
check("reaches listening", pump(lambda: eng.current_state == "listening"))
check("state order was loading then listening",
      states[:2] == ["loading", "listening"])
check("utterance delivered on the GUI thread", texts == ["hello world"])
check("level delivered", levels == [0.25])
check("is_listening true", eng.is_listening is True)
check("capture actually started", StubCapture.instances[-1].started is True)


# ---------------------------------------------------------------------------
print("[2] stop -> idle, capture torn down")
states.clear()
eng.stop()
check("reaches idle", pump(lambda: eng.current_state == "idle"))
check("idle emitted", "idle" in states)
check("capture stopped", StubCapture.instances[-1].stopped is True)
check("not listening", eng.is_listening is False)


# ---------------------------------------------------------------------------
print("[3] model load failure surfaces as error, not a crash")
install_stubs(transcriber=BoomTranscriber)
eng2 = VoiceEngine({})
st2, err2 = [], []
eng2.state.connect(st2.append)
eng2.error.connect(err2.append)
eng2.start()
check("reaches error state", pump(lambda: eng2.current_state == "error"))
check("error message carried", err2 and "no network" in err2[0])
check("not left marked listening", eng2.is_listening is False)


# ---------------------------------------------------------------------------
print("[4] toggle is a no-op flip; stop during load ends idle")
install_stubs()
eng3 = VoiceEngine({})
st3 = []
eng3.state.connect(st3.append)
eng3.toggle()          # -> start
eng3.toggle()          # -> stop (possibly before the loader finishes)
settled = pump(lambda: eng3.current_state in ("idle", "listening")
               and not eng3._busy)
check("settles, not busy", settled)
check("ends idle (stop wins)", eng3.current_state == "idle")
check("never left listening", eng3.is_listening is False)


# ---------------------------------------------------------------------------
print("[5] unavailable when the audio deps are missing")
voice_engine._IMPORT_OK = False
eng4 = VoiceEngine({})
st4 = []
eng4.state.connect(st4.append)
check("constructs as unavailable", eng4.current_state == "unavailable")
check("available flag false", eng4.available is False)
eng4.toggle()
check("toggle emits unavailable, starts nothing",
      pump(lambda: "unavailable" in st4) and eng4.is_listening is False)


# ---------------------------------------------------------------------------
print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
