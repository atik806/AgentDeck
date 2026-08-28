"""
Windows Multi-Terminal Launcher - Main Window GUI.

PySide6-based GUI for launching multiple terminal windows.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSpinBox, QComboBox, QCheckBox,
    QGroupBox, QMessageBox
)
from PySide6.QtCore import Qt
from typing import Dict, Any, Optional

from launcher import detect_available_terminals, launch_terminals, LaunchResult


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.config = config
        self.last_result: Optional[LaunchResult] = None

        self.setWindowTitle("Multi-Terminal Launcher")
        self.resize(config.get("window_width", 800), config.get("window_height", 600))

        if config.get("start_maximized", False):
            self.showMaximized()

        self._setup_ui()

    def _setup_ui(self):
        """Setup the user interface."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Title
        title = QLabel("Windows Multi-Terminal Launcher")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Terminal selection
        terminal_group = QGroupBox("Terminal Settings")
        terminal_layout = QVBoxLayout()

        terminal_row = QHBoxLayout()
        terminal_row.addWidget(QLabel("Terminal:"))
        self.terminal_combo = QComboBox()
        self._populate_terminals()
        terminal_row.addWidget(self.terminal_combo)
        terminal_layout.addLayout(terminal_row)

        terminal_group.setLayout(terminal_layout)
        layout.addWidget(terminal_group)

        # Launch settings
        settings_group = QGroupBox("Launch Settings")
        settings_layout = QVBoxLayout()

        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("Number of terminals:"))
        self.count_spin = QSpinBox()
        self.count_spin.setMinimum(1)
        self.count_spin.setMaximum(16)
        self.count_spin.setValue(self.config.get("default_count", 4))
        count_row.addWidget(self.count_spin)
        count_row.addStretch()
        settings_layout.addLayout(count_row)

        self.tile_checkbox = QCheckBox("Auto-tile windows")
        self.tile_checkbox.setChecked(self.config.get("auto_tile", True))
        settings_layout.addWidget(self.tile_checkbox)

        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # Launch button
        self.launch_btn = QPushButton("Launch Terminals")
        self.launch_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 16px;
                font-weight: bold;
                padding: 12px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)
        self.launch_btn.clicked.connect(self._on_launch)
        layout.addWidget(self.launch_btn)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 14px; color: #666;")
        layout.addWidget(self.status_label)

        layout.addStretch()

        # Footer
        footer = QLabel("Windows-native terminal launcher using PySide6")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("font-size: 11px; color: #999;")
        layout.addWidget(footer)

    def _populate_terminals(self):
        """Populate terminal dropdown with detected terminals."""
        terminals = detect_available_terminals()

        if not terminals:
            self.terminal_combo.addItem("No terminals detected", None)
            self.launch_btn.setEnabled(False)
            return

        default_terminal = self.config.get("default_terminal", "Windows Terminal")
        default_index = 0

        for i, (name, path) in enumerate(terminals):
            self.terminal_combo.addItem(name, str(path))
            if default_terminal.lower() in name.lower():
                default_index = i

        self.terminal_combo.setCurrentIndex(default_index)

    def _on_launch(self):
        """Handle launch button click."""
        count = self.count_spin.value()
        terminal_name = self.terminal_combo.currentText()
        terminal_path = self.terminal_combo.currentData()
        auto_tile = self.tile_checkbox.isChecked()

        if terminal_path is None:
            QMessageBox.warning(
                self,
                "No Terminal",
                "No terminal emulator detected on your system."
            )
            return

        self.status_label.setText(f"Launching {count} terminals...")
        self.launch_btn.setEnabled(False)

        # Launch terminals
        from pathlib import Path
        result = launch_terminals(
            count=count,
            terminal_name=terminal_name,
            terminal_path=Path(terminal_path),
            auto_tile=auto_tile,
            padding=self.config.get("window_padding", 10),
            margin=self.config.get("margin", 50),
        )

        self.last_result = result

        # Update status
        if result.success:
            self.status_label.setText(
                f"✓ Successfully launched {result.count} terminal(s)"
            )
            self.status_label.setStyleSheet("font-size: 14px; color: #4CAF50;")
        else:
            self.status_label.setText(f"✗ Error: {result.error}")
            self.status_label.setStyleSheet("font-size: 14px; color: #f44336;")
            QMessageBox.critical(
                self,
                "Launch Failed",
                f"Failed to launch terminals:\n{result.error}"
            )

        self.launch_btn.setEnabled(True)
