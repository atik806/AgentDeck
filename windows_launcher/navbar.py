"""Toolbar widgets for the account chip and the help menu.

``terminal_panel._build_toolbar`` drops an :class:`AccountChip` and a
:class:`HelpButton` at the far-right of the main toolbar. The chip mirrors the
product mock -- a circular avatar, the display name and a plan badge -- and
opens ``AccountDialog`` when clicked; signed-out it shrinks to a "Sign in" pill.

Neither widget owns any auth logic. They read :class:`account.AccountController`
state and reflect its signals (``signed_in`` / ``signed_out`` / ``avatar_ready``
/ ``profile_ready``), so the panel only has to construct them.
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QMenu, QToolButton, QWidget

import theme

__all__ = [
    "AccountChip", "HelpButton", "circular_avatar",
    "gear_icon", "help_icon", "theme_icon",
]

#: Where the help menu points.
_DOCS_URL = "https://github.com/atik806/AgentDeck#readme"
_ISSUES_URL = "https://github.com/atik806/AgentDeck/issues/new"


def _open(url: str) -> None:
    """Hand a URL to the system browser."""
    QDesktopServices.openUrl(QUrl(url))

# Toolbar surface colours come from `theme` so a custom-painted widget sits
# flush with the stylesheet-painted ones in either light or dark mode. These
# helpers read the *current* mode every call -- cheap, and always in step.
def _BG() -> str: return theme.color("surface")
def _BG_HOVER() -> str: return theme.color("surface_hover")
def _BORDER() -> str: return theme.color("border")
def _BORDER_HOVER() -> str: return theme.color("border_hover")
def _TEXT() -> str: return theme.color("text")
def _MUTED() -> str: return theme.color("text_muted")
def _PRO() -> str: return theme.color("pro")
def _ACCENT() -> str: return theme.color("accent")

#: Toolbar control height (matches the fixed-size buttons next to it).
_H = 27


# ---------------------------------------------------------------------------
# drawn art
# ---------------------------------------------------------------------------

def circular_avatar(
    data: Optional[bytes],
    size: int,
    fallback_text: str,
    accent: Optional[str] = None,
) -> QPixmap:
    """A round avatar ``size`` px across.

    ``data`` is raw image bytes (PNG/JPEG from the identity provider). When it is
    missing or will not decode, draw the first letter of ``fallback_text`` on an
    ``accent`` disc instead -- the same convention the workspace swatches use.
    """
    size = max(4, int(size))
    accent = accent or _ACCENT()
    initial = (fallback_text or "?").strip()[:1].upper() or "?"

    image: Optional[QImage] = None
    if data:
        img = QImage.fromData(bytes(data))
        if not img.isNull():
            img = img.convertToFormat(QImage.Format_ARGB32)
            img = img.scaled(
                size, size,
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            ox = max(0, (img.width() - size) // 2)
            oy = max(0, (img.height() - size) // 2)
            image = img.copy(ox, oy, size, size)

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    clip = QPainterPath()
    clip.addEllipse(0.0, 0.0, float(size), float(size))
    p.setClipPath(clip)

    if image is not None:
        p.drawImage(0, 0, image)
    else:
        p.fillPath(clip, QColor(accent))
        p.setPen(QColor("#ffffff"))
        f = QFont()
        f.setPixelSize(max(6, int(size * 0.46)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, initial)

    # A hairline rim so a light avatar doesn't bleed into a light toolbar.
    p.setClipping(False)
    p.setPen(QPen(QColor(0, 0, 0, 70), 1))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QRectF(0.5, 0.5, size - 1.0, size - 1.0))
    p.end()
    return pm


def _icon_color(color: Optional[str]) -> QColor:
    return QColor(color) if color else QColor(theme.color("text_muted"))


def gear_icon(px: int = 16, color: Optional[str] = None) -> QIcon:
    """A drawn settings cog -- reliable where an emoji font isn't."""
    px = max(8, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = _icon_color(color)
    cx = cy = px / 2.0
    body_r = px * 0.30
    tooth_w = px * 0.16
    tooth_r = body_r + px * 0.13

    p.setPen(Qt.NoPen)
    p.setBrush(c)
    for i in range(8):
        p.save()
        p.translate(cx, cy)
        p.rotate(i * 45.0)
        p.drawRoundedRect(
            QRectF(-tooth_w / 2.0, -tooth_r, tooth_w, tooth_r * 2.0), 1.2, 1.2
        )
        p.restore()
    p.drawEllipse(QPointF(cx, cy), body_r, body_r)

    # Punch the centre out so it reads as a gear, not a flower.
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.drawEllipse(QPointF(cx, cy), body_r * 0.42, body_r * 0.42)
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    p.end()
    return QIcon(pm)


def help_icon(px: int = 16, color: Optional[str] = None) -> QIcon:
    """A drawn "?" in a ring."""
    px = max(8, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = _icon_color(color)
    p.setPen(QPen(c, max(1.0, px * 0.09)))
    p.setBrush(Qt.NoBrush)
    m = px * 0.12
    p.drawEllipse(QRectF(m, m, px - 2 * m, px - 2 * m))
    f = QFont()
    f.setPixelSize(max(6, int(px * 0.62)))
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "?")
    p.end()
    return QIcon(pm)


def theme_icon(px: int = 16, color: Optional[str] = None, *, mode: Optional[str] = None) -> QIcon:
    """A sun (shown in dark mode -> "switch to light") or a crescent moon
    (shown in light mode -> "switch to dark"). ``mode`` is the *current* theme;
    the glyph is the one you'd tap to leave it."""
    px = max(8, int(px))
    now = mode or theme.mode()
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = _icon_color(color)
    cx = cy = px / 2.0

    if now == "dark":
        # sun
        r = px * 0.20
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(cx, cy), r, r)
        pen = QPen(c, max(1.0, px * 0.08))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for i in range(8):
            a = math.radians(i * 45.0)
            r0, r1 = px * 0.32, px * 0.44
            p.drawLine(
                QPointF(cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
                QPointF(cx + r1 * math.cos(a), cy + r1 * math.sin(a)),
            )
    else:
        # crescent moon: a disc with a second disc punched out
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(cx, cy), px * 0.34, px * 0.34)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.drawEllipse(QPointF(cx + px * 0.16, cy - px * 0.06), px * 0.30, px * 0.30)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# account chip
# ---------------------------------------------------------------------------

