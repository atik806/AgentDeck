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
        # Simulate the worker thread pushing a level, an interim, an utterance.
        self.kw["on_level"](0.25)
        if self.kw.get("on_partial"):
            self.kw["on_partial"]("hello")
        self.kw["on_transcription"]("hello world")

    def stop(self, discard_pending=False):
        self.stopped = True
        self.discard_pending = discard_pending

    def lose(self, message="could not open microphone: boom"):
        """Fire the on_lost callback like a real mid-session mic failure."""
        cb = self.kw.get("on_lost")
        if cb:
            cb(message)


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
    # Never touch the network from a test: the real _prefetch_model would try to
    # download the GGML model when it's not already cached.
    voice_engine.VoiceEngine._prefetch_model = lambda self: None


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
# the engine now emits the RAW utterance; the panel does the clean-up.
check("utterance delivered on the GUI thread", texts == ["hello world"])
check("level delivered", levels == [0.25])
check("is_listening true", eng.is_listening is True)

# partial signal (voice_show_partial defaults on)
_parts = []
eng.partial.connect(_parts.append)
install_stubs()
StubCapture.instances.clear()
eng_p = VoiceEngine({"voice_show_partial": True})
gotp = []
eng_p.partial.connect(gotp.append)
eng_p.start()
check("partial delivered on the GUI thread", pump(lambda: gotp == ["hello"]))
eng_p.stop(); pump(lambda: eng_p.current_state == "idle")

eng_off = VoiceEngine({"voice_show_partial": False})
offp = []
eng_off.partial.connect(offp.append)
eng_off.start()
check("no partials when voice_show_partial is off",
      pump(lambda: eng_off.current_state == "listening") and offp == [])
eng_off.stop(); pump(lambda: eng_off.current_state == "idle")
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
print("[4b] stop_listening ends a live session and is a no-op when idle")
install_stubs()
StubCapture.instances.clear()
eng3b = VoiceEngine({})
eng3b.stop_listening()  # nothing running -> must not start a teardown
check("no-op while idle", eng3b.current_state in ("idle",) and not eng3b._busy)
eng3b.start()
check("listening before the submit", pump(lambda: eng3b.current_state == "listening"))
eng3b.stop_listening()
check("stop_listening reaches idle", pump(lambda: eng3b.current_state == "idle"))
check("mic released", StubCapture.instances[-1].stopped is True)
check("not listening", eng3b.is_listening is False)
check("a user stop discards the pending utterance",
      getattr(StubCapture.instances[-1], "discard_pending", None) is True)


# ---------------------------------------------------------------------------
print("[4b2] a transcription that resolves after a stop is dropped")
install_stubs()
StubCapture.instances.clear()
eng3c = VoiceEngine({})
seen = []
eng3c.transcription.connect(seen.append)
eng3c.start()
check("listening", pump(lambda: eng3c.current_state == "listening"))
seen.clear()
eng3c._listening = False          # a stop just landed; teardown still running
eng3c._emit_transcription("late words")   # what the capture worker would call
app.processEvents()
check("late utterance did not reach the prompt", seen == [])
eng3c._listening = True
eng3c._emit_transcription("live words")
app.processEvents()
check("a live utterance still gets through", seen == ["live words"])
eng3c.stop(); pump(lambda: eng3c.current_state == "idle")


# ---------------------------------------------------------------------------
print("[4c] tuning config reaches the pipeline constructors")
install_stubs()
StubCapture.instances.clear()
eng4c = VoiceEngine({
    "voice_model": "small.en",
    "voice_language": "en",
    "voice_n_threads": 6,
    "voice_beam_size": 4,
    "voice_vad_aggressiveness": 3,
    "voice_silence_ms": 600,      # 600 / 30 = 20 blocks
    "voice_min_speech_ms": 90,    # 90 / 30 = 3 blocks
    "voice_preroll_ms": 150,      # 150 / 30 = 5 blocks
    "voice_post_processing": False,
})
texts4c = []
eng4c.transcription.connect(texts4c.append)
eng4c.start()
check("reaches listening", pump(lambda: eng4c.current_state == "listening"))
tk = eng4c._engine.kw
check("model_size resolved", tk.get("model_size") == "small.en")
check("language forwarded", tk.get("language") == "en")
check("n_threads forwarded", tk.get("n_threads") == 6)
check("beam_size forwarded", tk.get("beam_size") == 4)
check("no_context forced on", tk.get("no_context") is True)
vk = eng4c._vad.kw
check("vad aggressiveness forwarded", vk.get("aggressiveness") == 3)
ck = eng4c._capture.kw
check("silence_ms -> blocks", ck.get("silence_blocks") == 20)
check("min_speech_ms -> blocks", ck.get("min_speech_blocks") == 3)
check("preroll_ms -> blocks", ck.get("preroll_blocks") == 5)
check("post-processing off -> raw text", texts4c == ["hello world"])
eng4c.stop()
pump(lambda: eng4c.current_state == "idle")


