"""The account / profile panel, opened from the toolbar's ⚙ chip.

Mirrors the profile widget in the product mockup: a circular avatar, the
display name, the e-mail and a plan badge -- plus the two controls that only
make sense here (cloud-sync opt-in, Sign out). Signed out, it collapses to a
short pitch and a single "Continue with Google" button.

Blue accent, matching ``new_workspace_dialog.py`` -- this is an in-app action,
not the front door. It drives an :class:`account.AccountController` and never
touches auth itself.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:  # the shared helper lives in navbar.py; fall back to a local copy
    from navbar import circular_avatar as _circular_avatar
except Exception:  # noqa: BLE001 - navbar may not be importable in isolation
    _circular_avatar = None

from config import save_config

__all__ = ["AccountDialog"]

import theme

# Colour tokens, resolved against the active light/dark theme every read (this
# dialog is modal and rebuilt each time it opens, so that is always current).
def _BG() -> str: return theme.color("card_bg")
def _CARD() -> str: return theme.color("card_raised")
def _BORDER() -> str: return theme.color("card_border")
def _TEXT() -> str: return theme.color("dialog_text")
def _MUTED() -> str: return theme.color("text_muted")
def _BLUE() -> str: return theme.color("accent")
def _BLUE_HI() -> str: return theme.color("accent_hover")
def _GOLD() -> str: return theme.color("pro")
def _DANGER() -> str: return theme.color("danger")

_AVATAR_PX = 44


def _fallback_avatar(text: str, size: int, accent: str) -> QPixmap:
    """A filled circle with one initial -- used until the real picture loads."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(accent))
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor("#ffffff"))
    f = QFont("Segoe UI")
    f.setPixelSize(int(size * 0.44))
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, (text[:1] or "?").upper())
    p.end()
    return pm


def _avatar_pixmap(data: Optional[bytes], size: int, fallback_text: str,
                   accent: Optional[str] = None) -> QPixmap:
    accent = accent or _BLUE()
    if _circular_avatar is not None:
        return _circular_avatar(data, size, fallback_text, accent)
    if not data:
        return _fallback_avatar(fallback_text, size, accent)
    src = QPixmap()
    if not src.loadFromData(data):
        return _fallback_avatar(fallback_text, size, accent)
    src = src.scaled(size, size, Qt.KeepAspectRatioByExpanding,
                     Qt.SmoothTransformation)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(clip)
    p.drawPixmap(0, 0, src)
    p.setClipping(False)
    p.setPen(QColor(0, 0, 0, 40))
    p.setBrush(QBrush(Qt.NoBrush))
    p.drawEllipse(QRectF(0.5, 0.5, size - 1, size - 1))
    p.end()
    return pm


