"""A one-line strip across the top of the panel during the last days of the
free trial: "Your free trial ends in N days." + Upgrade + dismiss.

Dumb view -- it emits :attr:`upgrade_requested` / :attr:`dismissed` and lets
``terminal_panel`` decide what to do (open the pricing page, remember the
dismissal for the day). Hidden unless ``terminal_panel._refresh_trial_banner``
shows it.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

import theme

__all__ = ["TrialBanner"]


class TrialBanner(QWidget):
    upgrade_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._days = 0
        self.setObjectName("trialBanner")

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 8, 6)
        row.setSpacing(10)

        self._label = QLabel("")
        self._label.setObjectName("trialText")
        row.addWidget(self._label, 1)

        self._upgrade = QPushButton("Upgrade to Pro")
        self._upgrade.setObjectName("trialUpgrade")
        self._upgrade.setCursor(Qt.PointingHandCursor)
        self._upgrade.clicked.connect(self.upgrade_requested)
        row.addWidget(self._upgrade, 0)

        self._close = QPushButton("✕")
        self._close.setObjectName("trialClose")
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setFixedWidth(24)
        self._close.setToolTip("Dismiss until tomorrow")
        self._close.clicked.connect(self.dismissed)
        row.addWidget(self._close, 0)

        self.apply_theme()

    def set_days_left(self, days: int) -> None:
        self._days = int(days)
        if self._days <= 0:
            self._label.setText("Your free trial ends today.")
        elif self._days == 1:
            self._label.setText("Your free trial ends tomorrow.")
        else:
            self._label.setText(f"Your free trial ends in {self._days} days.")

    def apply_theme(self) -> None:
        accent = theme.color("pro")
        self.setStyleSheet(
            f"""
            QWidget#trialBanner {{
                background: {theme.color("accent_soft_bg")};
                border-bottom: 1px solid {accent};
            }}
            QLabel#trialText {{ color: {theme.color("text")}; font-size: 12px; }}
            QPushButton#trialUpgrade {{
                background: {accent}; color: {theme.color("window_bg")};
                border: none; border-radius: 5px; padding: 4px 12px;
                font-size: 11px; font-weight: 700;
            }}
            QPushButton#trialUpgrade:hover {{ background: {theme.color("pro")}; }}
            QPushButton#trialClose {{
                background: transparent; border: none;
                color: {theme.color("text_muted")}; font-size: 12px;
            }}
            QPushButton#trialClose:hover {{ color: {theme.color("text")}; }}
            """
        )
