"""The setup wizard -- the first thing you see when the panel starts.

Three steps, matching the product mockup:

    1. Start   -- welcome + a quick-launch list of recent folders
    2. Layout  -- pick the working folder, choose how many terminals
    3. Agents  -- pick the coding agent (claude / codex / opencode / …) that
                  auto-runs in every terminal

``main.py`` shows it modally before building the panel; :meth:`choices` returns
the picked ``{folder, count, agent_key, agent_command}``. ``exec()`` is
``Accepted`` only when the user launches (or skips, or clicks a recent folder);
closing the dialog is ``Rejected`` and the app just exits.

The wizard has its own amber accent -- deliberately distinct from the panel's
blue -- so it reads as a separate "front door".
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from agents import (
    CUSTOM_KEY,
    PLAIN_KEY,
    agent_label,
    all_agents,
    resolve_agent,
)
from agents_ui import InstallHint
from version import __version__
from workspace import MAX_PANES, grid_dims

__all__ = ["SetupWizard"]

_AMBER = "#e8833a"
_AMBER_HI = "#f0954e"
_BG = "#171717"
_CARD = "#212121"
_CARD_HI = "#282828"
_BORDER = "#333333"
_TEXT = "#e8e8e8"
_MUTED = "#8a8a8a"

#: The tile choices from the mockup.
_COUNTS = [1, 2, 4, 6, 8, 10, 12]

_STEPS = ["Start", "Layout", "Agents"]

#: The AgentDeck mark, shipped beside this file (see assets/).
_ASSET_ICON = Path(__file__).resolve().parent / "assets" / "icon.ico"


# ---------------------------------------------------------------------------
# Step indicator
# ---------------------------------------------------------------------------

class _StepIndicator(QWidget):
    """The `1 Start ── 2 Layout ── 3 Agents` strip across the top."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._current = 0
        self.setFixedHeight(48)

    def set_current(self, index: int) -> None:
        self._current = index
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        n = len(_STEPS)
        slot = self.width() / n
        r = 12
        cy = 20

        p.setFont(self.font())
        for i, label in enumerate(_STEPS):
            cx = slot * i + slot / 2
            done = i < self._current
            current = i == self._current

            # connector to the next step
            if i < n - 1:
                x1 = cx + r + 6
                x2 = slot * (i + 1) + slot / 2 - r - 6
                p.setPen(QColor(_AMBER if done else _BORDER))
                p.drawLine(int(x1), cy, int(x2), cy)

            if done or current:
                p.setBrush(QColor(_AMBER) if done else QColor(_BG))
                p.setPen(QColor(_AMBER))
            else:
                p.setBrush(QColor(_BG))
                p.setPen(QColor(_BORDER))
            p.drawEllipse(int(cx - r), cy - r, r * 2, r * 2)

            p.setPen(QColor("#ffffff") if done else
                     QColor(_AMBER) if current else QColor(_MUTED))
            mark = "\u2713" if done else str(i + 1)
            p.drawText(int(cx - r), cy - r, r * 2, r * 2, Qt.AlignCenter, mark)

            p.setPen(QColor(_TEXT) if (done or current) else QColor(_MUTED))
            p.drawText(int(cx - slot / 2), cy + r + 2, int(slot), 18,
                       Qt.AlignHCenter | Qt.AlignVCenter, label)


# ---------------------------------------------------------------------------
# Count tile
# ---------------------------------------------------------------------------