class AccountChip(QToolButton):
    """Avatar + name + plan badge; ``clicked`` opens the account dialog.

    Signed-out it collapses to an accent-outlined "Sign in" pill. It wires
    itself to the controller's signals, so callers only construct it (and may
    call :meth:`refresh` after a manual state change).
    """

    def __init__(self, account, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._account = account
        self._avatar_bytes: Optional[bytes] = None
        self._avatar_pm: Optional[QPixmap] = None
        self._avatar_key = None

        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(_H)
        self.setToolTip("Your AgentDeck account")

        for name in ("signed_in", "signed_out", "avatar_ready", "profile_ready"):
            sig = getattr(account, name, None)
            if sig is not None:
                sig.connect(self._on_account_event)

        self.refresh()

    # -- state -----------------------------------------------------------------

    def _on_account_event(self, *args) -> None:
        if len(args) == 1 and isinstance(args[0], (bytes, bytearray)):
            self._avatar_bytes = bytes(args[0])
            self._avatar_pm = None
        self.refresh()

    def refresh(self) -> None:
        """Re-read the controller and re-lay-out for the new content width."""
        if self._signed_in() and self._avatar_bytes is None:
            fetch = getattr(self._account, "fetch_avatar", None)
            if callable(fetch):
                try:
                    fetch()
                except Exception:  # noqa: BLE001 - avatar is cosmetic
                    pass
        self._avatar_pm = None
        self.setToolTip(self._tooltip())
        self.updateGeometry()
        self.update()

    def _signed_in(self) -> bool:
        return bool(getattr(self._account, "is_signed_in", False))

    def _display_label(self) -> str:
        if not self._signed_in():
            return "Sign in"
        name = str(getattr(self._account, "display_name", "") or "").strip()
        if name:
            return name
        email = str(getattr(self._account, "email", "") or "").strip()
        return email.split("@", 1)[0] if email else "Account"

    def _plan_text(self) -> str:
        plan = getattr(self._account, "plan", "free")
        try:
            import entitlements
            if entitlements.is_pro(plan):
                return "PRO"
        except Exception:  # noqa: BLE001
            p = str(plan or "free").strip().lower()
            if p in ("pro", "paid", "team", "plus"):
                return "PRO"
        left = getattr(self._account, "trial_days_left", None)
        if isinstance(left, int) and left >= 0:
            return "TRIAL"
        return "FREE"

    def _tooltip(self) -> str:
        if not self._signed_in():
            return "Sign in to AgentDeck"
        email = str(getattr(self._account, "email", "") or "").strip()
        base = f"{self._display_label()}\n{email}" if email else self._display_label()
        if self._plan_text() == "TRIAL":
            left = getattr(self._account, "trial_days_left", None)
            if isinstance(left, int):
                base += f"\nFree trial: {max(0, left)} day(s) left"
        return base

    # -- geometry ------------------------------------------------------------

    def _badge_font(self) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(8)
        f.setBold(True)
        return f

    def _name_font(self) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setBold(True)
        return f

    def _badge_width(self) -> int:
        return QFontMetrics(self._badge_font()).horizontalAdvance(self._plan_text()) + 10

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        if not self._signed_in():
            f = QFont(self.font())
            f.setPixelSize(11)
            return QSize(QFontMetrics(f).horizontalAdvance("Sign in") + 28, _H)
        d = _H - 2 * 4 - 2
        name_w = min(
            160,
            QFontMetrics(self._name_font()).horizontalAdvance(self._display_label() or "Account"),
        )
        w = 5 + d + 7 + name_w + 8 + self._badge_width() + 9
        return QSize(int(w), _H)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return self.sizeHint()

    # -- avatar ------------------------------------------------------------

    def _avatar_pixmap(self, size: int) -> QPixmap:
        key = (size, id(self._avatar_bytes) if self._avatar_bytes else 0)
        key = (*key, theme.mode())
        if self._avatar_pm is None or self._avatar_key != key:
            self._avatar_pm = circular_avatar(
                self._avatar_bytes, size, self._display_label(), _ACCENT()
            )
            self._avatar_key = key
        return self._avatar_pm

    # -- painting ------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        hover = self.underMouse() and self.isEnabled()
        signed_in = self._signed_in()

        if signed_in:
            bg = QColor(_BG_HOVER() if hover else _BG())
            border = QColor(_BORDER_HOVER() if hover else _BORDER())
        else:
            soft = QColor(theme.color("accent_soft_bg"))
            if hover:
                soft = soft.lighter(112)
            bg = soft
            border = QColor(_ACCENT())

        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, 6, 6)

        if not signed_in:
            p.setPen(QColor(theme.color("accent_text")))
            f = QFont(self.font())
            f.setPixelSize(11)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, "Sign in")
            p.end()
            return

        m = 4
        d = self.height() - 2 * m - 2
        ax = 5.0
        ay = (self.height() - d) / 2.0
        p.drawPixmap(int(ax), int(ay), self._avatar_pixmap(int(d)))

        x = ax + d + 7
        badge_w = self._badge_width()

        p.setPen(QColor(_TEXT()))
        p.setFont(self._name_font())
        fm = p.fontMetrics()
        avail = self.width() - x - badge_w - 8 - 9
        name = fm.elidedText(self._display_label(), Qt.ElideRight, max(12, int(avail)))
        name_w = fm.horizontalAdvance(name)
        p.drawText(
            QRect(int(x), 0, int(name_w + 2), self.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            name,
        )
        x += name_w + 8

        plan = self._plan_text()
        is_pro = plan == "PRO"
        p.setFont(self._badge_font())
        bh = 14.0
        badge = QRectF(x, (self.height() - bh) / 2.0, badge_w, bh)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_PRO()) if is_pro else QColor(theme.color("surface_hover")))
        p.drawRoundedRect(badge, 4, 4)
        p.setPen(QColor(theme.color("window_bg")) if is_pro else QColor(_MUTED()))
        p.drawText(badge, Qt.AlignCenter, plan)
        p.end()


