"""The SKILLS view -- a full-area panel the sidebar's nav strip swaps in.

A skill is a reusable ``SKILL.md`` instruction doc: a name, a one-line
"when to use this" description, and a Markdown body. Enabled skills are
materialized into the places each agent discovers instructions
(``skills_sync.materialize`` -- ``~/.claude/skills/`` for Claude Code, an
``AGENTS.md`` block for the rest).

List on the left (upload / new), an editor on the right (same split as
``routines_panel``), autosaved (debounced) to :class:`skills_store.SkillsStore`.
The editor's action bar can hand the skill to an agent to review and rewrite
("Improve with agent" -> ``improve_requested``), open its file, or export it.

Keep :func:`skill_icon` -- the sidebar's "Skills" nav button reuses it.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import theme
from agents import all_agents
from skills_store import Skill, SkillsStore, render_skill_markdown

__all__ = ["SkillsPanel", "skill_icon"]

#: Delay between the last edit and the autosave write.
_AUTOSAVE_MS = 600


def skill_icon(px: int = 16, color: Optional[str] = None) -> QIcon:
    """A drawn four-point "spark" over a page corner -- emoji renders broken in
    this Qt build (same reason :func:`notes_panel.note_icon` is drawn)."""
    color = color or theme.color("sidebar_text")
    px = max(8, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    u = px / 16.0
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))

    # A page.
    page = QPainterPath()
    page.addRoundedRect(QRectF(2.2 * u, 2.2 * u, 8.6 * u, 11.6 * u), 1.4 * u, 1.4 * u)
    inner = QPainterPath()
    inner.addRoundedRect(QRectF(3.6 * u, 3.6 * u, 5.8 * u, 8.8 * u), 0.8 * u, 0.8 * u)
    p.drawPath(page.subtracted(inner))

    # A four-point spark, bottom-right.
    cx, cy, r, w = 11.4 * u, 11.4 * u, 4.2 * u, 1.5 * u
    spark = QPainterPath()
    spark.moveTo(cx, cy - r)
    spark.quadTo(cx + w, cy - w, cx + r, cy)
    spark.quadTo(cx + w, cy + w, cx, cy + r)
    spark.quadTo(cx - w, cy + w, cx - r, cy)
    spark.quadTo(cx - w, cy - w, cx, cy - r)
    p.drawPath(spark)
    p.end()
    return QIcon(pm)


def _relative_time(ts: Optional[float]) -> str:
    if not ts:
        return "never"
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
QWidget#skillsPanel {{ background: {t('window_bg')}; }}
QLabel#skillsTitle {{ color: {t('text')}; font-size: 20px; font-weight: 800; }}
QLabel#skillsBody {{ color: {t('text_muted')}; font-size: 12px; }}
QLabel#skillsSaved {{ color: {t('text_faint')}; font-size: 11px; }}
QLabel#skillsEmpty {{ color: {t('text_muted')}; font-size: 13px; }}
QLabel#fieldLabel {{ color: {t('text_muted')}; font-size: 11px; font-weight: 700; }}
QLabel#slugLabel {{ color: {t('text_faint')}; font-size: 11px; }}

QListWidget#skillList {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 10px; padding: 4px;
    font-size: 12px; outline: none;
}}
QListWidget#skillList::item {{ border-radius: 7px; padding: 0; margin: 1px 0; }}
QListWidget#skillList::item:selected {{ background: {t('accent_soft_bg')}; }}
QListWidget#skillList::item:hover:!selected {{ background: {t('surface_hover')}; }}

QLabel#rowTitle {{ color: {t('text')}; font-size: 12px; font-weight: 700; }}
QLabel#rowTitleOff {{ color: {t('text_faint')}; font-size: 12px; font-weight: 700; }}
QLabel#rowDesc {{ color: {t('text_muted')}; font-size: 11px; }}
QLabel#rowMeta {{ color: {t('text_faint')}; font-size: 10px; }}

QLineEdit#skillName {{
    background: transparent; color: {t('text')};
    border: none; border-bottom: 1px solid {t('border')};
    padding: 6px 2px; font-size: 17px; font-weight: 700;
}}
QLineEdit#skillName:focus {{ border-bottom-color: {t('accent')}; }}
QLineEdit#skillDesc {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 7px; padding: 6px 8px; font-size: 12px;
}}
QLineEdit#skillDesc:focus {{ border-color: {t('accent')}; }}
QPlainTextEdit#skillBody {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 10px; padding: 10px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace; font-size: 12px;
}}
QPlainTextEdit#skillBody:focus {{ border-color: {t('accent')}; }}

QComboBox#improveAgent {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 7px; padding: 6px 8px; font-size: 12px;
}}
QComboBox#improveAgent:focus {{ border-color: {t('accent')}; }}
QComboBox#improveAgent::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {t('menu_bg')}; color: {t('text')};
    border: 1px solid {t('menu_border')};
    selection-background-color: {t('accent')}; selection-color: {t('on_accent')};
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
QPushButton#newSkill {{ font-weight: 700; }}
"""


