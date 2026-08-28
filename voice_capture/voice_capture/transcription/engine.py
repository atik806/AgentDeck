"""
Transcription engine using pywhispercpp.

Wraps the pywhispercpp Model class and provides:
  - Model loading with auto-download
  - Synchronous and streaming transcription
  - Callback-based real-time output
"""

import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

# whisper.cpp emits bracketed markers for non-speech audio, e.g. "[BLANK_AUDIO]",
# "(music)", "[ Silence ]". Drop segments that are nothing but such a marker.
_NON_SPEECH_RE = re.compile(r"^\s*[\[(][^\])]*[\])]\s*$")


class TranscriptionEngine:
    """
    Wraps pywhispercpp for audio transcription.

    Automatically downloads the model on first use (pywhispercpp handles this).
    Provides real-time segment callbacks and full transcription.
    """

    def __init__(
        self,
        model_size: str = "tiny.en",
        model_path: Optional[str] = None,
        language: Optional[str] = "en",
        n_threads: Optional[int] = None,
        print_realtime: bool = True,
        print_progress: bool = False,
        on_segment: Optional[Callable[[dict], None]] = None,
    ):
        """
        Initialize the transcription engine.

        Args:
            model_size: Whisper model size (e.g., "tiny.en", "base", "small").
            model_path: Path to a local GGML model file. Overrides model_size.
            language: Language code ("en" for English, None for auto-detect).
            n_threads: Number of CPU threads (None = auto).
            print_realtime: Print segments as they are transcribed.
            print_progress: Print progress information.
            on_segment: Callback for each transcribed segment (receives dict).
        """
        self.model_size = model_size
        self.model_path = model_path
        self.language = language
        self.n_threads = n_threads
        self.print_realtime = print_realtime
        self.print_progress = print_progress
        self.on_segment = on_segment

        self._model = None
        self._load_lock = threading.Lock()

    def ensure_loaded(self) -> None:
        """Load the model if it isn't already (idempotent, thread-safe)."""
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is None:
                self._load_model()

    def _load_model(self) -> None:
        """
        Load the whisper.cpp model.

        pywhispercpp auto-downloads the model to ~/.cache/pywhispercpp/
        if it doesn't exist locally. Loading is lazy (first ``transcribe``
        call) so a missing model / no network doesn't crash app startup.
        """
        from pywhispercpp.model import Model

        model_input = self.model_path if self.model_path else self.model_size
        print(f"[INFO]  Loading Whisper model: {model_input}")

        params = {
            "print_realtime": self.print_realtime,
            "print_progress": self.print_progress,
        }

        if self.language:
            params["language"] = self.language
        if self.n_threads:
            params["n_threads"] = self.n_threads

        self._model = Model(model_input, **params)
        print("[INFO]  Model loaded successfully.")

    def transcribe(self, audio: np.ndarray) -> str:
        """
        Transcribe an audio array.

        Args:
            audio: Audio data as numpy array (16 kHz, mono, float32).

        Returns:
            Transcribed text.
        """
        self.ensure_loaded()

        # whisper.cpp wants contiguous 1-D float32 mono @ 16 kHz.
        audio = np.ascontiguousarray(np.asarray(audio, dtype=np.float32).reshape(-1))
        if audio.size == 0:
            return ""

        segments = self._model.transcribe(audio)

        parts = []
        for segment in segments:
            piece = (segment.text or "").strip()
            if not piece or _NON_SPEECH_RE.match(piece):
                continue
            parts.append(piece)
            if self.on_segment:
                self.on_segment({
                    "text": segment.text,
                    "t0": segment.t0,
                    "t1": segment.t1,
                })

        return " ".join(parts).strip()

    def transcribe_file(self, file_path: str) -> str:
        """
        Transcribe an audio file.

        Args:
            file_path: Path to an audio file (WAV, MP3, etc.).

        Returns:
            Transcribed text.
        """
        segments = self._model.transcribe(file_path)
        return " ".join(seg.text for seg in segments)

    def get_model_info(self) -> dict:
        """Get information about the loaded model."""
        if self._model is None:
            return {"loaded": False}
        return {
            "loaded": True,
            "model_size": self.model_size,
            "model_path": self.model_path,
            "language": self.language,
        }

    def unload(self) -> None:
        """Unload the model to free memory."""
        self._model = None


# Utility to check available models in pywhispercpp cache
def list_cached_models() -> list:
    """List models cached in the pywhispercpp cache directory."""
    cache_dir = Path.home() / ".cache" / "pywhispercpp"
    if not cache_dir.exists():
        return []
    return sorted([
        f.stem.replace("ggml-", "")
        for f in cache_dir.glob("ggml-*.bin")
    ])
