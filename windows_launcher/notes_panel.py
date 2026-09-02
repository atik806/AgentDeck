"""The NOTES view -- a full-area panel the sidebar's nav strip swaps in.

A small local notebook: a list of notes on the left, a title + body editor on
the right. Edits autosave (debounced) to :class:`notes_store.NotesStore`, so
notes survive a restart. Notes are machine-local -- deliberately not part of the
cloud-synced settings.

Keep :func:`note_icon` -- the sidebar's "Notes" nav button reuses it.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
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
from notes_store import Note, NotesStore, derive_title

__all__ = ["NotesPanel", "note_icon"]

#: Delay between the last keystroke and the autosave write.
_AUTOSAVE_MS = 600


def note_icon(px: int = 16, color: Optional[str] = None) -> QIcon:
    """A drawn note page with ruled lines -- an emoji glyph renders broken in
    this Qt build (same reason :func:`plugins_panel.plugin_icon` is drawn)."""
    color = color or theme.color("sidebar_text")
    px = max(8, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    u = px / 16.0

    page = QPainterPath()
    page.addRoundedRect(QRectF(3 * u, 1.6 * u, 10 * u, 12.8 * u), 1.6 * u, 1.6 * u)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(page)

    # Punch three ruled lines out of the page.
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    for i in range(3):
        y = (4.4 + i * 2.6) * u
        p.drawRoundedRect(QRectF(5 * u, y, 6 * u, 1.1 * u), 0.5 * u, 0.5 * u)
    p.end()
    return QIcon(pm)


def _relative_time(ts: float) -> str:
    """A compact 'edited 3m ago' style stamp."""
    delta = max(0.0, time.time() - float(ts or 0))
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
QWidget#notesPanel {{ background: {t('window_bg')}; }}
QLabel#notesTitle {{ color: {t('text')}; font-size: 20px; font-weight: 800; }}
QLabel#notesBody {{ color: {t('text_muted')}; font-size: 12px; }}
QLabel#notesSaved {{ color: {t('text_faint')}; font-size: 11px; }}
QLabel#notesEmpty {{ color: {t('text_muted')}; font-size: 13px; }}

QListWidget#noteList {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 10px; padding: 4px;
    font-size: 12px; outline: none;
}}
QListWidget#noteList::item {{
    border-radius: 7px; padding: 0; margin: 1px 0;
}}
QListWidget#noteList::item:selected {{ background: {t('accent_soft_bg')}; }}
QListWidget#noteList::item:hover:!selected {{ background: {t('surface_hover')}; }}

QLabel#rowTitle {{ color: {t('text')}; font-size: 12px; font-weight: 700; }}
QLabel#rowPreview {{ color: {t('text_muted')}; font-size: 11px; }}
QLabel#rowTime {{ color: {t('text_faint')}; font-size: 10px; }}

QLineEdit#noteTitle {{
    background: transparent; color: {t('text')};
    border: none; border-bottom: 1px solid {t('border')};
    padding: 6px 2px; font-size: 17px; font-weight: 700;
}}
QLineEdit#noteTitle:focus {{ border-bottom-color: {t('accent')}; }}
QPlainTextEdit#noteBody {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 10px; padding: 10px;
    font-size: 13px;
}}
QPlainTextEdit#noteBody:focus {{ border-color: {t('accent')}; }}

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
QPushButton#newNote {{ font-weight: 700; }}
"""


# ---------------------------------------------------------------------------
# Note list row
# ---------------------------------------------------------------------------

