"""Qt bridge over the ``voice_capture`` pipeline.

The heavy lifting -- microphone capture, WebRTC VAD segmentation and
whisper.cpp transcription -- already exists, Qt-free and tested, in the sibling
``voice_capture`` project. This module is the thin adapter that lets the
Multi-Terminal Panel drive it:

* it puts ``E:\\Workspace\\V4\\voice_capture`` on ``sys.path`` and imports the
  three worker modules (all optional -- a missing dependency just disables the
  feature, it never stops the panel from starting);
* it wraps the worker-thread callbacks in Qt signals, marshalled onto the GUI
  thread by a QObject bridge (the same trick ``voice_capture/app.py`` uses);
* it owns start / stop / toggle / shutdown, keeping the model loaded between
  listening sessions.

Signals (all delivered on the GUI thread):

    state(str)          idle | loading | listening | error | unavailable
    level(float)        per-block RMS while listening, ~[0, 1]
    transcription(str)  a finished utterance
    error(str)          a non-fatal problem, already surfaced as state=error
"""

from __future__ import annotations

import sys
import threading
import time as _time
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal

import voice_models
import voice_postprocess

# --- optional dependency import ------------------------------------------------
#
# The capture pipeline lives in the sibling project. Import failures here are
# expected on a machine that only installed the panel's core deps; the panel
# checks ``VoiceEngine.available`` and shows a disabled mic button.

_VC_ROOT = Path(__file__).resolve().parent.parent / "voice_capture"
if _VC_ROOT.is_dir() and str(_VC_ROOT) not in sys.path:
    sys.path.insert(0, str(_VC_ROOT))

try:  # pragma: no cover - import wiring
    from voice_capture.audio.capture import AudioCapture, AudioDeviceManager
    from voice_capture.vad.processor import VADProcessor
    from voice_capture.transcription.engine import TranscriptionEngine

    _IMPORT_OK = True
    _IMPORT_ERR = ""
except Exception as exc:  # noqa: BLE001 - any import problem = feature off
    AudioCapture = AudioDeviceManager = VADProcessor = TranscriptionEngine = None  # type: ignore
    _IMPORT_OK = False
    _IMPORT_ERR = str(exc)


class _Bridge(QObject):
    """Re-emits worker-thread callbacks as queued (GUI-thread) signals."""

    state = Signal(str)
    level = Signal(float)
    transcription = Signal(str)
    error = Signal(str)
    model_progress = Signal(int)