# ---------------------------------------------------------------------------
# Skill list row
# ---------------------------------------------------------------------------

class _SkillRow(QFrame):
    """One skill in the list: name / description / where-it-goes · updated."""

    def __init__(self, skill: Skill, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self._title = QLabel(skill.display_name)
        self._title.setObjectName("rowTitle" if skill.enabled else "rowTitleOff")
        top.addWidget(self._title, 1)
        self._meta = QLabel(_relative_time(skill.updated))
        self._meta.setObjectName("rowMeta")
        top.addWidget(self._meta, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(top)

        self._desc = QLabel(self._desc_line(skill))
        self._desc.setObjectName("rowDesc")
        self._desc.setWordWrap(False)
        lay.addWidget(self._desc)

    @staticmethod
    def _desc_line(skill: Skill) -> str:
        desc = (skill.description or "").strip() or "No description"
        if len(desc) > 70:
            desc = desc[:69] + "…"
        state = "on" if skill.enabled else "off"
        return f"{desc}   ·   {state}"

    def update_from(self, skill: Skill) -> None:
        self._title.setText(skill.display_name)
        self._title.setObjectName("rowTitle" if skill.enabled else "rowTitleOff")
        self._title.style().unpolish(self._title)
        self._title.style().polish(self._title)
        self._desc.setText(self._desc_line(skill))
        self._meta.setText(_relative_time(skill.updated))


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class SkillsPanel(QWidget):
    """Full-area panel shown when the sidebar's "Skills" nav item is active."""

    count_changed = Signal(int)
    #: A skill was created / edited / deleted / imported -- re-materialize.
    changed = Signal()
    #: "Improve with agent" clicked -- review/rewrite this skill id with an agent.
    improve_requested = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        store: Optional[SkillsStore] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(parent)
        self._store = store if store is not None else SkillsStore()
        self._config = config if config is not None else {}
        self._current_id: Optional[str] = None
        self._dirty = False
        self._loading = False

        self.setObjectName("skillsPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(14)

        title = QLabel("Skills")
        title.setObjectName("skillsTitle")
        body = QLabel(
            "Reusable SKILL.md instructions. Enabled skills are wired into every "
            "agent — Claude Code discovers them natively, others via AGENTS.md — "
            "and an agent can review and rewrite one for you."
        )
        body.setObjectName("skillsBody")
        body.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(body)

        split = QHBoxLayout()
        split.setSpacing(16)
        outer.addLayout(split, 1)

        # -- left: new / upload + the list --
        left = QVBoxLayout()
        left.setSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._new_btn = QPushButton("+  New")
        self._new_btn.setObjectName("newSkill")
        self._new_btn.setCursor(Qt.PointingHandCursor)
        self._new_btn.clicked.connect(self._on_new)
        self._upload_btn = QPushButton("Upload SKILL.md…")
        self._upload_btn.setCursor(Qt.PointingHandCursor)
        self._upload_btn.clicked.connect(self._on_upload)
        btn_row.addWidget(self._new_btn)
        btn_row.addWidget(self._upload_btn, 1)
        left.addLayout(btn_row)

        self._list = QListWidget()
        self._list.setObjectName("skillList")
        self._list.setFixedWidth(270)
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.setUniformItemSizes(False)
        self._list.currentItemChanged.connect(self._on_row_changed)
        left.addWidget(self._list, 1)
        split.addLayout(left)

        # -- right: editor / empty state --
        self._editor = self._build_editor()
        split.addWidget(self._editor, 1)
        self._empty = QLabel("No skills yet — upload a SKILL.md or write one.")
        self._empty.setObjectName("skillsEmpty")
        self._empty.setAlignment(Qt.AlignCenter)
        split.addWidget(self._empty, 1)

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(_AUTOSAVE_MS)
        self._autosave.timeout.connect(self.flush)

        self.reload()

    # -- editor construction ---------------------------------------------

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
        self._name_edit.setObjectName("skillName")
        self._name_edit.setPlaceholderText("Skill name")
        self._name_edit.textEdited.connect(self._on_edited)
        top.addWidget(self._name_edit, 1)
        self._enabled_box = QCheckBox("Enabled")
        self._enabled_box.toggled.connect(self._on_edited)
        top.addWidget(self._enabled_box, 0, Qt.AlignVCenter)
        ed.addLayout(top)

        self._slug_label = QLabel("")
        self._slug_label.setObjectName("slugLabel")
        ed.addWidget(self._slug_label)

        ed.addWidget(self._label("When should the agent use this?"))
        self._desc_edit = QLineEdit()
        self._desc_edit.setObjectName("skillDesc")
        self._desc_edit.setPlaceholderText(
            "e.g.  Use when reviewing a pull request or a diff for correctness."
        )
        self._desc_edit.textEdited.connect(self._on_edited)
        ed.addWidget(self._desc_edit)

        ed.addWidget(self._label("Instructions (Markdown)"))
        self._body_edit = QPlainTextEdit()
        self._body_edit.setObjectName("skillBody")
        self._body_edit.setPlaceholderText(
            "What the agent should do when this skill applies. Keep it tight and "
            "imperative."
        )
        self._body_edit.textChanged.connect(self._on_edited)
        ed.addWidget(self._body_edit, 1)

        # -- action bar --
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._improve_combo = QComboBox()
        self._improve_combo.setObjectName("improveAgent")
        self._installed_agents: list[str] = []
        for key, label, command, ok in all_agents():
            if ok:
                self._installed_agents.append(key)
                self._improve_combo.addItem(label, key)
        if not self._installed_agents:
            self._improve_combo.addItem("no agent installed", "")
            self._improve_combo.setEnabled(False)
        self._improve_combo.currentIndexChanged.connect(self._remember_improve_agent)
        actions.addWidget(self._improve_combo, 0)

        self._improve_btn = QPushButton("Improve with agent")
        self._improve_btn.setToolTip(
            "Open a pane where this agent reviews the skill file and rewrites it "
            "in place. AgentDeck re-imports its edits."
        )
        self._improve_btn.setCursor(Qt.PointingHandCursor)
        self._improve_btn.clicked.connect(self._on_improve)
        actions.addWidget(self._improve_btn, 0)

        self._open_btn = QPushButton("Open file")
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.clicked.connect(self._on_open_file)
        actions.addWidget(self._open_btn, 0)

        self._export_btn = QPushButton("Export…")
        self._export_btn.setCursor(Qt.PointingHandCursor)
        self._export_btn.clicked.connect(self._on_export)
        actions.addWidget(self._export_btn, 0)
        actions.addStretch(1)
        ed.addLayout(actions)

        footer = QHBoxLayout()
        self._saved_label = QLabel("")
        self._saved_label.setObjectName("skillsSaved")
        footer.addWidget(self._saved_label, 1)
        self._delete_btn = QPushButton("Delete skill")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.clicked.connect(self._on_delete)
        footer.addWidget(self._delete_btn, 0, Qt.AlignRight)
        ed.addLayout(footer)

        # Seed the improve-agent picker from config.
        want = str(self._config.get("skills_improve_agent") or "")
        idx = self._improve_combo.findData(want)
        if idx >= 0:
            self._improve_combo.setCurrentIndex(idx)

        return editor

    # -- data ----------------------------------------------------------

    def reload(self) -> None:
        """Re-read the store and rebuild the list, keeping the selection if we
        can. Called each time the Skills view is shown."""
        self.flush()
        self._loading = True
        try:
            skills = self._store.load()
            self._list.clear()
            for skill in skills:
                item = QListWidgetItem(self._list)
                item.setData(Qt.UserRole, skill.id)
                row = _SkillRow(skill)
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
            target = self._current_id or (skills[0].id if skills else None)
            self._select_id(target)
        finally:
            self._loading = False
        self._sync_visibility(len(self._store))

    def refresh_list(self) -> None:
        """Cheap redraw of the list rows without disturbing the editor -- after
        an agent review re-imports a skill."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            skill = self._store.get(item.data(Qt.UserRole))
            widget = self._list.itemWidget(item)
            if skill is not None and isinstance(widget, _SkillRow):
                widget.update_from(skill)

    def reload_current_from_store(self) -> None:
        """The current skill changed on disk (an agent rewrote it) -- pull the
        new body into the editor unless the user has an unsaved edit going."""
        if self._current_id is None or self._dirty:
            self.refresh_list()
            return
        self._load_into_editor(self._current_id)
        self.refresh_list()

    def _select_id(self, skill_id: Optional[str]) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == skill_id:
                self._list.setCurrentItem(item)
                self._load_into_editor(skill_id)
                return
        self._current_id = None
        self._load_into_editor(None)

    def _load_into_editor(self, skill_id: Optional[str]) -> None:
        self._current_id = skill_id
        skill = self._store.get(skill_id) if skill_id else None
        self._loading = True
        try:
            self._name_edit.setText(skill.name if skill else "")
            self._enabled_box.setChecked(skill.enabled if skill else True)
            self._desc_edit.setText(skill.description if skill else "")
            self._body_edit.setPlainText(skill.body if skill else "")
            self._slug_label.setText(
                f"slug: {skill.slug}   ·   materialized as ~/.claude/skills/{skill.slug}/SKILL.md"
                if skill else ""
            )
        finally:
            self._loading = False
        self._dirty = False
        if skill:
            extra = f" · reviewed by {skill.last_reviewed_by}" if skill.last_reviewed_by else ""
            self._saved_label.setText(f"Saved {_relative_time(skill.updated)}{extra}")
        has = skill is not None
        for b in (self._delete_btn, self._improve_btn, self._open_btn, self._export_btn):
            b.setEnabled(has)
        if not self._installed_agents:
            self._improve_btn.setEnabled(False)

    # -- editing -----------------------------------------------------

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
        skill = self._store.update(
            self._current_id,
            name=self._name_edit.text().strip(),
            description=self._desc_edit.text().strip(),
            body=self._body_edit.toPlainText(),
            enabled=self._enabled_box.isChecked(),
            source="manual",
        )
        self._dirty = False
        if skill is not None:
            self._saved_label.setText(f"Saved {_relative_time(skill.updated)}")
            self._refresh_row(skill)
            self.changed.emit()

    def _refresh_row(self, skill: Skill) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == skill.id:
                widget = self._list.itemWidget(item)
                if isinstance(widget, _SkillRow):
                    widget.update_from(skill)
                    item.setSizeHint(widget.sizeHint())
                return

    # -- list / buttons -------------------------------------------

    def _on_row_changed(self, current: QListWidgetItem, _previous) -> None:
        if self._loading:
            return
        self.flush()
        skill_id = current.data(Qt.UserRole) if current is not None else None
        self._load_into_editor(skill_id)

    def _on_new(self) -> None:
        self.flush()
        skill = self._store.create(
            name="New skill",
            description="",
            body="",
            enabled=False,  # off until the user fills it in
            source="manual",
        )
        self._current_id = skill.id
        self.reload()
        self._name_edit.selectAll()
        self._name_edit.setFocus(Qt.OtherFocusReason)
        self.count_changed.emit(len(self._store))
        self.changed.emit()

    def _on_upload(self) -> None:
        self.flush()
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Upload SKILL.md file(s)", "", "Markdown (*.md *.markdown);;All files (*)"
        )
        created = 0
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            fallback = os.path.splitext(os.path.basename(path))[0]
            if fallback.lower() in ("skill", "readme"):
                fallback = os.path.basename(os.path.dirname(path)) or fallback
            skill = self._store.import_markdown(text, fallback_name=fallback)
            self._current_id = skill.id
            created += 1
        if created:
            self.reload()
            self.count_changed.emit(len(self._store))
            self.changed.emit()
            self._saved_label.setText(f"Imported {created} skill(s)")

    def _remember_improve_agent(self, *_a) -> None:
        key = self._improve_combo.currentData()
        if key:
            self._config["skills_improve_agent"] = key

    def _on_improve(self) -> None:
        if self._current_id is None or not self._installed_agents:
            return
        self.flush()
        self._remember_improve_agent()
        self.improve_requested.emit(self._current_id)

    def _on_open_file(self) -> None:
        skill = self._store.get(self._current_id) if self._current_id else None
        if skill is None:
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        import skills_sync

        path = skills_sync.working_copy_path(skill.slug)
        skills_sync.write_working_copies(self._store.all())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_export(self) -> None:
        skill = self._store.get(self._current_id) if self._current_id else None
        if skill is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export skill", f"{skill.slug}.md", "Markdown (*.md)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(render_skill_markdown(skill.name, skill.description, skill.body))
            self._saved_label.setText("Exported")
        except OSError as exc:
            self._saved_label.setText(f"Export failed: {exc}")

    def _on_delete(self) -> None:
        if self._current_id is None:
            return
        self._autosave.stop()
        self._dirty = False
        self._store.delete(self._current_id)
        self._current_id = None
        self.reload()
        self.count_changed.emit(len(self._store))
        self.changed.emit()

    def _sync_visibility(self, count: int) -> None:
        has = count > 0
        self._editor.setVisible(has)
        self._empty.setVisible(not has)

    # -- lifecycle ---------------------------------------------------

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.flush()
        super().hideEvent(event)

    def apply_theme(self) -> None:
        self.setStyleSheet(_qss())
