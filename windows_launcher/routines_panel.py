"""The ROUTINES view -- a full-area panel the sidebar's nav strip swaps in.

A routine is a scheduled agent prompt: "at 8:00 AM, open Claude in a new
workspace and tell it to ...". List on the left, an editor on the right (same
split as ``notes_panel.py``), autosaved (debounced) to
``routines_store.RoutinesStore``. Firing them is the panel's job to describe,
not to do -- see ``terminal_panel.TerminalPanel._run_routine`` for what
actually happens when one comes due (``routine_scheduler.RoutineScheduler``
decides *when*).

Keep :func:`routine_icon` -- the sidebar's "Routines" nav button reuses it.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import theme
from agents import CUSTOM_KEY, PLAIN_KEY, agent_label, all_agents, resolve_agent
from agents_ui import InstallHint
from routines_store import (
    DAY_ABBR,
    NEW_WORKSPACE,
    Routine,
    RoutinesStore,
    schedule_summary,
)

__all__ = ["RoutinesPanel", "routine_icon"]

#: Delay between the last edit and the autosave write.
_AUTOSAVE_MS = 600


def routine_icon(px: int = 16, color: Optional[str] = None) -> QIcon:
    """A drawn alarm-clock glyph -- emoji renders broken in this Qt build
    (same reason :func:`notes_panel.note_icon` is drawn)."""
    color = color or theme.color("sidebar_text")
    px = max(8, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    u = px / 16.0
    pen = QColor(color)
    p.setPen(Qt.NoPen)
    p.setBrush(pen)

    # Two feet.
    p.drawRoundedRect(QRectF(2.4 * u, 1.4 * u, 2.6 * u, 1.6 * u), 0.6 * u, 0.6 * u)
    p.drawRoundedRect(QRectF(11.0 * u, 1.4 * u, 2.6 * u, 1.6 * u), 0.6 * u, 0.6 * u)

    # Body ring.
    ring = QPainterPath()
    ring.addEllipse(QRectF(2.2 * u, 3.6 * u, 11.6 * u, 11.6 * u))
    inner = QPainterPath()
    inner.addEllipse(QRectF(3.6 * u, 5.0 * u, 8.8 * u, 8.8 * u))
    p.drawPath(ring.subtracted(inner))

    # Hands, meeting off-center to read as "8 o'clock".
    def _tick(x1, y1, x2, y2, w):
        path = QPainterPath()
        path.moveTo(QPointF(x1 * u, y1 * u))
        path.lineTo(QPointF(x2 * u, y2 * u))
        pen2 = p.pen()
        p.setPen(QColor(color))
        p.setBrush(Qt.NoBrush)
        old_width = pen2.widthF()
        pen2.setWidthF(w * u)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))

    _tick(8.0, 9.4, 8.0, 6.2, 1.2)
    _tick(8.0, 9.4, 10.4, 10.8, 1.2)
    p.end()
    return QIcon(pm)


def _relative_time(ts: Optional[float]) -> str:
    if not ts:
        return "Never run"
    delta = max(0.0, time.time() - float(ts))
    if delta < 45:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)}d ago"
    return time.strftime("%b %d", time.localtime(ts))


# ---------------------------------------------------------------------------
# QSS
# ---------------------------------------------------------------------------

def _qss() -> str:
    t = theme.color
    return f"""
QWidget#routinesPanel {{ background: {t('window_bg')}; }}
QLabel#routinesTitle {{ color: {t('text')}; font-size: 20px; font-weight: 800; }}
QLabel#routinesBody {{ color: {t('text_muted')}; font-size: 12px; }}
QLabel#routinesSaved {{ color: {t('text_faint')}; font-size: 11px; }}
QLabel#routinesEmpty {{ color: {t('text_muted')}; font-size: 13px; }}
QLabel#fieldLabel {{ color: {t('text_muted')}; font-size: 11px; font-weight: 700; }}

QListWidget#routineList {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 10px; padding: 4px;
    font-size: 12px; outline: none;
}}
QListWidget#routineList::item {{ border-radius: 7px; padding: 0; margin: 1px 0; }}
QListWidget#routineList::item:selected {{ background: {t('accent_soft_bg')}; }}
QListWidget#routineList::item:hover:!selected {{ background: {t('surface_hover')}; }}

QLabel#rowTitle {{ color: {t('text')}; font-size: 12px; font-weight: 700; }}
QLabel#rowTitleOff {{ color: {t('text_faint')}; font-size: 12px; font-weight: 700; }}
QLabel#rowSchedule {{ color: {t('text_muted')}; font-size: 11px; }}
QLabel#rowLast {{ color: {t('text_faint')}; font-size: 10px; }}