class VoiceEngine(QObject):
    """Owns the voice pipeline; exposes it as start/stop + Qt signals."""

    state = Signal(str)
    level = Signal(float)
    transcription = Signal(str)
    error = Signal(str)
    #: 0..100 while a first-run model download is in progress.
    model_progress = Signal(int)

    #: WebRTC VAD wants one of these; the pipeline is built around 16 kHz.
    SAMPLE_RATE = 16000
    BLOCK = 480  # 30 ms

    def __init__(self, config: Optional[dict] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._config = config or {}

        self._bridge = _Bridge()
        self._bridge.state.connect(self._set_state)
        self._bridge.level.connect(self.level)
        self._bridge.transcription.connect(self.transcription)
        self._bridge.error.connect(self.error)
        self._bridge.model_progress.connect(self.model_progress)

        self.available = bool(_IMPORT_OK)
        self.import_error = _IMPORT_ERR

        self._state = "idle" if self.available else "unavailable"
        self._listening = False
        self._busy = False

        self._vad: Any = None
        self._engine: Any = None       # TranscriptionEngine
        self._capture: Any = None      # AudioCapture
        self._mic_retry_done = False   # one auto-fallback to the default device

    # -- introspection -------------------------------------------------------

    @property
    def current_state(self) -> str:
        return self._state

    @property
    def is_listening(self) -> bool:
        return self._listening

    # -- control -----------------------------------------------------------

    def toggle(self) -> None:
        if not self.available:
            self._bridge.state.emit("unavailable")
            return
        if self._listening:
            # Works mid-load too: the loader thread sees _listening go False
            # and bails before opening the mic.
            self.stop()
        elif not self._busy:
            self.start()

    def start(self, *, _keep_retry_flag: bool = False) -> None:
        if not self.available or self._listening or self._busy:
            if not self.available:
                self._bridge.state.emit("unavailable")
            return
        self._listening = True
        self._busy = True
        if not _keep_retry_flag:
            self._mic_retry_done = False
        threading.Thread(target=self._start_pipeline, name="voice-start", daemon=True).start()

    def stop(self) -> None:
        if not self._listening and not self._busy:
            self._bridge.state.emit("idle")
            return
        self._listening = False
        self._busy = True
        threading.Thread(target=self._stop_pipeline, name="voice-stop", daemon=True).start()

    def stop_listening(self) -> None:
        """Stop a listen already in progress; a no-op when idle.

        Used when the user submits a command (presses Enter) -- the dictation
        session has served its purpose and should not keep the mic open. Covers
        the mid-load case too (``_listening`` is set before the model finishes).
        """
        if self._listening:
            self.stop()

    def shutdown(self) -> None:
        """Best-effort synchronous teardown for the window's closeEvent."""
        self._listening = False
        cap = self._capture
        if cap is not None:
            try:
                cap.stop()
            except Exception:  # noqa: BLE001
                pass

    def apply_config(self, new_config: dict) -> None:
        """Adopt changed settings, rebuilding the pipeline to match.

        Idle: drop the built pipeline so the next ``start()`` uses the new
        model / mic / tuning. Listening: stop and restart so the change takes
        effect now (a live in-place swap of the whisper model would block the
        GUI on the model load).
        """
        was_listening = self._listening
        self._config = new_config or {}
        if self._busy and not was_listening:
            return
        if was_listening:
            self.stop()
        self._capture = self._engine = self._vad = None
        if was_listening:
            threading.Thread(
                target=self._restart_after_stop, name="voice-reload", daemon=True
            ).start()

    def _restart_after_stop(self) -> None:
        deadline = _time.time() + 12.0
        while self._busy and _time.time() < deadline:
            _time.sleep(0.05)
        if not self._listening and not self._busy:
            self.start()

    # -- pipeline ---------------------------------------------------------------

    def _cfg_int(self, key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(self._config.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _ms_to_blocks(self, key: str, default_ms: int, lo: int, hi: int) -> int:
        ms = self._cfg_int(key, default_ms, lo, hi)
        return max(0, round(ms / (self.BLOCK / self.SAMPLE_RATE * 1000)))

    def _emit_transcription(self, text: str) -> None:
        try:
            text = voice_postprocess.apply(text, self._config)
        except Exception:  # noqa: BLE001 - never lose an utterance to clean-up
            pass
        if text:
            self._bridge.transcription.emit(text)

    _PERMISSION_HINTS = (
        "-9999", "unanticipated host error", "device unavailable",
        "access is denied", "not permitted", "-9996", "invalid device",
    )

    def _friendly_mic_error(self, message: str) -> str:
        low = message.lower()
        if any(h in low for h in self._PERMISSION_HINTS):
            return ("Microphone blocked — turn it on in Windows Settings ▸ "
                    "Privacy & security ▸ Microphone, then try again.")
        return message

    def _on_capture_lost(self, message: str) -> None:
        """A fatal mic failure mid-session: report it, then try the default mic once."""
        self._bridge.error.emit(self._friendly_mic_error(message))

        used_custom = self._config.get("voice_mic_device") not in (None, "")
        want_retry = (
            self._listening
            and not self._mic_retry_done
            and used_custom
            and bool(self._config.get("voice_mic_autofallback", True))
        )

        self._listening = False
        self._busy = True
        self._mic_retry_done = True

        def teardown_then(next_state: str) -> None:
            cap = self._capture
            if cap is not None:
                try:
                    cap.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._capture = self._engine = self._vad = None
            self._busy = False
            if next_state == "retry":
                cfg = dict(self._config)
                cfg["voice_mic_device"] = None      # system default
                self._config = cfg
                self._bridge.error.emit("Microphone lost — switching to the default mic…")
                self.start(_keep_retry_flag=True)
            else:
                self._bridge.state.emit("error")

        threading.Thread(
            target=teardown_then, args=("retry" if want_retry else "error",),
            name="voice-lost", daemon=True,
        ).start()

    def _ensure_built(self) -> None:
        if self._capture is not None:
            return
        cfg = self._config
        model = voice_models.resolve(cfg.get("voice_model"))
        lang = None if (cfg.get("voice_language") or "auto") == "auto" else "en"
        device = AudioDeviceManager.resolve_device(cfg.get("voice_mic_device"))
        self._vad = VADProcessor(
            backend="webrtc",
            aggressiveness=self._cfg_int("voice_vad_aggressiveness", 2, 0, 3),
            sample_rate=self.SAMPLE_RATE,
        )
        self._engine = TranscriptionEngine(
            model_size=model,
            language=lang,
            n_threads=(self._cfg_int("voice_n_threads", 0, 0, 32) or None),
            initial_prompt=(cfg.get("voice_initial_prompt") or voice_models.DEFAULT_PROMPT),
            beam_size=self._cfg_int("voice_beam_size", 1, 1, 8),
            no_context=True,
            print_realtime=False,
            print_progress=False,
        )
        self._capture = AudioCapture(
            device=device,
            sample_rate=self.SAMPLE_RATE,
            channels=1,
            blocksize=self.BLOCK,
            vad=self._vad,
            transcriber=self._engine,
            on_transcription=self._emit_transcription,
            on_error=self._bridge.error.emit,
            on_lost=self._on_capture_lost,
            on_level=self._bridge.level.emit,
            silence_blocks=self._ms_to_blocks("voice_silence_ms", 300, 120, 2000),
            min_speech_blocks=self._ms_to_blocks("voice_min_speech_ms", 120, 0, 1000),
            preroll_blocks=self._ms_to_blocks("voice_preroll_ms", 300, 0, 1000),
        )

    def _prefetch_model(self) -> None:
        """Download the resolved model if it isn't cached, reporting 0..100.

        Runs on the start thread (already off the GUI). No-op when the file is
        present or when the helper / registry is unavailable -- pywhispercpp's
        own downloader is the fallback inside ``ensure_loaded()``.
        """
        model = voice_models.resolve(self._config.get("voice_model"))
        try:
            import voice_download
        except Exception:  # noqa: BLE001
            return
        if voice_download.model_is_downloaded(model):
            return
        url = voice_download._url_for(model)
        if not url:
            return  # let ensure_loaded() fall back to pywhispercpp's fetch
        import urllib.request

        dest = voice_download.cache_path(model)
        tmp = dest.with_suffix(dest.suffix + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bridge.model_progress.emit(0)

        def hook(block_num: int, block_size: int, total: int) -> None:
            if total > 0 and self._listening:
                pct = max(0, min(100, int(block_num * block_size * 100 / total)))
                self._bridge.model_progress.emit(pct)

        try:
            urllib.request.urlretrieve(url, tmp, reporthook=hook)
            tmp.replace(dest)
            self._bridge.model_progress.emit(100)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise

    def _start_pipeline(self) -> None:
        try:
            self._ensure_built()
        except Exception as exc:  # noqa: BLE001
            self._fail(f"voice setup failed: {exc}")
            return

        self._bridge.state.emit("loading")
        try:
            # First run downloads the model. Do it ourselves with a progress
            # report when it's missing (pywhispercpp's own fetch is silent);
            # then ensure_loaded() just maps the file.
            self._prefetch_model()
        except Exception as exc:  # noqa: BLE001
            self._fail(f"model download failed: {exc}")
            return
        try:
            self._engine.ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            self._fail(f"model load failed: {exc}")
            return

        if not self._listening:  # user hit stop during the load
            self._busy = False
            self._bridge.state.emit("idle")
            return

        try:
            self._capture.start()
        except Exception as exc:  # noqa: BLE001
            self._fail(f"microphone failed: {exc}")
            return

        self._busy = False
        self._bridge.state.emit("listening")

    def _stop_pipeline(self) -> None:
        cap = self._capture
        if cap is not None:
            try:
                cap.stop()
            except Exception:  # noqa: BLE001
                pass
        self._busy = False
        self._bridge.state.emit("idle")

    def _fail(self, message: str) -> None:
        self._listening = False
        self._busy = False
        self._bridge.error.emit(message)
        self._bridge.state.emit("error")

    # -- slots ------------------------------------------------------------

    def _set_state(self, value: str) -> None:
        self._state = value
        self.state.emit(value)
