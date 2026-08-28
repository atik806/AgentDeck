"""
Application skeleton - VoiceCaptureApp.

This is the main application class that ties together the GUI,
audio capture, VAD, and transcription engine.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from PySide6.QtCore import QObject, Signal

#: App mark, shipped at <project>/assets/ (see that folder).
_ICON = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"


def _load_icon() -> QIcon:
    """The window / taskbar icon, or an empty QIcon if the file is missing."""
    return QIcon(str(_ICON)) if _ICON.exists() else QIcon()


def _set_app_user_model_id() -> None:
    """Give Windows an explicit AppUserModelID so the taskbar shows our icon
    (and not the interpreter's) for a windowed Python process. No-op elsewhere.
    """
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "voice-capture.app"
        )
    except Exception:
        pass


class _SignalBridge(QObject):
    """
    Marshals callbacks from the audio / transcription worker threads onto
    the GUI thread. Qt delivers a signal emitted from another thread via a
    queued connection, so the connected slots run on the GUI thread.
    """

    transcription = Signal(str)
    error = Signal(str)


class VoiceCaptureApp:
    """
    Main application class for Voice Capture.

    Wires together:
      - PySide6 GUI (MainWindow)
      - sounddevice audio capture (AudioCapture)
      - webrtcvad voice activity detection (VADProcessor)
      - pywhispercpp transcription (TranscriptionEngine)
      - Configuration management
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config

        # Qt Application (created early for proper event loop setup)
        _set_app_user_model_id()
        self.qt_app = QApplication(sys.argv)
        self.qt_app.setApplicationName("Voice Capture")
        self.qt_app.setApplicationDisplayName("Voice Capture")
        self.qt_app.setOrganizationName("voice-capture")
        self.qt_app.setWindowIcon(_load_icon())
        self.qt_app.setQuitOnLastWindowClosed(True)

        # Set application style
        self._apply_theme(config.get("theme", "system"))

        # Components (initialized in setup)
        self.main_window = None
        self.audio_capture = None
        self.vad = None
        self.transcriber = None
        self._bridge = _SignalBridge()

    def _apply_theme(self, theme: str) -> None:
        """Apply the UI theme."""
        if theme == "dark":
            self.qt_app.setStyle("Fusion")
            # A full dark palette would be applied here
        elif theme == "light":
            self.qt_app.setStyle("Fusion")
        # "system" uses the desktop default

    def setup(self) -> None:
        """Initialize all components."""
        from voice_capture.ui.main_window import MainWindow
        from voice_capture.audio.capture import AudioCapture, AudioDeviceManager
        from voice_capture.vad.processor import VADProcessor
        from voice_capture.transcription.engine import TranscriptionEngine

        # Create main window
        self.main_window = MainWindow(self.config)

        # Populate the microphone picker with real devices.
        try:
            devices = AudioDeviceManager.list_input_devices()
        except Exception as e:  # pragma: no cover - depends on host audio
            devices = []
            self.main_window.show_error(f"Could not list audio devices: {e}")
        resolved = AudioDeviceManager.resolve_device(
            self.config.get("microphone_device")
        )
        self.main_window.populate_devices(devices, resolved)

        # Create VAD
        self.vad = VADProcessor(
            backend=self.config.get("vad_backend", "webrtc"),
            aggressiveness=self.config.get("vad_aggressiveness", 2),
            sample_rate=self.config.get("sample_rate", 16000),
            threshold=self.config.get("vad_threshold", 0.5),
        )

        # Create transcription engine (model loads lazily on first segment)
        self.transcriber = TranscriptionEngine(
            model_size=self.config.get("model_size", "tiny.en"),
            model_path=self.config.get("model_path"),
            language=self.config.get("language", "en"),
            n_threads=self.config.get("n_threads"),
            print_realtime=self.config.get("print_realtime", True),
            print_progress=self.config.get("print_progress", False),
        )

        # Create audio capture (which feeds into VAD -> transcriber)
        self.audio_capture = AudioCapture(
            device=resolved,
            sample_rate=self.config.get("sample_rate", 16000),
            channels=self.config.get("channels", 1),
            blocksize=self.config.get("blocksize", 480),
            dtype=self.config.get("dtype", "float32"),
            vad=self.vad,
            transcriber=self.transcriber,
            on_transcription=self._bridge.transcription.emit,
            on_error=self._bridge.error.emit,
            silence_blocks=self.config.get("silence_blocks", 10),
        )

        # Worker-thread callbacks -> GUI thread (queued signal connections)
        self._bridge.transcription.connect(self.main_window.append_text)
        self._bridge.error.connect(self.main_window.show_error)

        # GUI -> audio pipeline
        self.main_window.start_recording.connect(self.audio_capture.start)
        self.main_window.stop_recording.connect(self.audio_capture.stop)
        self.main_window.device_changed.connect(self._on_device_changed)

        # Make sure a live stream is torn down cleanly on quit.
        self.qt_app.aboutToQuit.connect(self.audio_capture.stop)

    def _on_device_changed(self, device_id: Optional[int]) -> None:
        """Switch input device. Takes effect on the next Start Recording."""
        if self.audio_capture is None:
            return
        if self.audio_capture.is_running:
            self.main_window.show_error(
                "Stop recording before switching microphone."
            )
            return
        self.audio_capture.device = device_id

    def run(self) -> int:
        """Run the application."""
        self.setup()
        self.main_window.setWindowIcon(_load_icon())
        self.main_window.show()
        return self.qt_app.exec()


def run_app() -> None:
    """Convenience entry point for console_scripts."""
    from voice_capture.config import load_config
    config = load_config()
    app = VoiceCaptureApp(config)
    sys.exit(app.run())


if __name__ == "__main__":
    run_app()
