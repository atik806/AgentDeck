"""Shared UI: the "how to install this agent" panel.

Used by both the setup wizard (``setup_wizard.py``, amber accent) and the
in-app new-workspace dialog (``new_workspace_dialog.py``, blue accent) so an
agent that isn't on PATH is offered with clear, copyable install steps instead
of just being hidden.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from agents import agent_label, install_hint, is_installed, refresh_path

__all__ = ["InstallHint"]


class InstallHint(QWidget):
    """Install command (copyable) + docs link + note + a Re-check button.

    :attr:`rechecked` fires after Re-check with the fresh install state, so the
    owner can flip a card / re-enable a launch button without a restart.
    """

    rechecked = Signal(bool)

    def __init__(self, key: str, *, accent: str = "#e8833a",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._key = key
        hint = install_hint(key) or {}
        command = hint.get("command", "")
        self._docs = hint.get("docs", "")
        note = hint.get("note", "")
        label = agent_label(key)

        self.setStyleSheet(
            f"""
            QLabel {{ color: #9a9a9a; font-size: 10px; }}
            QLabel#hintHead {{ color: #c8c8c8; font-size: 11px; }}
            QLineEdit {{
                background: #141414; color: #c8c8c8;
                font-family: Consolas, 'Cascadia Mono', monospace;
                border: 1px solid #333333; border-radius: 6px;
                padding: 5px 8px; font-size: 10px;
            }}
            QPushButton {{
                background: #2a2a2a; color: #d8d8d8; border: 1px solid #3a3a3a;
                border-radius: 6px; padding: 4px 10px; font-size: 10px;
            }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton#hintGuide {{ background: transparent; color: {accent}; }}
            """
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)

        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        head = QLabel(f"Install {label}, then Re-check:")
        head.setObjectName("hintHead")
        head_row.addWidget(head)
        head_row.addStretch(1)
        if self._docs:
            guide = QPushButton("Open guide ↗")
            guide.setObjectName("hintGuide")
            guide.setCursor(Qt.PointingHandCursor)
            guide.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(self._docs)))
            head_row.addWidget(guide)
        lay.addLayout(head_row)

        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(6)
        self._cmd = QLineEdit(command)
        self._cmd.setReadOnly(True)
        self._cmd.setCursorPosition(0)
        self._copy = QPushButton("Copy")
        self._copy.setCursor(Qt.PointingHandCursor)
        self._copy.setFixedWidth(56)
        self._copy.clicked.connect(self._do_copy)
        cmd_row.addWidget(self._cmd, 1)
        cmd_row.addWidget(self._copy)
        lay.addLayout(cmd_row)

        if note:
            note_lbl = QLabel(note)
            note_lbl.setWordWrap(True)
            lay.addWidget(note_lbl)

        recheck_row = QHBoxLayout()
        recheck_row.setSpacing(8)
        self._recheck = QPushButton("Re-check")
        self._recheck.setCursor(Qt.PointingHandCursor)
        self._recheck.clicked.connect(self._do_recheck)
        self._status = QLabel("")
        recheck_row.addWidget(self._recheck)
        recheck_row.addWidget(self._status)
        recheck_row.addStretch(1)
        lay.addLayout(recheck_row)

    # -- actions -----------------------------------------------------------

    def _do_copy(self) -> None:
        QApplication.clipboard().setText(self._cmd.text())
        self._copy.setText("Copied")
        QTimer.singleShot(1300, lambda: self._copy.setText("Copy"))

    def _do_recheck(self) -> None:
        refresh_path()
        ok = is_installed(self._key)
        self._status.setText(
            "found — you're set" if ok
            else "still not on PATH (a restart may be needed)"
        )
        self._status.setStyleSheet(
            f"color: {'#5fb35f' if ok else '#c07070'}; font-size: 10px;"
        )
        self.rechecked.emit(ok)