# ---------------------------------------------------------------------------
# help menu
# ---------------------------------------------------------------------------

class HelpButton(QToolButton):
    """A "?" toolbar button with a small drop-down of help destinations."""

    #: The user picked "Keyboard shortcuts" -- the panel shows its cheat sheet.
    shortcuts_requested = Signal()
    #: The user picked "About AgentDeck" -- the panel shows the about box.
    about_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setIcon(help_icon(16))
        self.setToolTip("Help")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(30, _H)
        self.setPopupMode(QToolButton.InstantPopup)
        # Hide the tiny down-caret; the icon is enough and it crowds a 30px button.
        self.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")

        menu = QMenu(self)
        # Wrap every slot in an arg-swallowing lambda: QAction.triggered carries a
        # `checked` bool, and handing that to a 0-arg signal or helper raises.
        menu.addAction("Documentation", lambda *_: _open(_DOCS_URL))
        menu.addAction("Keyboard shortcuts", lambda *_: self.shortcuts_requested.emit())
        menu.addAction("Report an issue", lambda *_: _open(_ISSUES_URL))
        menu.addAction("About AgentDeck", lambda *_: self.about_requested.emit())
        self.setMenu(menu)
        self._menu = menu

    def apply_theme(self) -> None:
        self.setIcon(help_icon(16))
