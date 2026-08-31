"""A small modal dialog that gives the self-update some visible motion.

Before this the only feedback while a release downloaded / installed was a
transient status-bar line ("Downloading update… 42%"). ``terminal_panel`` now
also drives one of these:

    dlg = UpdateProgressDialog(version, self)
    dlg.show()
    ...
    dlg.set_progress(pct)      # during download, 0..100
    dlg.start_installing()     # once downloaded, just before apply_and_restart
    dlg.finish()               # download failed, or the user declined to restart

It is deliberately dumb -- no updater references, no signals of its own. The
"animation" is two layers that keep the dialog from ever looking frozen:

* the progress bar's ``::chunk`` sweeps (indeterminate range) while installing, and
* a looping opacity pulse on the glyph the whole time.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsOpacityEffect,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

import theme

__all__ = ["UpdateProgressDialog"]


class UpdateProgressDialog(QDialog):
    """Modal "downloading / installing" progress, with a pulsing glyph."""

    def __init__(self, version: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._version = (version or "").strip()
        self._installing = False
        self.setObjectName("updateProgress")
        self.setWindowTitle("Updating AgentDeck")
        self.setModal(True)
        # The flow owns this window -- no close box, no "?" help button.
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setMinimumWidth(360)

        col = QVBoxLayout(self)
        col.setContentsMargins(26, 22, 26, 22)
        col.setSpacing(13)

        self._icon = QLabel("⬇")            # ⬇
        self._icon.setObjectName("updateIcon")
        self._icon.setAlignment(Qt.AlignCenter)
        col.addWidget(self._icon)

        self._title = QLabel("Downloading update")
        self._title.setObjectName("updateTitle")
        self._title.setAlignment(Qt.AlignCenter)
        col.addWidget(self._title)

        self._bar = QProgressBar()
        self._bar.setObjectName("updateBar")
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        col.addWidget(self._bar)

        self._detail = QLabel("Starting…")
        self._detail.setObjectName("updateDetail")
        self._detail.setAlignment(Qt.AlignCenter)
        col.addWidget(self._detail)

        # A gentle opacity pulse on the glyph so the dialog never looks frozen,
        # even while the percentage sits still between progress callbacks.
        self._fx = QGraphicsOpacityEffect(self._icon)
        self._icon.setGraphicsEffect(self._fx)
        self._pulse = QPropertyAnimation(self._fx, b"opacity", self)
        self._pulse.setDuration(1100)
        self._pulse.setKeyValueAt(0.0, 1.0)
        self._pulse.setKeyValueAt(0.5, 0.35)
        self._pulse.setKeyValueAt(1.0, 1.0)
        self._pulse.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse.setLoopCount(-1)
        self._pulse.start()

        self._apply_theme()

    # -- public API ---------------------------------------------------------

    def set_progress(self, pct: int) -> None:
        """Advance the determinate bar (0..100). Ignored once installing."""
        if self._installing:
            return
        pct = max(0, min(100, int(pct)))
        if self._bar.maximum() == 0:             # coming back from indeterminate
            self._bar.setRange(0, 100)
        self._bar.setValue(pct)
        prefix = f"AgentDeck {self._version}  ·  " if self._version else ""
        self._detail.setText(f"{prefix}{pct}%")

    def start_installing(self) -> None:
        """Switch to the indeterminate "installing, about to restart" state."""
        self._installing = True
        self._icon.setText("⚙")            # ⚙
        self._title.setText("Installing update")
        self._detail.setText("AgentDeck will restart in a moment…")
        self._bar.setRange(0, 0)                # sweeping chunk
        self._pulse.setDuration(750)           # a touch more urgent

    def finish(self) -> None:
        """Stop the animation and close the dialog."""
        self._pulse.stop()
        self.close()

    # -- internals ---------------------------------------------------------

    def closeEvent(self, event):               # noqa: D102 - Qt override
        self._pulse.stop()
        super().closeEvent(event)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QDialog#updateProgress {{ background: {theme.color("window_bg")}; }}
            QLabel#updateIcon {{ color: {theme.color("accent")}; font-size: 30px; }}
            QLabel#updateTitle {{
                color: {theme.color("text")}; font-size: 14px; font-weight: 700;
            }}
            QLabel#updateDetail {{
                color: {theme.color("text_muted")}; font-size: 11px;
            }}
            QProgressBar#updateBar {{
                background: {theme.color("surface")};
                border: none; border-radius: 4px;
            }}
            QProgressBar#updateBar::chunk {{
                background: {theme.color("accent")}; border-radius: 4px;
            }}
            """
        )