QLineEdit#routineName {{
    background: transparent; color: {t('text')};
    border: none; border-bottom: 1px solid {t('border')};
    padding: 6px 2px; font-size: 17px; font-weight: 700;
}}
QLineEdit#routineName:focus {{ border-bottom-color: {t('accent')}; }}
QPlainTextEdit#routinePrompt {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 10px; padding: 10px;
    font-size: 13px;
}}
QPlainTextEdit#routinePrompt:focus {{ border-color: {t('accent')}; }}

QComboBox, QLineEdit#customAgent, QTimeEdit {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 7px;
    padding: 6px 8px; font-size: 12px;
}}
QComboBox:focus, QLineEdit:focus, QTimeEdit:focus {{ border-color: {t('accent')}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {t('menu_bg')}; color: {t('text')};
    border: 1px solid {t('menu_border')};
    selection-background-color: {t('accent')}; selection-color: {t('on_accent')};
}}

QToolButton#dayBtn {{
    background: {t('surface')}; color: {t('text_muted')};
    border: 1px solid {t('border')}; border-radius: 6px;
    padding: 4px 0; font-size: 11px; min-width: 30px;
}}
QToolButton#dayBtn:hover {{ border-color: {t('border_hover')}; }}
QToolButton#dayBtn:checked {{
    background: {t('accent')}; color: {t('on_accent')}; border-color: {t('accent')};
    font-weight: 700;
}}

