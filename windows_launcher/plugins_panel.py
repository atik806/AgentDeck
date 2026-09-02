"""The PLUGINS view -- a full-area panel the sidebar's nav strip swaps in.

Two levels (see ``docs/PLUGINS.md`` §2):

* **catalog** -- a vertical list of plugin cards, one per row. v1 ships three
  live cards, **GitHub**, **Vercel** and **Jira**; the rest render disabled as
  "Coming soon".
* **detail** -- click GitHub to connect it, pick which capabilities the agent
  gets, list your repos, and kick off a **GitHub review**; click Vercel or Jira
  to enable its (thin, agent-owns-the-OAuth) MCP server.

Keep :func:`plugin_icon` -- the sidebar's "Plugins" nav button reuses it.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import theme

try:
    import entitlements
except Exception:  # noqa: BLE001
    entitlements = None  # type: ignore

from plugin_store import CAPABILITIES, CAPABILITY_LABELS, REQUIRED_CAPABILITY

__all__ = ["PluginsPanel", "plugin_icon"]


def _wired_agent_labels(agents_provider, need: str = "mcp") -> list[str]:
    """Human labels of the agents a plugin will write its MCP server into.

    ``need`` -- the capability the plugin requires: ``"mcp"`` for GitHub (token
    injected), ``"mcp_oauth"`` for Vercel / Jira (agent runs the OAuth itself).
    """
    import agents
    import mcp_targets

    keys = list(agents_provider()) if agents_provider else ["claude"]
    return [agents.agent_label(k) for k in keys if mcp_targets.caps(k).get(need)]


def _oauth_auth_html(agents_provider, server: str) -> str:
    """One authorise-instruction line per OAuth-capable wired agent, as HTML."""
    import agents
    import mcp_targets

    keys = list(agents_provider()) if agents_provider else ["claude"]
    keys = [k for k in keys if mcp_targets.caps(k).get("mcp_oauth")]
    if not keys:
        return (
            f"Connected. No installed agent can authorise {server} on its own yet — "
            "this activates automatically once a supported agent (e.g. Claude Code) is on PATH."
        )
    rows = "<br>".join(
        f"• <b>{agents.agent_label(k)}</b> — {mcp_targets.oauth_hint(k, server)}" for k in keys
    )
    return "Almost there. One-time authorisation, in each agent's pane:<br>" + rows


def plugin_icon(px: int = 18, color: Optional[str] = None) -> QIcon:
    """A drawn puzzle-piece -- an emoji glyph renders broken in this Qt build."""
    color = color or theme.color("sidebar_text")
    px = max(8, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    u = px / 16.0

    body = QPainterPath()
    body.addRoundedRect(QRectF(2 * u, 5 * u, 12 * u, 9 * u), 2 * u, 2 * u)
    body.addEllipse(QRectF(6 * u, 1.6 * u, 4 * u, 4 * u))  # top knob
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(body.simplified())

    # Punch a socket out of the left edge so it reads as a plug-in.
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.drawEllipse(QRectF(0.4 * u, 8.0 * u, 3.6 * u, 3.6 * u))
    p.end()
    return QIcon(pm)


def _github_icon(px: int = 40) -> QPixmap:
    """The GitHub octocat mark, drawn (no asset dependency)."""
    px = max(12, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.color("text")))
    u = px / 16.0
    body = QPainterPath()
    body.addEllipse(QRectF(1 * u, 1 * u, 14 * u, 14 * u))
    p.drawPath(body)
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    # rough cat silhouette cut-out
    cat = QPainterPath()
    cat.addEllipse(QRectF(4.4 * u, 4.2 * u, 7.2 * u, 6.6 * u))
    cat.addRect(QRectF(6.6 * u, 9.5 * u, 2.8 * u, 4 * u))
    p.drawPath(cat)
    p.end()
    return pm


def _vercel_icon(px: int = 40) -> QPixmap:
    """The Vercel triangle mark, drawn (no asset dependency)."""
    px = max(12, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.color("text")))
    u = px / 16.0
    tri = QPainterPath()
    tri.moveTo(8 * u, 2 * u)
    tri.lineTo(15 * u, 14 * u)
    tri.lineTo(1 * u, 14 * u)
    tri.closeSubpath()
    p.drawPath(tri)
    p.end()
    return pm


def _jira_icon(px: int = 40) -> QPixmap:
    """The Jira mark, drawn (no asset dependency): two stacked down-chevrons."""
    px = max(12, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.color("accent")))
    u = px / 16.0
    # big downward chevron
    big = QPainterPath()
    big.moveTo(2 * u, 3 * u)
    big.lineTo(8 * u, 11 * u)
    big.lineTo(14 * u, 3 * u)
    big.lineTo(11 * u, 3 * u)
    big.lineTo(8 * u, 7 * u)
    big.lineTo(5 * u, 3 * u)
    big.closeSubpath()
    p.drawPath(big)
    # small offset chevron below (lighter)
    p.setBrush(QColor(theme.color("accent_2")))
    small = QPainterPath()
    small.moveTo(5 * u, 9 * u)
    small.lineTo(8 * u, 13 * u)
    small.lineTo(11 * u, 9 * u)
    small.lineTo(8 * u, 11 * u)
    small.closeSubpath()
    p.drawPath(small)
    p.end()
    return pm


# ---------------------------------------------------------------------------
# QSS
# ---------------------------------------------------------------------------

def _qss() -> str:
    t = theme.color
    return f"""