# ---------------------------------------------------------------------------
print("[4d] a mid-session mic loss on a custom device retries on the default")
install_stubs()
StubCapture.instances.clear()
eng4d = VoiceEngine({"voice_mic_device": "USB Mic", "voice_mic_autofallback": True})
errs4d, st4d = [], []
eng4d.error.connect(errs4d.append)
eng4d.state.connect(st4d.append)
eng4d.start()
check("listening", pump(lambda: eng4d.current_state == "listening"))
check("started on the configured custom device",
      eng4d._config.get("voice_mic_device") == "USB Mic")
eng4d._capture.lose("could not open microphone: Error -9999")
check("error surfaced with a friendly hint",
      pump(lambda: any("Windows Settings" in e for e in errs4d)))
check("retry built a second capture",
      pump(lambda: len(StubCapture.instances) >= 2 and StubCapture.instances[-1].started,
           timeout=8))
check("auto-retry brings it back to listening",
      pump(lambda: eng4d.current_state == "listening", timeout=8))
check("retry cleared the custom device", eng4d._config.get("voice_mic_device") is None)
# a second loss must NOT retry again
errs4d.clear()
StubCapture.instances[-1].lose("could not open microphone: Error -9999")
check("second loss ends in error, no further retry",
      pump(lambda: eng4d.current_state == "error", timeout=8))
eng4d.stop(); pump(lambda: eng4d.current_state == "idle")


# ---------------------------------------------------------------------------
print("[4e] apply_config while listening restarts with the new model")
install_stubs()
StubCapture.instances.clear()
eng4e = VoiceEngine({"voice_model": "base.en"})
eng4e.start()
check("listening on base.en", pump(lambda: eng4e.current_state == "listening"))
check("engine built for base.en", eng4e._engine.kw["model_size"] == "base.en")
eng4e.apply_config({"voice_model": "small.en"})
check("comes back to listening", pump(lambda: eng4e.current_state == "listening", timeout=8))
check("rebuilt for small.en", eng4e._engine.kw["model_size"] == "small.en")
eng4e.stop(); pump(lambda: eng4e.current_state == "idle")


# ---------------------------------------------------------------------------
print("[4f] apply_config while idle just drops the built pipeline")
install_stubs()
eng4f = VoiceEngine({"voice_model": "base.en"})
eng4f.start(); pump(lambda: eng4f.current_state == "listening")
eng4f.stop(); pump(lambda: eng4f.current_state == "idle")
eng4f.apply_config({"voice_model": "small.en"})
check("pipeline dropped while idle", eng4f._capture is None and eng4f._engine is None)
check("still idle (no restart)", eng4f.current_state == "idle")


# ---------------------------------------------------------------------------
print("[4g] prewarm never triggers a background download -- only warms a cached model")
install_stubs()
StubCapture.instances.clear()
import voice_download as _vd

_orig_downloaded = _vd.model_is_downloaded
checked = []
_vd.model_is_downloaded = lambda name: checked.append(name) or False
try:
    eng4g = VoiceEngine({"voice_model": "small.en"})
    eng4g.prewarm()
    time.sleep(0.2)
    app.processEvents()
    check("asked whether the model is already cached before touching it",
          len(checked) >= 1)
    check("did NOT build/download an uncached model", eng4g._engine is None)
finally:
    _vd.model_is_downloaded = _orig_downloaded

_vd.model_is_downloaded = lambda name: True  # already on disk -> safe to warm
try:
    eng4g2 = VoiceEngine({"voice_model": "small.en"})
    eng4g2.prewarm()
    check("warms (builds) it when it IS already cached",
          pump(lambda: eng4g2._engine is not None))
finally:
    _vd.model_is_downloaded = _orig_downloaded


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
