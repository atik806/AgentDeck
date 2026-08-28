"""
Voice Activity Detection (VAD) processor.

Supports two backends:
  - webrtcvad (default): lightweight, fast, Google WebRTC VAD.
  - silero-vad: more accurate, requires PyTorch (heavier dependency).
"""

from typing import Optional

import numpy as np


class VADProcessor:
    """
    Voice Activity Detection processor.

    Wraps the chosen VAD backend and provides a unified interface
    for processing audio blocks and determining if speech is present.
    """

    def __init__(
        self,
        backend: str = "webrtc",
        aggressiveness: int = 2,
        sample_rate: int = 16000,
        threshold: float = 0.5,
    ):
        """
        Initialize VAD processor.

        Args:
            backend: "webrtc" or "silero"
            aggressiveness: WebRTC VAD mode (0-3). Higher = more aggressive
                            filtering of non-speech.
            sample_rate: Audio sample rate (must be 8000, 16000, 32000, or 48000
                        for webrtcvad).
            threshold: Silero VAD confidence threshold (0.0 - 1.0).
        """
        self.backend = backend
        self.aggressiveness = aggressiveness
        self.sample_rate = sample_rate
        self.threshold = threshold

        self._vad = None
        self._setup()

    def _setup(self) -> None:
        """Initialize the VAD backend."""
        if self.backend == "webrtc":
            self._setup_webrtc()
        elif self.backend == "silero":
            self._setup_silero()
        else:
            raise ValueError(f"Unknown VAD backend: {self.backend}")

    def _setup_webrtc(self) -> None:
        """Initialize WebRTC VAD."""
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(self.aggressiveness)
        except ImportError:
            raise ImportError(
                "webrtcvad not installed. Install with: "
                "pip install webrtcvad-wheels"
            )

    def _setup_silero(self) -> None:
        """Initialize Silero VAD."""
        try:
            import torch
            # Load the Silero VAD model via torch.hub or the silero-vad package
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
            )
            (get_speech_timestamps, _, read_audio, _, _) = utils
            self._vad = model
            self._silero_utils = {
                "get_speech_timestamps": get_speech_timestamps,
            }
        except ImportError:
            raise ImportError(
                "Silero VAD requires torch. Install with: "
                "pip install torch torchaudio silero-vad"
            )

    def process_block(self, block: np.ndarray) -> bool:
        """
        Process an audio block and determine if it contains speech.

        Args:
            block: Audio data as numpy array (float32, -1 to 1 range).

        Returns:
            True if speech is detected, False otherwise.
        """
        if self.backend == "webrtc":
            return self._process_webrtc(block)
        elif self.backend == "silero":
            return self._process_silero(block)
        return False

    # 10 / 20 / 30 ms frame lengths webrtcvad accepts, per sample rate.
    _WEBRTC_FRAMES = {
        8000: (80, 160, 240),
        16000: (160, 320, 480),
        32000: (320, 640, 960),
        48000: (480, 960, 1440),
    }

    def _process_webrtc(self, block: np.ndarray) -> bool:
        """
        Process a block with WebRTC VAD.

        WebRTC VAD expects 16-bit PCM mono audio in 10/20/30 ms frames.
        A block larger than 30 ms is split into valid frames and counts as
        speech if any sub-frame is speech; a block that can't be split into
        a valid frame length is treated as non-speech.
        """
        valid = self._WEBRTC_FRAMES.get(self.sample_rate)
        if valid is None:
            raise ValueError(
                f"webrtcvad does not support {self.sample_rate} Hz "
                f"(use 8000, 16000, 32000, or 48000)."
            )

        # float32 [-1, 1] -> clipped int16 PCM
        clipped = np.clip(np.asarray(block, dtype=np.float32).reshape(-1), -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)

        frame_len = max(f for f in valid if f <= len(pcm)) if pcm.size >= valid[0] else 0
        if frame_len == 0:
            return False

        for start in range(0, len(pcm) - frame_len + 1, frame_len):
            frame = pcm[start:start + frame_len]
            if self._vad.is_speech(frame.tobytes(), self.sample_rate):
                return True
        return False

    def _process_silero(self, block: np.ndarray) -> bool:
        """
        Process a block with Silero VAD.

        Silero works directly with float32 audio.
        """
        import torch

        # Silero v5 wants a fixed window: 512 samples @ 16 kHz, 256 @ 8 kHz.
        window = 512 if self.sample_rate >= 16000 else 256
        samples = np.asarray(block, dtype=np.float32).reshape(-1)
        if len(samples) < window:
            samples = np.pad(samples, (0, window - len(samples)))
        else:
            samples = samples[:window]

        tensor = torch.from_numpy(np.ascontiguousarray(samples)).float()
        with torch.no_grad():
            speech_prob = self._vad(tensor, self.sample_rate).item()

        return speech_prob >= self.threshold

    def reset(self) -> None:
        """Reset the VAD state (for Silero VAD which maintains state)."""
        if self.backend == "silero" and hasattr(self._vad, "reset_states"):
            self._vad.reset_states()