QWidget#pluginsPanel {{ background: {t('window_bg')}; }}
QLabel#pluginsTitle {{ color: {t('text')}; font-size: 20px; font-weight: 800; }}
QLabel#pluginsBody {{ color: {t('text_muted')}; font-size: 12px; }}
QLabel#sectionH {{ color: {t('text')}; font-size: 13px; font-weight: 700; }}
QLineEdit#search {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 8px; padding: 7px 10px; font-size: 12px;
}}
QLineEdit#search:focus {{ border-color: {t('accent')}; }}
QFrame#card {{
    background: {t('card_raised')}; border: 1px solid {t('card_border')};
    border-radius: 12px;
}}
QFrame#card[interactive="true"]:hover {{ border-color: {t('accent')}; }}
QLabel#cardName {{ color: {t('text')}; font-size: 14px; font-weight: 700; }}
QLabel#cardTag {{ color: {t('text_faint')}; font-size: 10px; font-weight: 700; }}
QLabel#cardDesc {{ color: {t('text_muted')}; font-size: 11px; }}
QLabel#pill {{ font-size: 10px; font-weight: 700; border-radius: 7px; padding: 2px 8px; }}
QPushButton {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 7px; padding: 7px 14px; font-size: 12px;
}}
QPushButton:hover {{ border-color: {t('accent')}; }}
QPushButton:disabled {{ color: {t('text_faint')}; border-color: {t('border')}; }}
QPushButton#primary {{
    background: {t('accent')}; color: {t('on_accent')}; border-color: {t('accent')}; font-weight: 700;
}}
QPushButton#primary:hover {{ background: {t('accent_hover')}; border-color: {t('accent_hover')}; }}
QPushButton#danger {{ color: {t('danger')}; }}
QPushButton#danger:hover {{ border-color: {t('danger')}; }}
QPushButton#link {{ background: transparent; border: none; color: {t('text_muted')}; padding: 4px 2px; }}
QPushButton#link:hover {{ color: {t('accent')}; }}
QCheckBox {{ color: {t('text')}; font-size: 12px; spacing: 8px; }}
QCheckBox:disabled {{ color: {t('text_faint')}; }}
QComboBox {{
    background: {t('surface')}; color: {t('text')}; border: 1px solid {t('border')};
    border-radius: 6px; padding: 3px 8px; font-size: 11px;
}}
QListWidget {{
    background: {t('surface')}; color: {t('text')}; border: 1px solid {t('border')};
    border-radius: 8px; font-size: 12px;
}}
QFrame#codeBox {{
    background: {t('accent_soft_bg')}; border: 1px solid {t('accent')}; border-radius: 10px;
}}
QLabel#code {{ color: {t('text')}; font-size: 22px; font-weight: 800; letter-spacing: 3px; }}
"""


# ---------------------------------------------------------------------------
# Catalog card
# ---------------------------------------------------------------------------

_CATALOG = [
    ("github", "GitHub", "Version control",
     "Review PRs, run Actions, create repos — your agent works GitHub directly.", True),
    ("gitlab", "GitLab", "Version control", "Coming soon", False),
    ("linear", "Linear", "Project mgmt", "Coming soon", False),
    ("jira", "Jira", "Project mgmt",
     "Search, read & update Jira issues and Confluence pages — your agent works Atlassian directly.", True),
    ("sentry", "Sentry", "Observability", "Coming soon", False),
    ("vercel", "Vercel", "Deploys",
     "Manage deployments, inspect build logs, roll back — your agent runs Vercel directly.", True),
]


class _PluginCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, name: str, tag: str, desc: str, live: bool, parent=None):
        super().__init__(parent)
        self.key = key
        self.plugin_name = name
        self._live = live
        self.setObjectName("card")
        self.setProperty("interactive", "true" if live else "false")
        self.setMinimumHeight(76)
        self.setCursor(Qt.PointingHandCursor if live else Qt.ArrowCursor)

        # One list row: icon · (name / tag / description) · status pill.
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(14)

        icon = QLabel()
        icon.setFixedSize(28, 28)
        if key == "github":
            icon.setPixmap(_github_icon(28))
        elif key == "vercel":
            icon.setPixmap(_vercel_icon(28))
        elif key == "jira":
            icon.setPixmap(_jira_icon(28))
        else:
            icon.setPixmap(plugin_icon(24, theme.color("text_faint")).pixmap(24, 24))
        row.addWidget(icon, 0, Qt.AlignVCenter)

        mid = QVBoxLayout()
        mid.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        n = QLabel(name)
        n.setObjectName("cardName")
        tg = QLabel(tag.upper())
        tg.setObjectName("cardTag")
        head.addWidget(n, 0)
        head.addWidget(tg, 0, Qt.AlignVCenter)
        head.addStretch(1)
        mid.addLayout(head)
        d = QLabel(desc)
        d.setObjectName("cardDesc")
        d.setWordWrap(True)
        mid.addWidget(d)
        row.addLayout(mid, 1)

        self._pill = QLabel()
        self._pill.setObjectName("pill")
        self.set_status(None)
        row.addWidget(self._pill, 0, Qt.AlignVCenter)

    def set_status(self, login: Optional[str]) -> None:
        if not self._live:
            self._pill.setText("COMING SOON")
            self._pill.setStyleSheet(
                f"QLabel#pill {{ color:{theme.color('text_faint')}; background:{theme.color('surface')}; }}"
            )
            return
        if login is None:
            self._pill.setText("NOT CONNECTED")
            self._pill.setStyleSheet(
                f"QLabel#pill {{ color:{theme.color('text_muted')}; background:{theme.color('surface')}; }}"
            )
        else:
            self._pill.setText(f"CONNECTED{(' · @' + login) if login else ''}")
            self._pill.setStyleSheet(
                f"QLabel#pill {{ color:{theme.color('on_accent')}; background:{theme.color('activity')}; }}"
            )

    def set_toggle_status(self, enabled: bool) -> None:
        """Status for a plugin with no external identity (Vercel): just on/off."""
        if not self._live:
            self._pill.setText("COMING SOON")
            self._pill.setStyleSheet(
                f"QLabel#pill {{ color:{theme.color('text_faint')}; background:{theme.color('surface')}; }}"
            )
            return
        if enabled:
            self._pill.setText("ENABLED")
            self._pill.setStyleSheet(
                f"QLabel#pill {{ color:{theme.color('on_accent')}; background:{theme.color('activity')}; }}"
            )
        else:
            self._pill.setText("NOT ENABLED")
            self._pill.setStyleSheet(
                f"QLabel#pill {{ color:{theme.color('text_muted')}; background:{theme.color('surface')}; }}"
            )

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._live and self.rect().contains(event.pos()):
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# GitHub detail
# ---------------------------------------------------------------------------

class _GitHubDetail(QWidget):
    back = Signal()
    review_ready = Signal(dict)

    def __init__(self, github, account, config, agents_provider=None, parent=None):
        super().__init__(parent)
        self._gh = github
        self._account = account
        self._config = config or {}
        self._agents_provider = agents_provider
        self._cap_boxes: dict[str, QCheckBox] = {}
        self._auto_combos: dict[str, QComboBox] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        back = QPushButton("‹  All plugins")
        back.setObjectName("link")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self.back.emit)
        root.addWidget(back, 0, Qt.AlignLeft)

        head = QHBoxLayout()
        head.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(40, 40)
        icon.setPixmap(_github_icon(40))
        head.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel("GitHub")
        title.setObjectName("pluginsTitle")
        self._sub = QLabel("")
        self._sub.setObjectName("pluginsBody")
        col.addWidget(title)
        col.addWidget(self._sub)
        head.addLayout(col, 1)
        self._primary = QPushButton("Connect")
        self._primary.setObjectName("primary")
        self._primary.setCursor(Qt.PointingHandCursor)
        self._primary.clicked.connect(self._on_primary)
        head.addWidget(self._primary, 0, Qt.AlignTop)
        root.addLayout(head)

        # device-code box (hidden until connecting)
        self._code_box = QFrame()
        self._code_box.setObjectName("codeBox")
        cb = QVBoxLayout(self._code_box)
        cb.setContentsMargins(14, 12, 14, 12)
        cb.setSpacing(6)
        cb.addWidget(QLabel("Enter this code at github.com/login/device:"))
        self._code = QLabel("––––––––")
        self._code.setObjectName("code")
        cb.addWidget(self._code)
        crow = QHBoxLayout()
        open_btn = QPushButton("Open GitHub")
        open_btn.setObjectName("primary")
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/login/device"))
        )
        copy_btn = QPushButton("Copy code")
        copy_btn.clicked.connect(self._copy_code)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("danger")
        cancel_btn.clicked.connect(self._cancel_connect)
        crow.addWidget(open_btn)
        crow.addWidget(copy_btn)
        crow.addStretch(1)
        crow.addWidget(cancel_btn)
        cb.addLayout(crow)
        self._code_box.setVisible(False)
        root.addWidget(self._code_box)

        # capabilities
        self._caps_h = QLabel("What your agent can do")
        self._caps_h.setObjectName("sectionH")
        root.addWidget(self._caps_h)
        self._caps_wrap = QWidget()
        caps_l = QVBoxLayout(self._caps_wrap)
        caps_l.setContentsMargins(0, 0, 0, 0)
        caps_l.setSpacing(6)
        for cap in CAPABILITIES:
            r = QHBoxLayout()
            box = QCheckBox(CAPABILITY_LABELS.get(cap, cap))
            if cap == REQUIRED_CAPABILITY:
                box.setChecked(True)
                box.setEnabled(False)
            box.toggled.connect(self._on_caps_changed)
            self._cap_boxes[cap] = box
            combo = QComboBox()
            combo.addItem("Ask first", "ask")
            combo.addItem("Autonomous", "auto")
            combo.setFixedWidth(120)
            combo.currentIndexChanged.connect(
                lambda _i, c=cap: self._on_auto_changed(c)
            )
            self._auto_combos[cap] = combo
            r.addWidget(box, 1)
            r.addWidget(combo, 0)
            caps_l.addLayout(r)
        root.addWidget(self._caps_wrap)

        # repos + review
        self._repo_h = QLabel("Your repositories")
        self._repo_h.setObjectName("sectionH")
        root.addWidget(self._repo_h)
        self._repos = QListWidget()
        self._repos.setMaximumHeight(150)
        root.addWidget(self._repos)

        rrow = QHBoxLayout()
        self._review_btn = QPushButton("Review a pull request…")
        self._review_btn.setObjectName("primary")
        self._review_btn.clicked.connect(self._open_review_dialog)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setObjectName("danger")
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        rrow.addWidget(self._review_btn)
        rrow.addStretch(1)
        rrow.addWidget(self._disconnect_btn)
        root.addLayout(rrow)
        root.addStretch(1)

        if self._gh is not None:
            self._gh.connected.connect(lambda _i: self.refresh())
            self._gh.disconnected.connect(self.refresh)
            self._gh.device_code_ready.connect(self._show_code)
            self._gh.repos_ready.connect(self._fill_repos)
            self._gh.error.connect(self._on_error)
            self._gh.busy_changed.connect(lambda _b: self.refresh())

        self.refresh()

    # -- state ----------------------------------------------------------

    def _plan_ok(self) -> bool:
        if entitlements is None or self._account is None:
            return True
        try:
            return entitlements.plugins_enabled(self._account.plan)
        except Exception:  # noqa: BLE001
            return True

    def refresh(self) -> None:
        gh = self._gh
        connected = bool(gh and gh.is_connected)
        busy = bool(gh and gh.is_busy)
        pro = self._plan_ok()

        self._primary.setVisible(not connected)
        self._primary.setEnabled(pro and not busy)
        self._primary.setText("Connect" if pro else "Connect  (Pro)")

        for w in (self._caps_h, self._caps_wrap, self._repo_h, self._repos):
            w.setVisible(connected)
        self._review_btn.setVisible(connected)
        self._disconnect_btn.setVisible(connected)

        if connected:
            conn = gh.connection
            who = f"Connected as @{gh.login}" if gh.login else "Connected"
            wired = _wired_agent_labels(self._agents_provider, "mcp_oauth")
            if wired:
                who += " · tools in: " + ", ".join(wired)
            self._sub.setText(who)
            caps = set(conn.capabilities) if conn else set()
            for cap, box in self._cap_boxes.items():
                box.blockSignals(True)
                if cap != REQUIRED_CAPABILITY:
                    box.setChecked(cap in caps)
                box.blockSignals(False)
                combo = self._auto_combos[cap]
                combo.setVisible(cap in caps and cap != REQUIRED_CAPABILITY)
                mode = conn.automation_mode(cap) if conn else "ask"
                combo.blockSignals(True)
                combo.setCurrentIndex(1 if mode == "auto" else 0)
                combo.blockSignals(False)
            if self._repos.count() == 0 and not busy:
                gh.fetch_repos()
        else:
            self._sub.setText(
                "Let the agent in a workspace review PRs, run Actions and manage "
                "repos — no tokens to copy."
            )
        if not busy:
            self._code_box.setVisible(False)

    # -- actions ------------------------------------------------------

    def _on_primary(self) -> None:
        if self._gh is not None:
            self._gh.start_connect()

    def _cancel_connect(self) -> None:
        if self._gh is not None:
            self._gh.cancel_connect()
        self._code_box.setVisible(False)

    def _on_disconnect(self) -> None:
        if self._gh is not None:
            self._gh.disconnect()

    def _show_code(self, info: dict) -> None:
        code = str(info.get("user_code") or "")
        self._code.setText(code or "––––––––")
        self._code_box.setVisible(True)
        self._copy_code()
        QDesktopServices.openUrl(
            QUrl(str(info.get("verification_uri") or "https://github.com/login/device"))
        )

    def _copy_code(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(self._code.text())

    def _on_caps_changed(self, *_a) -> None:
        if self._gh is None:
            return
        chosen = [c for c, b in self._cap_boxes.items() if b.isChecked()]
        self._gh.set_capabilities(chosen)

    def _on_auto_changed(self, cap: str) -> None:
        if self._gh is None:
            return
        combo = self._auto_combos[cap]
        self._gh.set_automation(cap, combo.currentData())

    def _fill_repos(self, repos: list) -> None:
        self._repos.clear()
        for r in repos:
            name = r.get("full_name") if isinstance(r, dict) else str(r)
            if name:
                self._repos.addItem(("🔒 " if isinstance(r, dict) and r.get("private") else "") + name)

    def _on_error(self, message: str) -> None:
        self._sub.setText(message)

    def _selected_repo(self) -> str:
        item = self._repos.currentItem()
        if item is None:
            return ""
        return item.text().replace("🔒 ", "").strip()

    def _open_review_dialog(self) -> None:
        try:
            from github_review_dialog import GitHubReviewDialog
        except Exception as exc:  # noqa: BLE001
            self._sub.setText(f"Review dialog unavailable: {exc}")
            return
        dlg = GitHubReviewDialog(self._gh, preselect_repo=self._selected_repo(), parent=self)
        if dlg.exec():
            payload = dlg.result_payload()
            if payload:
                self.review_ready.emit(payload)

    def apply_theme(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Vercel detail
# ---------------------------------------------------------------------------

class _VercelDetail(QWidget):
    """Thin detail page: enable / disable the Vercel MCP server.

    No device-code box, no capability checklist, no repo list -- Vercel's MCP
    server is hosted and OAuth-only, and the agent owns the OAuth. "Connect" just
    drops the server entry into every OAuth-capable agent's config; the user
    authorises in a pane (``/mcp`` for Claude, ``codex mcp login vercel`` for
    Codex, …).
    """

    back = Signal()

    def __init__(self, vercel, account, config, agents_provider=None, parent=None):
        super().__init__(parent)
        self._vercel = vercel
        self._account = account
        self._config = config or {}
        self._agents_provider = agents_provider

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        back = QPushButton("‹  All plugins")
        back.setObjectName("link")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self.back.emit)
        root.addWidget(back, 0, Qt.AlignLeft)

        head = QHBoxLayout()
        head.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(40, 40)
        icon.setPixmap(_vercel_icon(40))
        head.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel("Vercel")
        title.setObjectName("pluginsTitle")
        self._sub = QLabel("")
        self._sub.setObjectName("pluginsBody")
        self._sub.setWordWrap(True)
        col.addWidget(title)
        col.addWidget(self._sub)
        head.addLayout(col, 1)
        self._primary = QPushButton("Connect")
        self._primary.setObjectName("primary")
        self._primary.setCursor(Qt.PointingHandCursor)
        self._primary.clicked.connect(self._on_primary)
        head.addWidget(self._primary, 0, Qt.AlignTop)
        root.addLayout(head)

        # one-time-authorise instructions (shown once connected)
        self._info = QFrame()
        self._info.setObjectName("codeBox")
        ib = QVBoxLayout(self._info)
        ib.setContentsMargins(14, 12, 14, 12)
        ib.setSpacing(4)
        self._step = QLabel("")
        self._step.setWordWrap(True)
        self._step.setTextFormat(Qt.RichText)
        ib.addWidget(self._step)
        self._info.setVisible(False)
        root.addWidget(self._info)

        drow = QHBoxLayout()
        drow.addStretch(1)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setObjectName("danger")
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        drow.addWidget(self._disconnect_btn)
        root.addLayout(drow)
        root.addStretch(1)

        if self._vercel is not None:
            self._vercel.connected.connect(lambda _i: self.refresh())
            self._vercel.disconnected.connect(self.refresh)
            self._vercel.error.connect(self._on_error)
            self._vercel.busy_changed.connect(lambda _b: self.refresh())

        self.refresh()

    def _plan_ok(self) -> bool:
        if entitlements is None or self._account is None:
            return True
        try:
            return entitlements.plugins_enabled(self._account.plan)
        except Exception:  # noqa: BLE001
            return True

    def refresh(self) -> None:
        v = self._vercel
        connected = bool(v and v.is_connected)
        busy = bool(v and v.is_busy)
        pro = self._plan_ok()

        self._primary.setVisible(not connected)
        self._primary.setEnabled(pro and not busy)
        self._primary.setText("Connect" if pro else "Connect  (Pro)")

        self._info.setVisible(connected)
        self._disconnect_btn.setVisible(connected)

        if connected:
            self._step.setText(_oauth_auth_html(self._agents_provider, "vercel"))
            wired = _wired_agent_labels(self._agents_provider, "mcp_oauth")
            self._sub.setText(
                "Enabled for: " + (", ".join(wired) if wired else "no installed agent yet")
            )
        else:
            self._sub.setText(
                "Let your agents ship deployments, read build logs and query "
                "analytics — no tokens to copy."
            )

    def _on_primary(self) -> None:
        if self._vercel is not None:
            self._vercel.start_connect()

    def _on_disconnect(self) -> None:
        if self._vercel is not None:
            self._vercel.disconnect()

    def _on_error(self, message: str) -> None:
        self._sub.setText(message)

    def apply_theme(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Jira detail
# ---------------------------------------------------------------------------

class _JiraDetail(QWidget):
    """Thin detail page: enable / disable the Atlassian (Jira) MCP server.

    Same shape as :class:`_VercelDetail` -- Atlassian's Rovo MCP server is hosted
    and OAuth-only, and the agent owns the OAuth. "Connect" drops the tokenless
    server entry into every OAuth-capable agent's config; the user authorises in
    a pane.
    """

    back = Signal()

    def __init__(self, jira, account, config, agents_provider=None, parent=None):
        super().__init__(parent)
        self._jira = jira
        self._account = account
        self._config = config or {}
        self._agents_provider = agents_provider

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        back = QPushButton("‹  All plugins")
        back.setObjectName("link")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self.back.emit)
        root.addWidget(back, 0, Qt.AlignLeft)

        head = QHBoxLayout()
        head.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(40, 40)
        icon.setPixmap(_jira_icon(40))
        head.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel("Jira")
        title.setObjectName("pluginsTitle")
        self._sub = QLabel("")
        self._sub.setObjectName("pluginsBody")
        self._sub.setWordWrap(True)
        col.addWidget(title)
        col.addWidget(self._sub)
        head.addLayout(col, 1)
        self._primary = QPushButton("Connect")
        self._primary.setObjectName("primary")
        self._primary.setCursor(Qt.PointingHandCursor)
        self._primary.clicked.connect(self._on_primary)
        head.addWidget(self._primary, 0, Qt.AlignTop)
        root.addLayout(head)

        # one-time-authorise instructions (shown once connected)
        self._info = QFrame()
        self._info.setObjectName("codeBox")
        ib = QVBoxLayout(self._info)
        ib.setContentsMargins(14, 12, 14, 12)
        ib.setSpacing(4)
        self._step = QLabel("")
        self._step.setWordWrap(True)
        self._step.setTextFormat(Qt.RichText)
        ib.addWidget(self._step)
        self._info.setVisible(False)
        root.addWidget(self._info)

        drow = QHBoxLayout()
        drow.addStretch(1)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setObjectName("danger")
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        drow.addWidget(self._disconnect_btn)
        root.addLayout(drow)
        root.addStretch(1)

        if self._jira is not None:
            self._jira.connected.connect(lambda _i: self.refresh())
            self._jira.disconnected.connect(self.refresh)
            self._jira.error.connect(self._on_error)
            self._jira.busy_changed.connect(lambda _b: self.refresh())

        self.refresh()

    def _plan_ok(self) -> bool:
        if entitlements is None or self._account is None:
            return True
        try:
            return entitlements.plugins_enabled(self._account.plan)
        except Exception:  # noqa: BLE001
            return True

    def refresh(self) -> None:
        j = self._jira
        connected = bool(j and j.is_connected)
        busy = bool(j and j.is_busy)
        pro = self._plan_ok()

        self._primary.setVisible(not connected)
        self._primary.setEnabled(pro and not busy)
        self._primary.setText("Connect" if pro else "Connect  (Pro)")

        self._info.setVisible(connected)
        self._disconnect_btn.setVisible(connected)

        if connected:
            self._step.setText(_oauth_auth_html(self._agents_provider, "atlassian"))
            wired = _wired_agent_labels(self._agents_provider, "mcp_oauth")
            self._sub.setText(
                "Enabled for: " + (", ".join(wired) if wired else "no installed agent yet")
            )
        else:
            self._sub.setText(
                "Let your agents search, read and update Jira issues and "
                "Confluence pages — no tokens to copy."
            )

    def _on_primary(self) -> None:
        if self._jira is not None:
            self._jira.start_connect()

    def _on_disconnect(self) -> None:
        if self._jira is not None:
            self._jira.disconnect()

    def _on_error(self, message: str) -> None:
        self._sub.setText(message)

    def apply_theme(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class PluginsPanel(QWidget):
    """Full-area panel shown when the sidebar's "Plugins" nav item is active."""

    review_ready = Signal(dict)

    def __init__(self, parent: QWidget | None = None, *, github=None, vercel=None,
                 jira=None, account=None, config: Optional[dict] = None,
                 agents_provider=None):
        super().__init__(parent)
        self._github = github
        self._vercel = vercel
        self._jira = jira
        # () -> list[str] of agent keys the plugins will wire (installed + active).
        self._agents_provider = agents_provider
        self.setObjectName("pluginsPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget(self)
        outer.addWidget(self._stack)

        # -- catalog page --
        catalog = QWidget()
        cl = QVBoxLayout(catalog)
        cl.setContentsMargins(40, 36, 40, 24)
        cl.setSpacing(14)

        title = QLabel("Plugins")
        title.setObjectName("pluginsTitle")
        body = QLabel("Connect AgentDeck to the tools your agents work in.")
        body.setObjectName("pluginsBody")
        cl.addWidget(title)
        cl.addWidget(body)

        self._search = QLineEdit()
        self._search.setObjectName("search")
        self._search.setPlaceholderText("Search plugins…")
        self._search.setMaximumWidth(320)
        self._search.textChanged.connect(self._filter_cards)
        cl.addWidget(self._search)

        list_host = QWidget()
        self._list = QVBoxLayout(list_host)
        self._list.setContentsMargins(0, 6, 0, 0)
        self._list.setSpacing(10)
        self._cards: list[_PluginCard] = []
        for key, name, tag, desc, live in _CATALOG:
            card = _PluginCard(key, name, tag, desc, live)
            card.clicked.connect(self._open_detail)
            self._list.addWidget(card)
            self._cards.append(card)
        self._list.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(list_host)
        cl.addWidget(scroll, 1)

        self._stack.addWidget(catalog)

        # -- detail page --
        detail_host = QScrollArea()
        detail_host.setWidgetResizable(True)
        detail_host.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(40, 28, 40, 24)
        self._gh_detail = _GitHubDetail(github, account, config, agents_provider=agents_provider)
        self._gh_detail.back.connect(lambda: self._stack.setCurrentIndex(0))
        self._gh_detail.review_ready.connect(self.review_ready.emit)
        il.addWidget(self._gh_detail)
        detail_host.setWidget(inner)
        self._stack.addWidget(detail_host)

        # -- vercel detail page --
        v_host = QScrollArea()
        v_host.setWidgetResizable(True)
        v_host.setFrameShape(QFrame.NoFrame)
        v_inner = QWidget()
        vil = QVBoxLayout(v_inner)
        vil.setContentsMargins(40, 28, 40, 24)
        self._vercel_detail = _VercelDetail(vercel, account, config, agents_provider=agents_provider)
        self._vercel_detail.back.connect(lambda: self._stack.setCurrentIndex(0))
        vil.addWidget(self._vercel_detail)
        v_host.setWidget(v_inner)
        self._stack.addWidget(v_host)

        # -- jira detail page --
        j_host = QScrollArea()
        j_host.setWidgetResizable(True)
        j_host.setFrameShape(QFrame.NoFrame)
        j_inner = QWidget()
        jil = QVBoxLayout(j_inner)
        jil.setContentsMargins(40, 28, 40, 24)
        self._jira_detail = _JiraDetail(jira, account, config, agents_provider=agents_provider)
        self._jira_detail.back.connect(lambda: self._stack.setCurrentIndex(0))
        jil.addWidget(self._jira_detail)
        j_host.setWidget(j_inner)
        self._stack.addWidget(j_host)

        if github is not None:
            github.connected.connect(lambda _i: self._sync_cards())
            github.disconnected.connect(self._sync_cards)
        if vercel is not None:
            vercel.connected.connect(lambda _i: self._sync_cards())
            vercel.disconnected.connect(self._sync_cards)
        if jira is not None:
            jira.connected.connect(lambda _i: self._sync_cards())
            jira.disconnected.connect(self._sync_cards)
        self._sync_cards()

    # -- helpers ------------------------------------------------------

    def _filter_cards(self, text: str) -> None:
        text = (text or "").strip().lower()
        for card in self._cards:
            card.setVisible(text in card.key or text in card.plugin_name.lower())

    def _open_detail(self, key: str) -> None:
        if key == "github":
            self._stack.setCurrentIndex(1)
            self._gh_detail.refresh()
        elif key == "vercel":
            self._stack.setCurrentIndex(2)
            self._vercel_detail.refresh()
        elif key == "jira":
            self._stack.setCurrentIndex(3)
            self._jira_detail.refresh()

    def _sync_cards(self) -> None:
        login = None
        if self._github is not None and self._github.is_connected:
            login = self._github.login or ""
        vercel_on = bool(self._vercel is not None and self._vercel.is_connected)
        jira_on = bool(self._jira is not None and self._jira.is_connected)
        for card in self._cards:
            if card.key == "github":
                card.set_status(login)
            elif card.key == "vercel":
                card.set_toggle_status(vercel_on)
            elif card.key == "jira":
                card.set_toggle_status(jira_on)

    def show_catalog(self) -> None:
        self._stack.setCurrentIndex(0)

    def apply_theme(self) -> None:
        self.setStyleSheet(_qss())
        self._sync_cards()
