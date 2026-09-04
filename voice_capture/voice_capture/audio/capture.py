"""
Audio capture using sounddevice.

Reads audio from the microphone with a sounddevice callback, runs each
block through the VAD to segment utterances, and hands finished speech
segments to a background transcription worker.

Pipeline (all off the GUI thread):

    sounddevice callback  --raw blocks-->  _audio_queue
    _capture_loop         --VAD / segment->  _segment_queue
    _transcribe_loop      --whisper.cpp--->  on_transcription(text)

This module deliberately has **no Qt imports** - the caller is responsible
for marshalling ``on_transcription`` / ``on_error`` onto the GUI thread
(e.g. by passing a queued Qt signal's ``emit``).
"""

import collections
import queue
import threading
import time
from typing import Any, Callable, Deque, List, Optional

import numpy as np
import sounddevice as sd


class AudioCapture:
    """
    Captures audio from a microphone using sounddevice.

    Audio blocks flow: microphone -> VAD -> segment queue -> transcriber.
    Transcription runs on its own thread so a slow inference pass never
    stalls audio capture (which would drop microphone blocks).
    """

    def __init__(
        self,
        device: Optional[int] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        blocksize: int = 480,
        dtype: str = "float32",
        vad: Any = None,           # VADProcessor instance
        transcriber: Any = None,   # TranscriptionEngine instance
        on_transcription: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_level: Optional[Callable[[float], None]] = None,
        on_lost: Optional[Callable[[str], None]] = None,
        on_partial: Optional[Callable[[str], None]] = None,
        silence_blocks: int = 10,
        min_speech_blocks: int = 3,
        preroll_blocks: int = 5,
        starve_timeout: float = 4.0,
    ):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.dtype = dtype
        self.vad = vad
        self.transcriber = transcriber
        self.on_transcription = on_transcription
        self.on_error = on_error
        # A *fatal* input problem (device unplugged / driver gone), as opposed
        # to on_error's transient hiccups. The caller stops the session.
        self.on_lost = on_lost
        self.on_level = on_level
        self.on_partial = on_partial
        self._starve_timeout = max(1.0, float(starve_timeout))
        self._lost_fired = False

        self._stream: Optional[sd.InputStream] = None
        self._audio_queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue()
        self._segment_queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue()
        self._is_running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._transcribe_thread: Optional[threading.Thread] = None
        self._cleanup_thread: Optional[threading.Thread] = None

        # Bumped on every start()/stop(). A transcribe worker captures its value
        # at launch and drops any result once it no longer matches -- so a decode
        # that was already running when the user stopped never reaches the caller.
        self._generation = 0
        # A "throw it all away" stop (user pressed stop / Enter / "stop
        # listening"): the transcribe loop drains without decoding, and any
        # in-flight whisper.cpp decode is asked to abort.
        self._discard = False
        self._abort_transcription = False

        # --- utterance segmentation state ---
        # Number of trailing silent blocks that ends an utterance.
        self._silence_threshold = max(1, int(silence_blocks))
        # Minimum speech blocks for a segment to be worth transcribing
        # (filters out VAD flicker / single clicks).
        self._min_speech_blocks = max(1, int(min_speech_blocks))
        # Blocks of audio kept before speech onset so the first phoneme
        # isn't clipped.
        self._preroll: Deque[np.ndarray] = collections.deque(
            maxlen=max(0, int(preroll_blocks))
        )
        self._speech_buffer: List[np.ndarray] = []
        self._speech_blocks = 0
        self._is_speaking = False
        self._silence_counter = 0

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start audio capture and the transcription worker."""
        if self._is_running:
            return

        self._is_running = True
        self._lost_fired = False
        self._discard = False
        self._abort_transcription = False
        self._generation += 1
        gen = self._generation
        self._audio_queue = queue.Queue()
        self._segment_queue = queue.Queue()
        self._reset_buffer()
        self._preroll.clear()

        # Let a discarding stop() abort an in-flight whisper.cpp decode.
        if self.transcriber is not None:
            setter = getattr(self.transcriber, "set_abort_check", None)
            if callable(setter):
                setter(lambda: self._abort_transcription)

        self._transcribe_thread = threading.Thread(
            target=self._transcribe_loop, args=(gen,),
            name="voice-transcribe", daemon=True,
        )
        self._transcribe_thread.start()

        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="voice-capture", daemon=True
        )
        self._capture_thread.start()

    def stop(self, discard_pending: bool = False) -> None:
        """Stop capture. Returns in a few milliseconds -- safe from the GUI thread.

        Only the microphone stream is closed synchronously (the one thing that
        must happen now to actually stop recording). The worker-thread joins --
        and, on the ``discard_pending=False`` path, any final transcription pass
        -- are handed to a background cleanup thread so the caller never blocks.

        ``discard_pending=False`` (default -- natural teardown / the standalone
        app): flush the half-spoken utterance and let the transcription worker
        finish its queue in the background; late results still arrive via
        ``on_transcription``.

        ``discard_pending=True`` (the user pressed stop / Enter / said "stop
        listening"): do **not** flush, drop everything already queued, and abort
        an in-flight whisper.cpp decode.
        """
        if not self._is_running:
            return
        self._is_running = False
        if discard_pending:
            # Invalidate any decode still in flight from this session, and tell
            # the loop to drop what's queued rather than spend a pass on it.
            self._generation += 1
            self._discard = True
            self._abort_transcription = True

        # (1) SYNCHRONOUS: cut the mic now. abort() over stop() -- it doesn't
        # wait for buffered frames to drain.
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        # (2) wake the blocked worker threads.
        self._audio_queue.put(None)
        if discard_pending:
            # Abandon whatever is already queued rather than transcribing it.
            try:
                while True:
                    self._segment_queue.get_nowait()
            except queue.Empty:
                pass
        else:
            self._flush_segment()
        self._segment_queue.put(None)

        # (3) ASYNCHRONOUS: join the workers off the caller's thread.
        cap_t, self._capture_thread = self._capture_thread, None
        tr_t, self._transcribe_thread = self._transcribe_thread, None
        gen = self._generation

        def _cleanup() -> None:
            if cap_t is not None:
                cap_t.join(timeout=2.0)
            if tr_t is not None:
                tr_t.join(timeout=30.0)
            if gen == self._generation:  # no newer session has taken over
                self._discard = False
                self._abort_transcription = False

        self._cleanup_thread = threading.Thread(
            target=_cleanup, name="voice-capture-cleanup", daemon=True
        )
        self._cleanup_thread.start()

    def cancel(self) -> None:
        """Immediate throw-away stop -- alias for ``stop(discard_pending=True)``."""
        self.stop(discard_pending=True)

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ------------------------------------------------------------------
    # capture thread
    # ------------------------------------------------------------------
    def _capture_loop(self) -> None:
        """Own the InputStream and segment its blocks via the VAD."""
        def callback(indata: np.ndarray, frames: int, time_info, status) -> None:
            if status:
                # Overflows etc. - log but keep going.
                self._emit_error(f"audio status: {status}")
            self._audio_queue.put(indata.copy())

        try:
            stream = sd.InputStream(
                device=self.device,
                samplerate=self.sample_rate,
                channels=self.channels,
                blocksize=self.blocksize,
                dtype=self.dtype,
                callback=callback,
            )
            stream.start()
        except Exception as e:
            self._is_running = False
            self._emit_lost(f"could not open microphone: {e}")
            return

        # A stop() that landed while the stream was opening already nulled
        # self._stream and won't see this one -- close it here so it can't leak.
        if not self._is_running:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
            return
        self._stream = stream

        last_block_at = time.monotonic()
        while self._is_running:
            try:
                block = self._audio_queue.get(timeout=0.2)
            except queue.Empty:
                # No audio for a while = the device most likely went away
                # (unplugged, driver reset). WASAPI often just stops calling the
                # callback rather than raising.
                if time.monotonic() - last_block_at > self._starve_timeout:
                    self._emit_lost("microphone stopped delivering audio")
                    break
                continue
            if block is None:
                break
            last_block_at = time.monotonic()
            try:
                self._process_block(block)
            except Exception as e:  # never let the loop die silently
                self._emit_error(f"audio processing error: {e}")

    def _process_block(self, block: np.ndarray) -> None:
        """Run one block through the VAD and grow/close the utterance buffer."""
        mono = self._to_mono(block)

        if self.on_level is not None:
            # RMS of the block, ~[0, 1] for float32 audio. Cheap; lets a UI
            # draw a live level meter without touching the audio path.
            try:
                self.on_level(float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0)
            except Exception:
                pass

        is_speech = bool(self.vad.process_block(mono)) if self.vad else True

        if is_speech:
            if not self._is_speaking:
                # Utterance onset - prepend the pre-roll so we don't clip
                # the start of the first word.
                self._speech_buffer.extend(self._preroll)
                self._is_speaking = True
            self._speech_buffer.append(mono)
            self._speech_blocks += 1
            self._silence_counter = 0
        elif self._is_speaking:
            # Keep trailing silence in the buffer so word endings survive.
            self._speech_buffer.append(mono)
            self._silence_counter += 1
            if self._silence_counter >= self._silence_threshold:
                self._flush_segment()

        self._preroll.append(mono)

    def _flush_segment(self) -> None:
        """Queue the current utterance for transcription, if it's big enough."""
        if self._speech_buffer and self._speech_blocks >= self._min_speech_blocks:
            audio = np.concatenate(self._speech_buffer).astype(np.float32, copy=False)
            self._segment_queue.put(audio)
        self._reset_buffer()

    def _reset_buffer(self) -> None:
        self._speech_buffer = []
        self._speech_blocks = 0
        self._is_speaking = False
        self._silence_counter = 0

    # ------------------------------------------------------------------
    # transcription thread
    # ------------------------------------------------------------------
    def _transcribe_loop(self, generation: Optional[int] = None) -> None:
        if generation is None:  # raw target= use (tests)
            generation = self._generation
        seg_q = self._segment_queue  # bind now -- start() swaps the attr later
        while True:
            audio = seg_q.get()
            if audio is None:
                break
            # A discarding stop bumped the generation / set _discard: keep
            # draining to the sentinel, but don't spend a whisper pass on it.
            if generation != self._generation or self._discard:
                continue
            if self.transcriber is None:
                continue
            try:
                text = self.transcriber.transcribe(audio, on_partial=self.on_partial) \
                    if self.on_partial is not None else self.transcriber.transcribe(audio)
            except TypeError:
                text = self.transcriber.transcribe(audio)
            except Exception as e:
                self._emit_error(f"transcription failed: {e}")
                continue
            # A stop that landed while we were decoding: the result is stale.
            if generation != self._generation or self._discard:
                continue
            if text and self.on_transcription:
                self.on_transcription(text)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _to_mono(block: np.ndarray) -> np.ndarray:
        """Collapse to a contiguous 1-D float32 mono array."""
        arr = np.asarray(block, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.mean(axis=1)
        return np.ascontiguousarray(arr, dtype=np.float32)

    def _emit_error(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)
        else:
            print(f"[ERROR] {message}")

    def _emit_lost(self, message: str) -> None:
        """Fire ``on_lost`` once per session for a fatal input failure."""
        if self._lost_fired:
            return
        self._lost_fired = True
        self._is_running = False
        # Close the dead stream here: stop() early-returns once _is_running is
        # False, so without this the InputStream (and its callback) would leak
        # for the rest of the session -- and an autofallback then opens a second.
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        cb = self.on_lost or self.on_error
        if cb:
            cb(message)
        else:
            print(f"[LOST] {message}")


class AudioDeviceManager:
    """Utility class for listing and selecting audio devices."""

    @staticmethod
    def list_input_devices() -> list:
        """List all input (microphone) devices."""
        devices = sd.query_devices()
        return [
            {
                "id": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": dev["default_samplerate"],
            }
            for i, dev in enumerate(devices)
            if dev["max_input_channels"] > 0
        ]

    @staticmethod
    def get_default_input() -> Optional[int]:
        """Get the default input device ID (None if there is no valid default)."""
        try:
            dev = sd.default.device[0]
        except Exception:
            return None
        return dev if isinstance(dev, int) and dev >= 0 else None

    @staticmethod
    def resolve_device(device_spec: Any) -> Optional[int]:
        """
        Resolve a device specification to a device ID.

        Args:
            device_spec: None (default), int (device ID), or str (name match).

        Returns:
            Device ID or None for the system default.
        """
        if device_spec is None or device_spec == "":
            return None
        if isinstance(device_spec, bool):  # guard: bool is a subclass of int
            return None
        if isinstance(device_spec, int):
            return device_spec
        if isinstance(device_spec, str):
            try:
                devices = sd.query_devices()
            except Exception:
                return None
            for i, dev in enumerate(devices):
                if dev["max_input_channels"] > 0 and device_spec.lower() in dev["name"].lower():
                    return i
            print(f"[WARN] Device '{device_spec}' not found, using default.")
            return None
        return None