class AccountDialog(QDialog):
    """Profile + sign-out, or a sign-in pitch when signed out."""

    def __init__(
        self,
        account,
        config: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._account = account
        self._config = config if config is not None else {}

        self.setWindowTitle("Account")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setStyleSheet(
            f"""
            QDialog {{ background: {_BG()}; }}
            QLabel {{ color: {_TEXT()}; }}
            QLabel#name {{ font-size: 15px; font-weight: 700; }}
            QLabel#email {{ color: {_MUTED()}; font-size: 11px; }}
            QLabel#pitch {{ color: {_MUTED()}; font-size: 12px; }}
            QLabel#badge {{
                font-size: 9px; font-weight: 800; border-radius: 6px;
                padding: 2px 7px;
            }}
            QCheckBox {{ color: {_TEXT()}; font-size: 12px; spacing: 8px; }}
            QCheckBox::indicator {{ width: 15px; height: 15px; }}
            QPushButton {{
                background: {_CARD()}; color: {_TEXT()};
                border: 1px solid {_BORDER()}; border-radius: 7px;
                padding: 8px 16px; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {_BLUE()}; }}
            QPushButton#primary {{
                background: {_BLUE()}; color: {theme.color('on_accent')}; border-color: {_BLUE()};
                font-weight: 700;
            }}
            QPushButton#primary:hover {{ background: {_BLUE_HI()};
                                         border-color: {_BLUE_HI()}; }}
            QPushButton#danger {{ color: {_DANGER()}; }}
            QPushButton#danger:hover {{ border-color: {_DANGER()};
                                        background: {theme.color('accent_soft_bg')}; }}
            QPushButton#link {{ background: transparent; border: none;
                                color: {_MUTED()}; padding: 6px 2px; font-size: 11px; }}
            QPushButton#link:hover {{ color: {_BLUE()}; }}
            """
        )

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(22, 20, 22, 18)
        self._outer.setSpacing(12)

        if account.is_signed_in:
            self._build_signed_in()
        else:
            self._build_signed_out()

        # React to state changes while the dialog is open.
        account.signed_in.connect(self._on_signed_in)
        account.signed_out.connect(self.accept)
        account.avatar_ready.connect(self._on_avatar)
        account.profile_ready.connect(self._on_profile)

    # -- signed-in layout ---------------------------------------------------

    def _build_signed_in(self) -> None:
        acc = self._account

        head = QHBoxLayout()
        head.setSpacing(14)

        self._avatar = QLabel()
        self._avatar.setFixedSize(_AVATAR_PX, _AVATAR_PX)
        self._avatar.setPixmap(
            _avatar_pixmap(None, _AVATAR_PX, acc.display_name)
        )
        head.addWidget(self._avatar)

        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel(acc.display_name or "Signed in")
        name.setObjectName("name")
        email = QLabel(acc.email or "")
        email.setObjectName("email")
        col.addWidget(name)
        col.addWidget(email)
        self._plan_note = QLabel("")
        self._plan_note.setObjectName("email")
        self._plan_note.setWordWrap(True)
        col.addWidget(self._plan_note)
        head.addLayout(col, 1)

        self._badge = QLabel()
        self._badge.setObjectName("badge")
        self._badge.setAlignment(Qt.AlignCenter)
        head.addWidget(self._badge, 0, Qt.AlignTop)
        self._apply_plan(acc.plan)

        self._outer.addLayout(head)

        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {_BORDER()};")
        self._outer.addWidget(line)

        self._sync = QCheckBox("Sync my workspaces and agents to this account")
        self._sync.setChecked(bool(self._config.get("account_cloud_sync", True)))
        self._sync.toggled.connect(self._on_sync_toggled)
        self._outer.addWidget(self._sync)
        self._apply_plan(acc.plan)  # also gates the sync row now that it exists

        docs = QPushButton("Account help & privacy")
        docs.setObjectName("link")
        docs.setCursor(Qt.PointingHandCursor)
        docs.clicked.connect(self._open_docs)
        self._outer.addWidget(docs, 0, Qt.AlignLeft)

        self._outer.addSpacing(4)
        row = QHBoxLayout()
        signout = QPushButton("Sign out")
        signout.setObjectName("danger")
        signout.setCursor(Qt.PointingHandCursor)
        signout.clicked.connect(self._on_sign_out)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(signout)
        row.addStretch(1)
        row.addWidget(close)
        self._outer.addLayout(row)

        # Kick off the network refreshes; the dialog updates as they land.
        try:
            acc.fetch_avatar()
            acc.fetch_profile()
        except Exception:  # noqa: BLE001 - a profile refresh must not break the dialog
            pass

    def _apply_plan(self, plan: str) -> None:
        if not hasattr(self, "_badge"):
            return
        try:
            import entitlements
            pro = entitlements.is_pro(plan)
        except Exception:  # noqa: BLE001
            pro = str(plan or "").lower() == "pro"
        self._badge.setText("PRO" if pro else "FREE")
        if pro:
            self._badge.setStyleSheet(
                f"QLabel#badge {{ color: {theme.color('window_bg')}; background: {_GOLD()}; }}"
            )
        else:
            self._badge.setStyleSheet(
                f"QLabel#badge {{ color: {_MUTED()}; background: {_CARD()}; }}"
            )

        self._apply_plan_note(pro)

        # Cloud settings sync is a Pro feature -- show the control either way, but
        # a Free user can't arm it.
        sync = getattr(self, "_sync", None)
        if sync is not None:
            sync.setEnabled(pro)
            sync.setText(
                "Sync my workspaces and agents to this account"
                if pro else
                "Sync my workspaces and agents to this account  (Pro)"
            )

    def _apply_plan_note(self, pro: bool) -> None:
        """One muted line under the email: when Pro renews, or that it lapsed."""
        note = getattr(self, "_plan_note", None)
        if note is None:
            return
        acc = self._account
        raw = str(getattr(acc, "raw_plan", getattr(acc, "plan", "free")) or "free")
        try:
            import entitlements
            exp = entitlements.plan_expiry(getattr(acc, "plan_expires_at", None))
        except Exception:  # noqa: BLE001
            exp = None

        text = ""
        if exp is not None:
            when = exp.astimezone().strftime("%d %b %Y")
            if pro:
                text = f"Pro renews {when}"
            elif entitlements.is_pro(raw):
                text = f"Pro expired {when} — renew to restore Pro features"

        if not text and not pro:
            left = getattr(acc, "trial_days_left", None)
            if isinstance(left, int):
                if left >= 0:
                    text = f"Free trial — {max(0, left)} day(s) left"
                else:
                    text = "Trial ended — upgrade to keep using AgentDeck"
        note.setText(text)
        note.setVisible(bool(text))

    # -- signed-out layout ------------------------------------------------

    def _build_signed_out(self) -> None:
        title = QLabel("You're not signed in")
        title.setObjectName("name")
        self._outer.addWidget(title)

        pitch = QLabel(
            "Sign in with Google to sync your working folder, recent folders "
            "and agent choices across every machine you run AgentDeck on."
        )
        pitch.setObjectName("pitch")
        pitch.setWordWrap(True)
        self._outer.addWidget(pitch)

        self._outer.addSpacing(6)
        row = QHBoxLayout()
        google = QPushButton("Continue with Google")
        google.setObjectName("primary")
        google.setCursor(Qt.PointingHandCursor)
        google.clicked.connect(self._on_google)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(google)
        row.addStretch(1)
        row.addWidget(close)
        self._outer.addLayout(row)

    # -- actions ---------------------------------------------------------

    def _on_sync_toggled(self, on: bool) -> None:
        self._config["account_cloud_sync"] = bool(on)
        try:
            save_config(self._config)
        except (OSError, ValueError):
            pass

    def _on_sign_out(self) -> None:
        try:
            self._account.sign_out()
        except Exception:  # noqa: BLE001
            pass
        self.accept()

    def _on_google(self) -> None:
        try:
            self._account.sign_in_with_google()
        except Exception:  # noqa: BLE001
            pass

    def _open_docs(self) -> None:
        QDesktopServices.openUrl(QUrl("https://github.com/atik806/AgentDeck#readme"))

    # -- account signals ----------------------------------------------------

    def _on_signed_in(self, _user: dict) -> None:
        # Rebuild in place: the signed-out pitch becomes the profile view.
        self.accept()

    def _on_avatar(self, data: bytes) -> None:
        if hasattr(self, "_avatar"):
            self._avatar.setPixmap(
                _avatar_pixmap(bytes(data), _AVATAR_PX, self._account.display_name)
            )

    def _on_profile(self, profile: dict) -> None:
        # Use the controller's effective plan (it folds in plan_expires_at) rather
        # than the raw string on the row, so a lapsed Pro shows the FREE badge.
        self._apply_plan(self._account.plan)