QCheckBox {{ color: {t('text')}; font-size: 12px; spacing: 8px; }}

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
QPushButton#danger {{ background: transparent; color: {t('danger')}; border: none; padding: 4px 2px; }}
QPushButton#danger:hover {{ color: {t('danger')}; text-decoration: underline; }}
QPushButton#newRoutine {{ font-weight: 700; }}
"""


# ---------------------------------------------------------------------------
# Routine list row
# ---------------------------------------------------------------------------

class _RoutineRow(QFrame):
    """One routine in the list: name / schedule / target · agent / last run."""

    def __init__(self, routine: Routine, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self._title = QLabel(routine.display_name)
        self._title.setObjectName("rowTitle" if routine.enabled else "rowTitleOff")
        top.addWidget(self._title, 1)
        self._last = QLabel(_relative_time(routine.last_run_at))
        self._last.setObjectName("rowLast")
        top.addWidget(self._last, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(top)

        self._schedule = QLabel(self._schedule_line(routine))
        self._schedule.setObjectName("rowSchedule")
        lay.addWidget(self._schedule)

    @staticmethod
    def _schedule_line(routine: Routine) -> str:
        if routine.workspace_target == NEW_WORKSPACE:
            target = (
                f"New: {routine.new_workspace_name}"
                if routine.new_workspace_name.strip()
                else "New workspace"
            )
        else:
            target = routine.workspace_target
        suffix = "" if routine.enabled else "  ·  disabled"
        return f"{schedule_summary(routine.days, routine.time)}  ·  {target}{suffix}"

    def update_from(self, routine: Routine) -> None:
        self._title.setText(routine.display_name)
        self._title.setObjectName("rowTitle" if routine.enabled else "rowTitleOff")
        # objectName-based QSS needs a style repolish to pick up the change.
        self._title.style().unpolish(self._title)
        self._title.style().polish(self._title)
        self._schedule.setText(self._schedule_line(routine))
        self._last.setText(_relative_time(routine.last_run_at))


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class RoutinesPanel(QWidget):
    """Full-area panel shown when the sidebar's "Routines" nav item is active."""

    count_changed = Signal(int)
    #: "Run now" clicked -- fire this routine id immediately, off-schedule.
    run_now = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        store: Optional[RoutinesStore] = None,
        config: Optional[dict] = None,
        workspaces_provider: Optional[Callable[[], "list[str]"]] = None,
    ):
        super().__init__(parent)
        self._store = store if store is not None else RoutinesStore()
        self._config = config or {}
        self._workspaces_provider = workspaces_provider or (lambda: [])
        self._current_id: Optional[str] = None
        self._dirty = False
        self._loading = False
        self._installed: dict[str, bool] = {}
        self._hint: Optional[InstallHint] = None
        self._hint_key = ""

        self.setObjectName("routinesPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(14)

        title = QLabel("Routines")
        title.setObjectName("routinesTitle")
        body = QLabel(
            "Scheduled agent prompts — set a time and AgentDeck opens the "
            "agent and sends it, while the app is running."
        )
        body.setObjectName("routinesBody")
        body.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(body)

        split = QHBoxLayout()
        split.setSpacing(16)
        outer.addLayout(split, 1)

        # -- left: the list + new button --
        left = QVBoxLayout()
        left.setSpacing(8)
        self._new_btn = QPushButton("+  New routine")
        self._new_btn.setObjectName("newRoutine")
        self._new_btn.setCursor(Qt.PointingHandCursor)
        self._new_btn.clicked.connect(self._on_new)
        left.addWidget(self._new_btn)

        self._list = QListWidget()
        self._list.setObjectName("routineList")
        self._list.setFixedWidth(260)
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.setUniformItemSizes(False)
        self._list.currentItemChanged.connect(self._on_row_changed)
        left.addWidget(self._list, 1)
        split.addLayout(left)

        # -- right: the editor (or the empty state) --
        self._editor = self._build_editor()
        split.addWidget(self._editor, 1)

        self._empty = QLabel("No routines yet — start one with “New routine”.")
        self._empty.setObjectName("routinesEmpty")
        self._empty.setAlignment(Qt.AlignCenter)
        split.addWidget(self._empty, 1)

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(_AUTOSAVE_MS)
        self._autosave.timeout.connect(self.flush)

        self.reload()

    # -- editor construction ------------------------------------------------

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    def _build_editor(self) -> QWidget:
        editor = QWidget()
        ed = QVBoxLayout(editor)
        ed.setContentsMargins(0, 0, 0, 0)
        ed.setSpacing(10)

        top = QHBoxLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setObjectName("routineName")
        self._name_edit.setPlaceholderText("Routine name")
        self._name_edit.textEdited.connect(self._on_edited)
        top.addWidget(self._name_edit, 1)
        self._enabled_box = QCheckBox("Enabled")
        self._enabled_box.toggled.connect(self._on_edited)
        top.addWidget(self._enabled_box, 0, Qt.AlignVCenter)
        ed.addLayout(top)

        ed.addWidget(self._label("Prompt"))
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setObjectName("routinePrompt")
        self._prompt_edit.setPlaceholderText("What should the agent do at this time?")
        self._prompt_edit.setFixedHeight(90)
        self._prompt_edit.textChanged.connect(self._on_edited)
        ed.addWidget(self._prompt_edit)

        row = QHBoxLayout()
        row.setSpacing(12)
        col1 = QVBoxLayout()
        col1.addWidget(self._label("Workspace"))
        self._ws_combo = QComboBox()
        self._ws_combo.currentIndexChanged.connect(self._on_workspace_changed)
        col1.addWidget(self._ws_combo)
        self._new_ws_name = QLineEdit()
        self._new_ws_name.setObjectName("customAgent")  # same compact input style
        self._new_ws_name.setPlaceholderText("Name for the new workspace (optional)")
        self._new_ws_name.textEdited.connect(self._on_edited)
        col1.addWidget(self._new_ws_name)
        row.addLayout(col1, 1)

        col2 = QVBoxLayout()
        col2.addWidget(self._label("Agent"))
        self._agent_combo = QComboBox()
        for key, label, command, ok in all_agents():
            self._installed[key] = ok
            suffix = "" if ok else "   ·  not installed"
            self._agent_combo.addItem(f"{label}  —  {command}{suffix}", key)
        self._agent_combo.addItem("Plain shell — no agent", PLAIN_KEY)
        self._agent_combo.addItem("Custom command…", CUSTOM_KEY)
        self._agent_combo.currentIndexChanged.connect(self._on_agent_changed)
        col2.addWidget(self._agent_combo)
        row.addLayout(col2, 1)
        ed.addLayout(row)

        self._custom_edit = QLineEdit()
        self._custom_edit.setObjectName("customAgent")
        self._custom_edit.setPlaceholderText("e.g.  aider --model sonnet")
        self._custom_edit.textEdited.connect(self._on_edited)
        ed.addWidget(self._custom_edit)

        self._hint_slot = QVBoxLayout()
        ed.addLayout(self._hint_slot)

        ed.addWidget(self._label("Runs at"))
        time_row = QHBoxLayout()
        time_row.setSpacing(10)
        self._time_edit = QTimeEdit()
        self._time_edit.setDisplayFormat("h:mm AP")
        self._time_edit.setTime(QTime(8, 0))
        self._time_edit.timeChanged.connect(self._on_edited)
        time_row.addWidget(self._time_edit)

        self._day_btns: list[QToolButton] = []
        for i, abbr in enumerate(DAY_ABBR):
            btn = QToolButton()
            btn.setObjectName("dayBtn")
            btn.setText(abbr[0])
            btn.setToolTip(abbr + " (unchecked = every day)")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            btn.toggled.connect(self._on_edited)
            time_row.addWidget(btn)
            self._day_btns.append(btn)
        time_row.addStretch(1)
        ed.addLayout(time_row)

        ed.addStretch(1)

        footer = QHBoxLayout()
        self._saved_label = QLabel("")
        self._saved_label.setObjectName("routinesSaved")
        footer.addWidget(self._saved_label, 1)
        self._run_btn = QPushButton("Run now")
        self._run_btn.setToolTip("Fire this routine immediately, ignoring its schedule")
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.clicked.connect(self._on_run_now)
        footer.addWidget(self._run_btn, 0, Qt.AlignRight)
        self._delete_btn = QPushButton("Delete routine")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.clicked.connect(self._on_delete)
        footer.addWidget(self._delete_btn, 0, Qt.AlignRight)
        ed.addLayout(footer)

        return editor

    # -- data ------------------------------------------------------------

    def reload(self) -> None:
        """Re-read the store and rebuild the list, keeping the selection if we
        can. Called by the panel each time the Routines view is shown."""
        self.flush()
        self._loading = True
        try:
            routines = self._store.load()
            self._list.clear()
            for routine in routines:
                item = QListWidgetItem(self._list)
                item.setData(Qt.UserRole, routine.id)
                row = _RoutineRow(routine)
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)

            target = self._current_id or (routines[0].id if routines else None)
            self._select_id(target)
        finally:
            self._loading = False
        self._sync_visibility(len(self._store))

    def refresh_list(self) -> None:
        """Cheap redraw of the list rows (schedule/last-run text) without
        disturbing the editor -- called after a routine fires."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            routine = self._store.get(item.data(Qt.UserRole))
            widget = self._list.itemWidget(item)
            if routine is not None and isinstance(widget, _RoutineRow):
                widget.update_from(routine)

    def _select_id(self, routine_id: Optional[str]) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == routine_id:
                self._list.setCurrentItem(item)
                self._load_into_editor(routine_id)
                return
        self._current_id = None
        self._load_into_editor(None)

    def _load_into_editor(self, routine_id: Optional[str]) -> None:
        self._current_id = routine_id
        routine = self._store.get(routine_id) if routine_id else None
        self._loading = True
        try:
            self._name_edit.setText(routine.name if routine else "")
            self._enabled_box.setChecked(routine.enabled if routine else True)
            self._prompt_edit.setPlainText(routine.prompt if routine else "")

            self._rebuild_workspace_combo(routine.workspace_target if routine else NEW_WORKSPACE)
            self._new_ws_name.setText(routine.new_workspace_name if routine else "")
            self._sync_new_ws_field()

            agent_key = routine.agent_key if routine else PLAIN_KEY
            idx = self._agent_combo.findData(agent_key)
            self._agent_combo.setCurrentIndex(idx if idx >= 0 else self._agent_combo.findData(PLAIN_KEY))
            self._custom_edit.setText(routine.agent_custom if routine else "")

            hh, mm = (routine.time if routine else "08:00").split(":")
            self._time_edit.setTime(QTime(int(hh), int(mm)))
            days = set(routine.days if routine else [])
            for i, btn in enumerate(self._day_btns):
                btn.setChecked(i in days)

            self._sync_agent_hint()
        finally:
            self._loading = False
        self._dirty = False
        if routine:
            self._saved_label.setText(f"Saved {_relative_time(routine.updated)}")
        self._delete_btn.setEnabled(routine is not None)
        self._run_btn.setEnabled(routine is not None)

    def _rebuild_workspace_combo(self, current_target: str) -> None:
        self._ws_combo.blockSignals(True)
        self._ws_combo.clear()
        self._ws_combo.addItem("New workspace", NEW_WORKSPACE)
        names = list(self._workspaces_provider())
        for name in names:
            self._ws_combo.addItem(name, name)
        if current_target != NEW_WORKSPACE and current_target not in names:
            self._ws_combo.addItem(f"{current_target}  (closed)", current_target)
        idx = self._ws_combo.findData(current_target)
        self._ws_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._ws_combo.blockSignals(False)

    # -- agent picker --------------------------------------------------------

    def _agent_key(self) -> str:
        return self._agent_combo.currentData()

    def _real_missing(self) -> bool:
        key = self._agent_key()
        return key not in (PLAIN_KEY, CUSTOM_KEY) and not self._installed.get(key, True)

    def _sync_agent_hint(self) -> None:
        key = self._agent_key()
        self._custom_edit.setVisible(key == CUSTOM_KEY)
        if self._real_missing():
            if self._hint is None or self._hint_key != key:
                if self._hint is not None:
                    self._hint.setParent(None)
                    self._hint.deleteLater()
                self._hint = InstallHint(key, accent=theme.color("accent"))
                self._hint_key = key
                self._hint.rechecked.connect(self._on_agent_rechecked)
                self._hint_slot.addWidget(self._hint)
            self._hint.setVisible(True)
        elif self._hint is not None:
            self._hint.setVisible(False)

    def _on_agent_rechecked(self, ok: bool) -> None:
        if ok:
            self._installed[self._hint_key] = True
        self._sync_agent_hint()

    def _on_agent_changed(self, *_a) -> None:
        if self._loading:
            return
        self._sync_agent_hint()
        self._on_edited()

    # -- editing ------------------------------------------------------

    def _sync_new_ws_field(self) -> None:
        """The 'name the new workspace' field only makes sense when the target
        is a new workspace, not an existing one."""
        self._new_ws_name.setVisible(self._ws_combo.currentData() == NEW_WORKSPACE)

    def _on_workspace_changed(self, *_a) -> None:
        self._sync_new_ws_field()  # runs during load too, so visibility tracks
        self._on_edited()

    def _on_edited(self, *_a) -> None:
        if self._loading or self._current_id is None:
            return
        self._dirty = True
        self._saved_label.setText("Saving…")
        self._autosave.start()

    def flush(self) -> None:
        """Persist the in-progress edit immediately (if any)."""
        self._autosave.stop()
        if not self._dirty or self._current_id is None:
            return
        days = sorted(i for i, btn in enumerate(self._day_btns) if btn.isChecked())
        routine = self._store.update(
            self._current_id,
            name=self._name_edit.text().strip(),
            prompt=self._prompt_edit.toPlainText(),
            enabled=self._enabled_box.isChecked(),
            workspace_target=self._ws_combo.currentData() or NEW_WORKSPACE,
            new_workspace_name=self._new_ws_name.text().strip(),
            agent_key=self._agent_key(),
            agent_custom=self._custom_edit.text().strip(),
            days=days,
            time=self._time_edit.time().toString("HH:mm"),
        )
        self._dirty = False
        if routine is not None:
            self._saved_label.setText(f"Saved {_relative_time(routine.updated)}")
            self._refresh_row(routine)

    def _refresh_row(self, routine: Routine) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == routine.id:
                widget = self._list.itemWidget(item)
                if isinstance(widget, _RoutineRow):
                    widget.update_from(routine)
                    item.setSizeHint(widget.sizeHint())
                return

    # -- list / buttons ------------------------------------------------

    def _on_row_changed(self, current: QListWidgetItem, _previous) -> None:
        if self._loading:
            return
        self.flush()
        routine_id = current.data(Qt.UserRole) if current is not None else None
        self._load_into_editor(routine_id)

    def _on_new(self) -> None:
        self.flush()
        routine = self._store.create(
            name="", prompt="", agent_key=PLAIN_KEY, agent_custom="",
            workspace_target=NEW_WORKSPACE, days=[], time="08:00", enabled=True,
        )
        self._current_id = routine.id
        self.reload()
        self._name_edit.setFocus(Qt.OtherFocusReason)
        self.count_changed.emit(len(self._store))

    def _on_run_now(self) -> None:
        if self._current_id is None:
            return
        self.flush()  # persist any in-flight edit so the fire uses it
        self.run_now.emit(self._current_id)

    def _on_delete(self) -> None:
        if self._current_id is None:
            return
        self._autosave.stop()
        self._dirty = False
        self._store.delete(self._current_id)
        self._current_id = None
        self.reload()
        self.count_changed.emit(len(self._store))

    def _sync_visibility(self, count: int) -> None:
        has_routines = count > 0
        self._editor.setVisible(has_routines)
        self._empty.setVisible(not has_routines)

    # -- lifecycle -------------------------------------------------------

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.flush()
        super().hideEvent(event)

    def apply_theme(self) -> None:
        self.setStyleSheet(_qss())