class _NoteRow(QFrame):
    """The widget shown for one note in the list (title / preview / time)."""

    def __init__(self, note: Note, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(2)

        self._title = QLabel(note.display_title)
        self._title.setObjectName("rowTitle")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self._title, 1)
        self._time = QLabel(_relative_time(note.updated))
        self._time.setObjectName("rowTime")
        top.addWidget(self._time, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(top)

        self._preview = QLabel(note.preview or "No additional text")
        self._preview.setObjectName("rowPreview")
        lay.addWidget(self._preview)

    def update_from(self, note: Note) -> None:
        self._title.setText(note.display_title)
        self._preview.setText(note.preview or "No additional text")
        self._time.setText(_relative_time(note.updated))


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class NotesPanel(QWidget):
    """Full-area panel shown when the sidebar's "Notes" nav item is active."""

    #: The note count changed (create / delete). Carries the new count.
    count_changed = Signal(int)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        store: Optional[NotesStore] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(parent)
        self._store = store if store is not None else NotesStore()
        self._config = config or {}
        self._current_id: Optional[str] = None
        self._dirty = False
        self._loading = False

        self.setObjectName("notesPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(14)

        title = QLabel("Notes")
        title.setObjectName("notesTitle")
        body = QLabel(
            "Scratch space that sticks around — prompts, checklists, snippets. "
            "Saved on this machine."
        )
        body.setObjectName("notesBody")
        outer.addWidget(title)
        outer.addWidget(body)

        split = QHBoxLayout()
        split.setSpacing(16)
        outer.addLayout(split, 1)

        # -- left: the list + new button --
        left = QVBoxLayout()
        left.setSpacing(8)
        self._new_btn = QPushButton("+  New note")
        self._new_btn.setObjectName("newNote")
        self._new_btn.setCursor(Qt.PointingHandCursor)
        self._new_btn.clicked.connect(self._on_new)
        left.addWidget(self._new_btn)

        self._list = QListWidget()
        self._list.setObjectName("noteList")
        self._list.setFixedWidth(248)
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.setUniformItemSizes(False)
        self._list.currentItemChanged.connect(self._on_row_changed)
        left.addWidget(self._list, 1)
        split.addLayout(left)

        # -- right: the editor (or the empty state) --
        self._editor = QWidget()
        ed = QVBoxLayout(self._editor)
        ed.setContentsMargins(0, 0, 0, 0)
        ed.setSpacing(10)

        self._title_edit = QLineEdit()
        self._title_edit.setObjectName("noteTitle")
        self._title_edit.setPlaceholderText("Title")
        self._title_edit.textEdited.connect(self._on_edited)
        ed.addWidget(self._title_edit)

        self._body_edit = QPlainTextEdit()
        self._body_edit.setObjectName("noteBody")
        self._body_edit.setPlaceholderText("Start typing…")
        self._body_edit.textChanged.connect(self._on_edited)
        ed.addWidget(self._body_edit, 1)

        footer = QHBoxLayout()
        self._saved_label = QLabel("")
        self._saved_label.setObjectName("notesSaved")
        footer.addWidget(self._saved_label, 1)
        self._delete_btn = QPushButton("Delete note")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.clicked.connect(self._on_delete)
        footer.addWidget(self._delete_btn, 0, Qt.AlignRight)
        ed.addLayout(footer)
        split.addWidget(self._editor, 1)

        self._empty = QLabel("No notes yet — start one with “New note”.")
        self._empty.setObjectName("notesEmpty")
        self._empty.setAlignment(Qt.AlignCenter)
        split.addWidget(self._empty, 1)

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(_AUTOSAVE_MS)
        self._autosave.timeout.connect(self.flush)

        self.reload()

    # -- data ------------------------------------------------------------

    def reload(self) -> None:
        """Re-read the store and rebuild the list, keeping the selection if we
        can. Called by the panel each time the Notes view is shown."""
        self.flush()
        self._loading = True
        try:
            notes = self._store.load()
            self._list.clear()
            for note in notes:
                item = QListWidgetItem(self._list)
                item.setData(Qt.UserRole, note.id)
                row = _NoteRow(note)
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)

            target = self._current_id or (notes[0].id if notes else None)
            self._select_id(target)
        finally:
            self._loading = False
        self._sync_visibility(len(self._store))

    def _select_id(self, note_id: Optional[str]) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == note_id:
                self._list.setCurrentItem(item)
                self._load_into_editor(note_id)
                return
        self._current_id = None
        self._load_into_editor(None)

    def _load_into_editor(self, note_id: Optional[str]) -> None:
        self._current_id = note_id
        note = self._store.get(note_id) if note_id else None
        self._loading = True
        try:
            self._title_edit.setText(note.title if note else "")
            self._body_edit.setPlainText(note.body if note else "")
        finally:
            self._loading = False
        self._dirty = False
        if note:
            self._saved_label.setText(f"Edited {_relative_time(note.updated)}")

    # -- editing ------------------------------------------------------

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
        note = self._store.update(
            self._current_id,
            body=self._body_edit.toPlainText(),
            title=self._title_edit.text().strip(),
        )
        self._dirty = False
        if note is not None:
            self._saved_label.setText(f"Saved {_relative_time(note.updated)}")
            self._refresh_row(note)

    def _refresh_row(self, note: Note) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == note.id:
                widget = self._list.itemWidget(item)
                if isinstance(widget, _NoteRow):
                    widget.update_from(note)
                    item.setSizeHint(widget.sizeHint())
                return

    # -- list / buttons ------------------------------------------------

    def _on_row_changed(self, current: QListWidgetItem, _previous) -> None:
        if self._loading:
            return
        self.flush()
        note_id = current.data(Qt.UserRole) if current is not None else None
        self._load_into_editor(note_id)

    def _on_new(self) -> None:
        self.flush()
        note = self._store.create()
        self._current_id = note.id
        self.reload()
        self._title_edit.setFocus(Qt.OtherFocusReason)
        self.count_changed.emit(len(self._store))

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
        has_notes = count > 0
        self._editor.setVisible(has_notes)
        self._empty.setVisible(not has_notes)

    # -- lifecycle -------------------------------------------------------

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.flush()
        super().hideEvent(event)

    def apply_theme(self) -> None:
        self.setStyleSheet(_qss())
