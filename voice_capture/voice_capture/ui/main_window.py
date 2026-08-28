"""
Main window (PySide6) for the Voice Capture application.
"""

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    """Main application window."""

    # Signals for communicating with audio/transcription threads
    start_recording = Signal()
    stop_recording = Signal()
    clear_text = Signal()
    copy_text = Signal()
    save_text = Signal()
    settings_changed = Signal(dict)
    # Emits the selected device id (int) or None for the system default.
    device_changed = Signal(object)

    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.config = config
        self._is_recording = False

        self.setWindowTitle("Voice Capture")
        self.resize(
            config.get("window_width", 800),
            config.get("window_height", 600),
        )

        self._setup_menu_bar()
        self._setup_central_widget()
        self._setup_status_bar()
        self._apply_font_size(config.get("font_size", 12))

        # Wire the text-management signals to their handlers.
        self.clear_text.connect(self._clear_output)
        self.copy_text.connect(self._copy_all)
        self.save_text.connect(self._save_transcription)

    def _setup_menu_bar(self) -> None:
        """Create the menu bar."""
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("&File")

        save_action = QAction("&Save Transcription...", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self.save_text.emit)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # Edit menu
        edit_menu = menu_bar.addMenu("&Edit")

        clear_action = QAction("&Clear Text", self)
        clear_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        clear_action.triggered.connect(self.clear_text.emit)
        edit_menu.addAction(clear_action)

        edit_menu.addSeparator()

        settings_action = QAction("&Preferences...", self)
        settings_action.setShortcut(QKeySequence.Preferences)
        settings_action.triggered.connect(self._show_settings)
        edit_menu.addAction(settings_action)

        # View menu
        view_menu = menu_bar.addMenu("&View")

        self.top_action = QAction("Stay on &Top", self)
        self.top_action.setCheckable(True)
        self.top_action.setChecked(self.config.get("stay_on_top", False))
        self.top_action.triggered.connect(self._toggle_always_on_top)
        view_menu.addAction(self.top_action)

    def _setup_central_widget(self) -> None:
        """Create the central widget with controls."""
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setSpacing(8)

        # --- Control bar ---
        controls = QHBoxLayout()

        self.record_btn = QPushButton("Start Recording")
        self.record_btn.setCheckable(True)
        self.record_btn.clicked.connect(self._toggle_recording)
        controls.addWidget(self.record_btn)

        self.status_label = QLabel("Ready")
        controls.addWidget(self.status_label)

        controls.addStretch()

        self.mic_combo = QComboBox()
        self.mic_combo.addItem("Default Microphone", None)
        self.mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        controls.addWidget(self.mic_combo)

        layout.addLayout(controls)

        # --- Transcription output ---
        self.text_output = QPlainTextEdit()
        self.text_output.setReadOnly(True)
        self.text_output.setPlaceholderText(
            "Transcription will appear here..."
        )
        layout.addWidget(self.text_output, stretch=1)

        # --- Bottom controls ---
        bottom = QHBoxLayout()

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_text.emit)
        bottom.addWidget(self.clear_btn)

        self.copy_btn = QPushButton("Copy All")
        self.copy_btn.clicked.connect(self.copy_text.emit)
        bottom.addWidget(self.copy_btn)

        bottom.addStretch()

        self.word_count_label = QLabel("Words: 0")
        bottom.addWidget(self.word_count_label)

        layout.addLayout(bottom)

    def _setup_status_bar(self) -> None:
        """Create the status bar."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Voice Capture ready")

    def _apply_font_size(self, size: int) -> None:
        """Apply font size to the text output."""
        font = self.text_output.font()
        font.setPointSize(size)
        self.text_output.setFont(font)

    def _toggle_recording(self) -> None:
        """Toggle recording state."""
        if self._is_recording:
            self.stop_recording.emit()
            self.record_btn.setText("Start Recording")
            self.record_btn.setChecked(False)
            self.status_label.setText("Stopped")
            self.status_bar.showMessage("Recording stopped")
            self._is_recording = False
        else:
            self.start_recording.emit()
            self.record_btn.setText("Stop Recording")
            self.record_btn.setChecked(True)
            self.status_label.setText("Recording...")
            self.status_bar.showMessage("Recording active")
            self._is_recording = True

    def _toggle_always_on_top(self, checked: bool) -> None:
        """Toggle window always-on-top."""
        flags = self.windowFlags()
        if checked:
            self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
        self.show()

    def _show_settings(self) -> None:
        """Show the settings dialog."""
        # TODO: Create a QDialog for settings
        pass

    @Slot(str)
    def append_text(self, text: str) -> None:
        """Append transcribed text to the output."""
        text = text.strip()
        if not text:
            return
        self.text_output.appendPlainText(text)
        # Update word count
        full_text = self.text_output.toPlainText()
        word_count = len(full_text.split())
        self.word_count_label.setText(f"Words: {word_count}")

        if self.config.get("auto_scroll", True):
            scrollbar = self.text_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    @Slot(str)
    def show_error(self, message: str) -> None:
        """Surface a non-fatal error without blocking the event loop."""
        self.status_label.setText("Error")
        self.status_bar.showMessage(message, 8000)
        print(f"[ERROR] {message}")

    def populate_devices(self, devices: list, selected: Optional[int]) -> None:
        """Fill the microphone combo box with available input devices."""
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        self.mic_combo.addItem("Default Microphone", None)
        for dev in devices:
            self.mic_combo.addItem(f"{dev['name']} (#{dev['id']})", dev["id"])
        if selected is not None:
            idx = self.mic_combo.findData(selected)
            if idx >= 0:
                self.mic_combo.setCurrentIndex(idx)
        self.mic_combo.blockSignals(False)

    def _on_mic_changed(self, _index: int) -> None:
        self.device_changed.emit(self.mic_combo.currentData())

    def _clear_output(self) -> None:
        self.text_output.clear()
        self.word_count_label.setText("Words: 0")

    def _copy_all(self) -> None:
        QApplication.clipboard().setText(self.text_output.toPlainText())
        self.status_bar.showMessage("Transcription copied to clipboard", 3000)

    def _save_transcription(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Transcription", "transcription.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text_output.toPlainText())
        except OSError as e:
            QMessageBox.warning(self, "Save Failed", str(e))
            return
        self.status_bar.showMessage(f"Saved to {path}", 4000)
