"""The wall shown when the 7-day free trial has ended and there is no active
Pro plan.

Shown *instead of* the app at startup (``main.py``), and again if the trial
lapses while AgentDeck is running (``terminal_panel._recheck_trial``). Accepting
means "an active plan was found, let them in"; rejecting means "quit" -- exactly
like :class:`login_window.LoginWindow`, whose amber styling this mirrors.

It owns no billing or auth code: it opens the pricing page in the browser and
re-reads the profile via :class:`account.AccountController`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import entitlements
from login_window import _AMBER, _AMBER_HI, _BG, _DANGER, _MUTED, _TEXT
from version import __version__

__all__ = ["TrialGateDialog"]

_ASSET_ICON = Path(__file__).resolve().parent / "assets" / "icon.ico"


class TrialGateDialog(QDialog):
    """Upgrade-or-quit. ``Accepted`` only once ``account.access_allowed`` is True."""

    def __init__(
        self,
        account,
        config: Optional[dict] = None,
        icon: Optional[QIcon] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._account = account
        self._config = config or {}
        self._checking = False

        self.setWindowTitle("AgentDeck — trial ended")
        self.setModal(True)
        self.setFixedSize(460, 440)
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)

        self.setStyleSheet(
            f"""
            QDialog {{ background: {_BG}; }}
            QLabel {{ color: {_TEXT}; }}
            QLabel#h1 {{ font-size: 22px; font-weight: 700; }}
            QLabel#sub {{ color: {_MUTED}; font-size: 12px; }}
            QLabel#status {{ font-size: 11px; }}
            QLabel#foot {{ color: {_MUTED}; font-size: 10px; }}
            QPushButton#primary {{
                background: {_AMBER}; color: #1a1a1a; border: 1px solid {_AMBER};
                border-radius: 8px; padding: 11px 18px; font-size: 13px;
                font-weight: 700;
            }}
            QPushButton#primary:hover {{ background: {_AMBER_HI};
                                         border-color: {_AMBER_HI}; }}
            QPushButton#secondary {{
                background: transparent; color: {_TEXT}; border: 1px solid #444;
                border-radius: 8px; padding: 9px 16px; font-size: 12px;
            }}
            QPushButton#secondary:hover {{ border-color: {_AMBER}; color: {_AMBER}; }}
            QPushButton#secondary:disabled {{ color: #6a6a6a; border-color: #333; }}
            QPushButton#link {{ background: transparent; border: none;
                                color: {_MUTED}; padding: 7px 6px; font-size: 12px; }}
            QPushButton#link:hover {{ color: {_AMBER}; }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 30, 34, 22)
        outer.setSpacing(10)
        outer.addStretch(1)

        if _ASSET_ICON.exists():
            mark = QLabel()
            mark.setPixmap(QIcon(str(_ASSET_ICON)).pixmap(72, 72))
            mark.setAlignment(Qt.AlignCenter)
            outer.addWidget(mark)
            outer.addSpacing(8)

        title = QLabel("Your free trial has ended")
        title.setObjectName("h1")
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        outer.addWidget(title)

        sub = QLabel(
            f"The {entitlements.TRIAL_DAYS}-day AgentDeck trial is over. Upgrade to "
            "Pro to keep using it — your workspaces and settings are untouched."
        )
        sub.setObjectName("sub")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        outer.addWidget(sub)

        outer.addSpacing(14)

        self._primary = QPushButton("Upgrade to Pro")
        self._primary.setObjectName("primary")
        self._primary.setCursor(Qt.PointingHandCursor)
        self._primary.clicked.connect(self._on_upgrade)
        outer.addWidget(self._primary)

        self._recheck = QPushButton("I've upgraded — re-check")
        self._recheck.setObjectName("secondary")
        self._recheck.setCursor(Qt.PointingHandCursor)
        self._recheck.clicked.connect(self._on_recheck)
        outer.addWidget(self._recheck)

        self._link = QPushButton("Sign out & quit")
        self._link.setObjectName("link")
        self._link.setCursor(Qt.PointingHandCursor)
        self._link.clicked.connect(self._on_quit)
        outer.addWidget(self._link, 0, Qt.AlignHCenter)

        self._status = QLabel("")
        self._status.setObjectName("status")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        outer.addWidget(self._status)

        outer.addStretch(1)

        foot = QHBoxLayout()
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("foot")
        hint = QLabel("An active plan is required after the trial.")
        hint.setObjectName("foot")
        foot.addWidget(ver)
        foot.addStretch(1)
        foot.addWidget(hint)
        outer.addLayout(foot)

        account.profile_ready.connect(self._on_profile)
        account.signed_out.connect(self.reject)
        account.error.connect(self._on_error)

    # -- user actions --------------------------------------------------------

    def _on_upgrade(self) -> None:
        QDesktopServices.openUrl(QUrl(entitlements.UPGRADE_URL))
        self._status.setStyleSheet(f"color: {_MUTED};")
        self._status.setText(
            "Finish the upgrade in your browser, then click “re-check”."
        )
        self._status.setVisible(True)

    def _on_recheck(self) -> None:
        if self._checking:
            return
        self._checking = True
        self._recheck.setEnabled(False)
        self._recheck.setText("Checking…")
        self._status.setVisible(False)
        try:
            self._account.fetch_profile()
        except Exception:  # noqa: BLE001
            self._on_profile(None)

    def _on_quit(self) -> None:
        try:
            self._account.sign_out()  # signed_out -> self.reject()
        except Exception:  # noqa: BLE001
            self.reject()

    # -- account signals ---------------------------------------------------

    def _on_profile(self, _profile) -> None:
        if not self._checking:
            return
        self._checking = False
        self._recheck.setEnabled(True)
        self._recheck.setText("I've upgraded — re-check")
        if self._account.access_allowed:
            self.accept()
        else:
            self._status.setStyleSheet(f"color: {_DANGER};")
            self._status.setText(
                "Still no active plan on this account. It can take a minute to "
                "sync after payment."
            )
            self._status.setVisible(True)

    def _on_error(self, message: str) -> None:
        if not self._checking:
            return
        self._checking = False
        self._recheck.setEnabled(True)
        self._recheck.setText("I've upgraded — re-check")
        self._status.setStyleSheet(f"color: {_DANGER};")
        self._status.setText(message or "Couldn't reach the server. Try again.")
        self._status.setVisible(True)
