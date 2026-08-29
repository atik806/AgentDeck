"""The sign-in front door -- shown once, before the setup wizard.

AgentDeck works fully offline; an account only adds cloud sync of your
workspaces / agents and the profile chip in the toolbar. So this dialog always
offers a way straight past it ("Continue without an account"), and closing it
outright (the [X]) is treated by ``main.py`` as "quit".

It owns no auth code: it drives an :class:`account.AccountController` and
reflects the signals it emits back (``busy_changed`` / ``signed_in`` /
``error``). The amber accent matches ``setup_wizard.py`` -- both read as the
same "front door".
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from version import __version__

__all__ = ["LoginWindow"]

_AMBER = "#e8833a"
_AMBER_HI = "#f0954e"
_BG = "#171717"
_BORDER = "#333333"
_TEXT = "#e8e8e8"
_MUTED = "#8a8a8a"
_DANGER = "#e0666b"

#: The AgentDeck mark, shipped beside this file (see assets/).
_ASSET_ICON = Path(__file__).resolve().parent / "assets" / "icon.ico"


class LoginWindow(QDialog):
    """Choose "Continue with Google" or "Continue without an account"."""

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
        #: "signed-in" | "offline" once the dialog is accepted; "" while open.
        self._mode = ""

        self.setWindowTitle("AgentDeck — sign in")
        self.setModal(True)
        self.setFixedSize(460, 430)
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
            QPushButton#primary:disabled {{ background: #4a3a2c; color: #9b8a78;
                                            border-color: #4a3a2c; }}
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

        title = QLabel("AgentDeck")
        title.setObjectName("h1")
        title.setAlignment(Qt.AlignCenter)
        outer.addWidget(title)

        sub = QLabel("Sign in to sync your workspaces and agents across machines.")
        sub.setObjectName("sub")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        outer.addWidget(sub)

        outer.addSpacing(14)

        self._primary = QPushButton("Continue with Google")
        self._primary.setObjectName("primary")
        self._primary.setCursor(Qt.PointingHandCursor)
        self._primary.clicked.connect(self._on_primary)
        outer.addWidget(self._primary)

        self._link = QPushButton("Continue without an account")
        self._link.setObjectName("link")
        self._link.setCursor(Qt.PointingHandCursor)
        self._link.clicked.connect(self._on_link)
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
        hint = QLabel("You can sign in later from the ⚙ menu.")
        hint.setObjectName("foot")
        foot.addWidget(ver)
        foot.addStretch(1)
        foot.addWidget(hint)
        outer.addLayout(foot)

        # -- account wiring --------------------------------------------------
        account.busy_changed.connect(self._on_busy)
        account.signed_in.connect(self._on_signed_in)
        account.error.connect(self._on_error)

    # -- state -----------------------------------------------------------------

    def result_mode(self) -> str:
        """``"signed-in"`` or ``"offline"`` -- valid only after the dialog accepts."""
        return self._mode

    @property
    def _waiting(self) -> bool:
        return self._primary.text().startswith("Waiting")

    def _set_idle(self) -> None:
        self._primary.setEnabled(True)
        self._primary.setText("Continue with Google")
        self._link.setText("Continue without an account")

    def _set_waiting(self) -> None:
        self._primary.setEnabled(False)
        self._primary.setText("Waiting for your browser…")
        self._link.setText("Cancel")
        self._status.setVisible(False)

    # -- user actions --------------------------------------------------------

    def _on_primary(self) -> None:
        self._status.setVisible(False)
        self._set_waiting()
        self._account.sign_in_with_google()

    def _on_link(self) -> None:
        if self._waiting:
            self._account.cancel_sign_in()
            self._set_idle()
            return
        self._mode = "offline"
        self.accept()

    # -- account signals ---------------------------------------------------

    def _on_busy(self, busy: bool) -> None:
        if busy:
            self._set_waiting()
        elif self._mode == "":
            self._set_idle()

    def _on_signed_in(self, _user: dict) -> None:
        self._mode = "signed-in"
        self.accept()

    def _on_error(self, message: str) -> None:
        self._set_idle()
        self._status.setStyleSheet(f"color: {_DANGER};")
        self._status.setText(message or "Sign-in failed. Please try again.")
        self._status.setVisible(True)
