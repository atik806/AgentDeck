"""
Offline tests for the voice-capture pipeline.

No microphone and no Whisper model required: the audio stream and the
transcriber are stubbed. Run:

    .venv\\Scripts\\python.exe tests\\test_pipeline.py
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voice_capture.audio.capture import AudioCapture, AudioDeviceManager
from voice_capture.vad.processor import VADProcessor
from voice_capture.transcription.engine import _NON_SPEECH_RE

SR = 16000
BLOCK = 480  # 30 ms

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


class ScriptedVAD:
    """VAD stub: returns speech flags from a supplied list, then False."""

    def __init__(self, flags):
        self._flags = list(flags)
        self.seen = []

    def process_block(self, block):
        self.seen.append(np.asarray(block))
        return self._flags.pop(0) if self._flags else False


class RecordingTranscriber:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio):
        self.calls.append(np.asarray(audio))
        return "hello world"


def silence(n=BLOCK):
    return np.zeros((n, 1), dtype=np.float32)


def noise(n=BLOCK, amp=0.4):
    rng = np.random.default_rng(0)
    return (rng.standard_normal((n, 1)) * amp).astype(np.float32)


# ---------------------------------------------------------------------------
print("[1] webrtc VAD basics")
vad = VADProcessor(backend="webrtc", aggressiveness=0, sample_rate=SR)
check("silence -> not speech", vad.process_block(np.zeros(BLOCK, dtype=np.float32)) is False)
# webrtcvad is trained on speech; broadband noise at low aggressiveness reads as speech.
loud = (np.random.default_rng(1).standard_normal(BLOCK) * 0.5).astype(np.float32)
check("loud noise -> speech", vad.process_block(loud) is True)
check("2-D block accepted", isinstance(vad.process_block(noise()), bool))
check("odd block length -> not speech (no crash)",
      vad.process_block(np.zeros(37, dtype=np.float32)) is False)

try:
    VADProcessor(backend="webrtc", aggressiveness=1, sample_rate=44100).process_block(
        np.zeros(441, dtype=np.float32)
    )
    check("unsupported sample rate raises", False)
except ValueError:
    check("unsupported sample rate raises", True)


# ---------------------------------------------------------------------------
print("[2] utterance segmentation")
# 6 speech blocks, then 10 silent blocks -> exactly one flushed segment.
flags = [True] * 6 + [False] * 12
vad = ScriptedVAD(flags)
tr = RecordingTranscriber()
cap = AudioCapture(vad=vad, transcriber=tr, sample_rate=SR, blocksize=BLOCK,
                   silence_blocks=10, min_speech_blocks=3, preroll_blocks=4)

got = []
cap.on_transcription = got.append

# Pre-load the pre-roll with 4 silent blocks (bypass the VAD stub).
for _ in range(4):
    cap._preroll.append(cap._to_mono(silence()))
for _ in range(len(flags)):
    cap._process_block(noise())

check("one segment queued", cap._segment_queue.qsize() == 1)
seg = cap._segment_queue.get_nowait()
# 4 preroll + 6 speech + 10 trailing-silence blocks = 20 blocks
check("segment length = preroll+speech+trailing silence",
      seg.shape[0] == (4 + 6 + 10) * BLOCK)
check("segment is 1-D float32", seg.ndim == 1 and seg.dtype == np.float32)


# ---------------------------------------------------------------------------
print("[3] short blips are dropped")
vad = ScriptedVAD([True, True, False] + [False] * 12)
cap = AudioCapture(vad=vad, transcriber=RecordingTranscriber(), sample_rate=SR,
                   blocksize=BLOCK, silence_blocks=10, min_speech_blocks=3)
for _ in range(15):
    cap._process_block(noise())
check("2-block utterance below min_speech_blocks -> nothing queued",
      cap._segment_queue.qsize() == 0)


# ---------------------------------------------------------------------------
print("[4] transcription worker thread")
vad = ScriptedVAD([True] * 5 + [False] * 12)
tr = RecordingTranscriber()
cap = AudioCapture(vad=vad, transcriber=tr, sample_rate=SR, blocksize=BLOCK,
                   silence_blocks=10, min_speech_blocks=3)
out = []
cap.on_transcription = out.append

import threading
cap._transcribe_thread = threading.Thread(target=cap._transcribe_loop, daemon=True)
cap._transcribe_thread.start()
for _ in range(17):
    cap._process_block(noise())
cap._segment_queue.put(None)
cap._transcribe_thread.join(timeout=5)
check("transcriber received audio", len(tr.calls) == 1)
check("callback delivered text", out == ["hello world"])
check("transcriber got 1-D float32", tr.calls[0].ndim == 1)


# ---------------------------------------------------------------------------
print("[5] flush on stop")
vad = ScriptedVAD([True] * 8)  # speaking, never goes silent
cap = AudioCapture(vad=vad, transcriber=RecordingTranscriber(), sample_rate=SR,
                   blocksize=BLOCK, silence_blocks=10, min_speech_blocks=3)
for _ in range(8):
    cap._process_block(noise())
check("mid-utterance, nothing queued yet", cap._segment_queue.qsize() == 0)
cap._flush_segment()
check("stop() flushes the pending utterance", cap._segment_queue.qsize() == 1)


# ---------------------------------------------------------------------------
print("[6] non-speech token filter")
for tok in ["[BLANK_AUDIO]", "(music)", "  [ Silence ]  ", "[typing]"]:
    check(f"{tok!r} filtered", bool(_NON_SPEECH_RE.match(tok)))
for tok in ["hello", "[music] playing loud", "a [x] b"]:
    check(f"{tok!r} kept", not _NON_SPEECH_RE.match(tok))


# ---------------------------------------------------------------------------
print("[6b] on_lost fires once for a fatal input failure")
lost = []
cap = AudioCapture(vad=ScriptedVAD([False] * 4), transcriber=RecordingTranscriber(),
                   sample_rate=SR, blocksize=BLOCK,
                   on_lost=lost.append, on_error=lambda m: lost.append(("err", m)))
cap._emit_lost("microphone disconnected")
cap._emit_lost("microphone disconnected")   # must not fire again
check("on_lost fired exactly once", lost == ["microphone disconnected"])
check("_emit_lost stops the capture loop", cap._is_running is False)

lost2 = []
cap2 = AudioCapture(sample_rate=SR, blocksize=BLOCK, on_error=lost2.append)  # no on_lost
cap2._emit_lost("gone")
check("falls back to on_error when on_lost is unset", lost2 == ["gone"])


# ---------------------------------------------------------------------------
print("[7] device resolution")
check("None -> None", AudioDeviceManager.resolve_device(None) is None)
check("'' -> None", AudioDeviceManager.resolve_device("") is None)
check("int passthrough", AudioDeviceManager.resolve_device(3) == 3)
check("bool guarded", AudioDeviceManager.resolve_device(True) is None)


# ---------------------------------------------------------------------------
print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