class _CountTile(QFrame):
    """One layout choice: a mini dot-grid preview + the number."""

    clicked = Signal(int)

    def __init__(self, count: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.count = count
        self._selected = False
        self.setObjectName("countTile")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(78, 84)
        self.setCursor(Qt.PointingHandCursor)
        self._restyle()

    def set_selected(self, value: bool) -> None:
        if value != self._selected:
            self._selected = value
            self._restyle()

    def _restyle(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#countTile {{
                background: {_CARD_HI if self._selected else _CARD};
                border: 2px solid {_AMBER if self._selected else _BORDER};
                border-radius: 10px;
            }}
            """
        )
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.count)
            event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        rows, cols = grid_dims(self.count)
        area_w, area_h = 46, 40
        ox = (self.width() - area_w) / 2
        oy = 12
        dot = min(area_w / cols, area_h / rows) * 0.42

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_AMBER if self._selected else _MUTED))
        drawn = 0
        for rr in range(rows):
            for cc in range(cols):
                if drawn >= self.count:
                    break
                cx = ox + (cc + 0.5) * (area_w / cols)
                cy = oy + (rr + 0.5) * (area_h / rows)
                p.drawEllipse(int(cx - dot / 2), int(cy - dot / 2),
                              int(dot), int(dot))
                drawn += 1

        p.setPen(QColor(_TEXT if self._selected else _MUTED))
        f = p.font()
        f.setPointSize(9)
        f.setBold(self._selected)
        p.setFont(f)
        p.drawText(0, self.height() - 20, self.width(), 16,
                   Qt.AlignCenter, str(self.count))


# ---------------------------------------------------------------------------
# Agent card
# ---------------------------------------------------------------------------

class _AgentCard(QFrame):
    """Name + what it runs + an installed / not-installed pill. A not-installed
    agent unfolds an inline InstallHint when selected; 'Custom' carries a
    command field instead.
    """

    clicked = Signal(str)
    edited = Signal()
    rechecked = Signal(str, bool)   # (key, installed) after a Re-check

    def __init__(self, key: str, title: str, subtitle: str,
                 installed: bool = True, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.key = key
        self.installed = installed
        self._selected = False
        self.setObjectName("agentCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)

        self._row = QVBoxLayout(self)
        self._row.setContentsMargins(14, 10, 14, 10)
        self._row.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._dot = QLabel("\u25cb")
        self._dot.setObjectName("agentDot")
        top.addWidget(self._dot, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("agentTitle")
        s = QLabel(subtitle)
        s.setObjectName("agentSub")
        text.addWidget(t)
        text.addWidget(s)
        top.addLayout(text, 1)
        self._pill = QLabel("")
        self._pill.setObjectName("agentPill")
        top.addWidget(self._pill, 0, Qt.AlignTop)
        self._row.addLayout(top)

        self.field: Optional[QLineEdit] = None
        if key == CUSTOM_KEY:
            self.field = QLineEdit()
            self.field.setPlaceholderText("e.g.  aider --model sonnet")
            self.field.setVisible(False)
            self.field.textChanged.connect(lambda _t: self.edited.emit())
            self._row.addWidget(self.field)

        self._hint: Optional[InstallHint] = None   # built lazily
        self._refresh_pill()
        self._restyle()

    def _real_agent(self) -> bool:
        return self.key not in (PLAIN_KEY, CUSTOM_KEY)

    def _refresh_pill(self) -> None:
        show = self._real_agent()
        self._pill.setVisible(show)
        if show:
            self._pill.setText("✓ installed" if self.installed
                               else "not installed")

    def _on_rechecked(self, ok: bool) -> None:
        if ok:
            self.installed = True
            self._refresh_pill()
            if self._hint is not None:
                self._hint.setVisible(False)
            self._restyle()
        self.rechecked.emit(self.key, ok)

    def set_selected(self, value: bool) -> None:
        self._selected = value
        self._dot.setText("\u25c9" if value else "\u25cb")
        if self.field is not None:
            self.field.setVisible(value)
            if value:
                self.field.setFocus()
        if value and self._real_agent() and not self.installed:
            if self._hint is None:
                self._hint = InstallHint(self.key, accent=_AMBER)
                self._hint.rechecked.connect(self._on_rechecked)
                self._row.addWidget(self._hint)
            self._hint.setVisible(True)
        elif self._hint is not None:
            self._hint.setVisible(False)
        self._restyle()

    def _restyle(self) -> None:
        pill_bg = "#2f4030" if self.installed else "#3a2f2f"
        pill_fg = "#7fce7f" if self.installed else "#c99a9a"
        self.setStyleSheet(
            f"""
            QFrame#agentCard {{
                background: {_CARD_HI if self._selected else _CARD};
                border: 2px solid {_AMBER if self._selected else _BORDER};
                border-radius: 10px;
            }}
            QLabel#agentDot {{ color: {_AMBER if self._selected else _MUTED};
                               font-size: 15px; }}
            QLabel#agentTitle {{ color: {_TEXT}; font-size: 12px; font-weight: 600; }}
            QLabel#agentSub {{ color: {_MUTED}; font-size: 10px; }}
            QLabel#agentPill {{
                color: {pill_fg}; background: {pill_bg}; border-radius: 6px;
                padding: 1px 6px; font-size: 9px;
            }}
            QLineEdit {{ background: {_BG}; color: {_TEXT};
                        border: 1px solid {_BORDER}; border-radius: 6px;
                        padding: 5px 8px; font-size: 11px; }}
            """
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
            event.accept()


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class SetupWizard(QDialog):
    """Front-door dialog: choose a folder, a terminal count and an agent."""

    def __init__(self, config: Optional[dict] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config or {}

        self._folder = str(self._config.get("working_folder", "") or "") \
            or str(Path.home())
        self._count = max(1, min(MAX_PANES,
                                 int(self._config.get("default_count", 4))))
        self._agent_key = self._config.get("agent", PLAIN_KEY) or PLAIN_KEY
        self._recent = [
            f for f in self._config.get("recent_folders", []) if isinstance(f, str)
        ]
        self._result: Optional[dict] = None

        self.setWindowTitle("AgentDeck — set up your workspace")
        self.setModal(True)
        self.resize(820, 560)
        self.setStyleSheet(
            f"""
            QDialog {{ background: {_BG}; }}
            QLabel {{ color: {_TEXT}; }}
            QLabel#h1 {{ font-size: 21px; font-weight: 700; }}
            QLabel#sub {{ color: {_MUTED}; font-size: 12px; }}
            QLabel#section {{ font-size: 12px; font-weight: 700; }}
            QLabel#hint, QLabel#badge {{ color: {_MUTED}; font-size: 10px; }}
            QLabel#badge {{ color: {_AMBER}; font-weight: 700; }}
            QLineEdit#folder {{
                background: {_CARD}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 8px;
                padding: 9px 12px; font-size: 12px;
            }}
            QLineEdit#folder[bad="true"] {{ border-color: #a04040; }}
            QPushButton {{
                background: {_CARD}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 8px;
                padding: 8px 18px; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {_AMBER}; }}
            QPushButton#primary {{
                background: {_AMBER}; color: #1a1a1a; border-color: {_AMBER};
                font-weight: 700;
            }}
            QPushButton#primary:hover {{ background: {_AMBER_HI};
                                         border-color: {_AMBER_HI}; }}
            QPushButton#primary:disabled {{ background: #4a3a2c; color: #8a7a6a;
                                            border-color: #4a3a2c; }}
            QPushButton#link {{ background: transparent; border: none;
                                color: {_MUTED}; padding: 8px 6px; }}
            QPushButton#link:hover {{ color: {_AMBER}; }}
            QPushButton#iconbtn {{ padding: 8px 10px; font-size: 13px; }}
            QScrollArea {{ border: none; background: transparent; }}
            QWidget#pageScroll {{ background: transparent; }}

            QLabel#installHead {{
                color: {_MUTED}; font-size: 11px; padding: 10px 2px 2px 2px;
            }}
            QFrame#installRow {{
                background: {_CARD}; border: 1px solid {_BORDER};
                border-radius: 9px;
            }}
            QLabel#installName {{ color: {_TEXT}; font-size: 11px; font-weight: 600; }}
            QLabel#installTag {{
                color: #7a7a7a; background: #2a2a2a; border-radius: 6px;
                padding: 1px 6px; font-size: 9px;
            }}
            QLineEdit#installCmd {{
                background: {_BG}; color: #c8c8c8; font-family: Consolas, monospace;
                border: 1px solid {_BORDER}; border-radius: 6px;
                padding: 5px 8px; font-size: 10px;
            }}
            QPushButton#installCopy {{
                background: {_CARD_HI}; padding: 5px 8px; font-size: 10px;
                border-radius: 6px;
            }}
            QPushButton#installGuide {{
                background: transparent; border: 1px solid {_BORDER};
                color: {_AMBER}; padding: 3px 9px; font-size: 10px;
                border-radius: 6px;
            }}
            QPushButton#installGuide:hover {{ border-color: {_AMBER}; }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 20, 28, 20)
        outer.setSpacing(16)

        self._steps = _StepIndicator()
        outer.addWidget(self._steps)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_start())
        self._stack.addWidget(self._build_layout())
        self._stack.addWidget(self._build_agents())
        outer.addWidget(self._stack, 1)

        outer.addLayout(self._build_footer())

        self._agent_cards: list[_AgentCard] = []
        self._populate_agents()
        self._select_agent(self._agent_key)
        self._select_count(self._count)
        self._folder_edit.setText(self._folder)
        self._goto(0)

    # -- pages -------------------------------------------------------------

    def _build_start(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(10)

        if _ASSET_ICON.exists():
            mark = QLabel()
            mark.setPixmap(QIcon(str(_ASSET_ICON)).pixmap(72, 72))
            mark.setAlignment(Qt.AlignCenter)
            lay.addSpacing(6)
            lay.addWidget(mark)

        title = QLabel("AgentDeck")
        title.setObjectName("h1")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Every terminal, every agent, one deck. Let's set you up.")
        sub.setObjectName("sub")
        sub.setAlignment(Qt.AlignCenter)
        lay.addSpacing(10)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(18)

        if self._recent:
            rl = QLabel("Recent folders")
            rl.setObjectName("section")
            lay.addWidget(rl, 0, Qt.AlignHCenter)
            box = QVBoxLayout()
            box.setSpacing(6)
            for folder in self._recent[:5]:
                btn = QPushButton(f"  {Path(folder).name or folder}"
                                  f"      {folder}")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _c=False, f=folder: self._quick_launch(f))
                box.addWidget(btn)
            wrap = QWidget()
            wrap.setMaximumWidth(560)
            wrap.setLayout(box)
            lay.addWidget(wrap, 0, Qt.AlignHCenter)

        lay.addStretch(1)
        return page

    def _build_layout(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(8)

        h = QLabel("Set up your workspace")
        h.setObjectName("h1")
        h.setAlignment(Qt.AlignCenter)
        s = QLabel("Pick a folder to work in and choose how many terminals you want.")
        s.setObjectName("sub")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(h)
        lay.addWidget(s)
        lay.addSpacing(14)

        fl = QHBoxLayout()
        fl.setSpacing(8)
        wf = QLabel("Working folder")
        wf.setObjectName("section")
        fl.addWidget(wf)
        fh = QLabel("Where your terminals will start")
        fh.setObjectName("hint")
        fl.addWidget(fh)
        fl.addStretch(1)
        lay.addLayout(fl)

        row = QHBoxLayout()
        row.setSpacing(8)
        browse = QPushButton("\U0001F4C1")
        browse.setObjectName("iconbtn")
        browse.setToolTip("Browse for a folder")
        browse.clicked.connect(self._browse)
        self._folder_edit = QLineEdit()
        self._folder_edit.setObjectName("folder")
        self._folder_edit.setPlaceholderText("C:\\path\\to\\your\\project")
        self._folder_edit.textChanged.connect(self._on_folder_changed)
        self._folder_status = QLabel("")
        self._folder_status.setObjectName("hint")
        row.addWidget(browse)
        row.addWidget(self._folder_edit, 1)
        row.addWidget(self._folder_status)
        lay.addLayout(row)

        lay.addSpacing(18)

        tl = QHBoxLayout()
        htl = QLabel("How many terminals?")
        htl.setObjectName("section")
        tl.addWidget(htl)
        htlh = QLabel("Tap a tile to choose a layout")
        htlh.setObjectName("hint")
        tl.addWidget(htlh)
        tl.addStretch(1)
        self._count_badge = QLabel("")
        self._count_badge.setObjectName("badge")
        tl.addWidget(self._count_badge)
        lay.addLayout(tl)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self._tiles: list[_CountTile] = []
        for c in _COUNTS:
            tile = _CountTile(c)
            tile.clicked.connect(self._select_count)
            tiles.addWidget(tile)
            self._tiles.append(tile)
        tiles.addStretch(1)
        lay.addLayout(tiles)

        lay.addStretch(1)
        return page

    def _build_agents(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(8)

        h = QLabel("Choose an agent")
        h.setObjectName("h1")
        h.setAlignment(Qt.AlignCenter)
        s = QLabel("It runs in every terminal as soon as the workspace opens.")
        s.setObjectName("sub")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(h)
        lay.addWidget(s)
        lay.addSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        holder.setObjectName("pageScroll")
        self._agent_box = QVBoxLayout(holder)
        self._agent_box.setContentsMargins(0, 0, 6, 0)
        self._agent_box.setSpacing(8)
        self._agent_box.addStretch(1)
        scroll.setWidget(holder)
        lay.addWidget(scroll, 1)

        self._agent_note = QLabel("")
        self._agent_note.setObjectName("hint")
        lay.addWidget(self._agent_note)
        return page

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._back)
        self._skip_btn = QPushButton("Skip \u2014 use last setup")
        self._skip_btn.setObjectName("link")
        self._skip_btn.clicked.connect(self._skip)
        self._next_btn = QPushButton("Continue")
        self._next_btn.setObjectName("primary")
        self._next_btn.clicked.connect(self._next)

        version = QLabel(f"v{__version__}")
        version.setObjectName("hint")

        footer.addWidget(self._back_btn)
        footer.addWidget(self._skip_btn)
        footer.addWidget(version)
        footer.addStretch(1)
        footer.addWidget(self._next_btn)
        return footer

    # -- agents ----------------------------------------------------------------

    def _populate_agents(self) -> None:
        # Every known agent is a selectable card -- installed or not. A
        # not-installed card unfolds its InstallHint when selected.
        entries = [
            (k, lbl, f"Runs  {cmd}", ok) for k, lbl, cmd, ok in all_agents()
        ]
        entries.append(
            (PLAIN_KEY, "Plain shell", "Just open a shell \u2014 no agent", True))
        entries.append(
            (CUSTOM_KEY, "Custom command\u2026", "Run any command you like", True))

        for key, title, sub, ok in entries:
            card = _AgentCard(key, title, sub, installed=ok)
            card.clicked.connect(self._select_agent)
            card.edited.connect(self._refresh_agent_note)
            card.rechecked.connect(self._on_agent_rechecked)
            self._agent_box.insertWidget(self._agent_box.count() - 1, card)
            self._agent_cards.append(card)
            if key == CUSTOM_KEY and self._config.get("agent_command"):
                card.field.setText(str(self._config.get("agent_command")))

    def _agent_installed(self, key: str) -> bool:
        for card in self._agent_cards:
            if card.key == key:
                return card.installed
        return False

    def _on_agent_rechecked(self, key: str, ok: bool) -> None:
        # A Re-check that found the agent re-enables Launch.
        if ok and key == self._agent_key:
            self._refresh_agent_note()
            self._refresh_nav()

    def _select_agent(self, key: str) -> None:
        known = {c.key for c in self._agent_cards}
        self._agent_key = key if key in known else PLAIN_KEY
        for card in self._agent_cards:
            card.set_selected(card.key == self._agent_key)
        self._refresh_agent_note()
        self._refresh_nav()

    def _custom_command(self) -> str:
        for card in self._agent_cards:
            if card.key == CUSTOM_KEY and card.field is not None:
                return card.field.text().strip()
        return ""

    def _resolved_command(self) -> str:
        return resolve_agent(self._agent_key, self._custom_command())

    def _refresh_agent_note(self) -> None:
        folder = self._folder_edit.text().strip() or str(Path.home())
        name = Path(folder).name or folder
        real = self._agent_key not in (PLAIN_KEY, CUSTOM_KEY)
        if real and not self._agent_installed(self._agent_key):
            self._agent_note.setText(
                f"{agent_label(self._agent_key)} isn't installed yet — "
                f"follow the steps above, then Re-check."
            )
        elif self._resolved_command():
            self._agent_note.setText(
                f"Runs  {self._resolved_command()}  in all {self._count} "
                f"terminal(s) in  {name}"
            )
        else:
            self._agent_note.setText(
                f"Opens {self._count} plain shell(s) in  {name}"
            )
        self._refresh_nav()

    # -- count ---------------------------------------------------------------

    def _select_count(self, count: int) -> None:
        self._count = count
        for tile in getattr(self, "_tiles", []):
            tile.set_selected(tile.count == count)
        rows, cols = grid_dims(count)
        self._count_badge.setText(
            f"{count} terminal{'s' if count != 1 else ''}  \u00b7  {cols}\u00d7{rows} grid"
        )
        if hasattr(self, "_agent_note"):
            self._refresh_agent_note()

    # -- folder ------------------------------------------------------------

    def _browse(self) -> None:
        start = self._folder_edit.text().strip() or str(Path.home())
        picked = QFileDialog.getExistingDirectory(
            self, "Choose a working folder", start
        )
        if picked:
            self._folder_edit.setText(picked)

    def _on_folder_changed(self, _text: str) -> None:
        ok = self._folder_is_valid()
        self._folder_edit.setProperty("bad", "false" if ok else "true")
        self._folder_edit.style().unpolish(self._folder_edit)
        self._folder_edit.style().polish(self._folder_edit)
        self._folder_status.setText("\u2713" if ok else "not a folder")
        self._folder_status.setStyleSheet(
            f"color: {'#5fb35f' if ok else '#c07070'}; font-size: 11px;"
        )
        self._refresh_agent_note()

    def _folder_is_valid(self) -> bool:
        raw = self._folder_edit.text().strip()
        return bool(raw) and Path(os.path.expanduser(raw)).is_dir()

    # -- navigation ------------------------------------------------------------

    def _goto(self, index: int) -> None:
        index = max(0, min(self._stack.count() - 1, index))
        self._stack.setCurrentIndex(index)
        self._steps.set_current(index)
        self._back_btn.setVisible(index > 0)
        self._skip_btn.setVisible(index == 0)
        self._next_btn.setText("Launch" if index == 2 else "Continue")
        self._refresh_nav()

    def _refresh_nav(self) -> None:
        index = self._stack.currentIndex()
        if index == 1:
            self._next_btn.setEnabled(self._folder_is_valid())
        elif index == 2:
            real = self._agent_key not in (PLAIN_KEY, CUSTOM_KEY)
            ok = (
                self._folder_is_valid()
                and not (self._agent_key == CUSTOM_KEY and not self._custom_command())
                and not (real and not self._agent_installed(self._agent_key))
            )
            self._next_btn.setEnabled(ok)
        else:
            self._next_btn.setEnabled(True)

    def _next(self) -> None:
        index = self._stack.currentIndex()
        if index < 2:
            self._goto(index + 1)
        else:
            self._launch()

    def _back(self) -> None:
        self._goto(self._stack.currentIndex() - 1)

    def _skip(self) -> None:
        self._folder = (str(self._config.get("working_folder", "") or "")
                        or str(Path.home()))
        self._finish(
            self._config.get("agent", PLAIN_KEY) or PLAIN_KEY,
            str(self._config.get("agent_command", "") or ""),
        )

    def _quick_launch(self, folder: str) -> None:
        self._folder = folder
        self._finish(
            self._config.get("agent", PLAIN_KEY) or PLAIN_KEY,
            str(self._config.get("agent_command", "") or ""),
        )

    def _launch(self) -> None:
        self._folder = os.path.expanduser(self._folder_edit.text().strip())
        self._finish(self._agent_key, self._custom_command())

    def _finish(self, agent_key: str, custom: str) -> None:
        folder = self._folder or str(Path.home())
        self._result = {
            "folder": folder,
            "count": self._count,
            "agent_key": agent_key,
            "agent_command": resolve_agent(agent_key, custom),
            "agent_custom": custom,
        }
        self.accept()

    # -- result ----------------------------------------------------------------

    def choices(self) -> dict:
        """The picked setup, or last-known values if the dialog was rejected."""
        if self._result is not None:
            return dict(self._result)
        return {
            "folder": self._folder,
            "count": self._count,
            "agent_key": self._agent_key,
            "agent_command": self._resolved_command()
            if hasattr(self, "_agent_cards") else "",
            "agent_custom": self._custom_command()
            if hasattr(self, "_agent_cards") else "",
        }
