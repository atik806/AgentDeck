"""The NOTES view -- a full-area panel the sidebar's nav strip swaps in.

A small local notebook: a searchable list of notes on the left, a title + body
editor on the right. Edits autosave (debounced) to
:class:`notes_store.NotesStore`, so notes survive a restart. Notes are
machine-local -- deliberately not part of the cloud-synced settings.

Beyond plain text the panel can:

* **pin** a note to the top of the list,
* tag a note with a **colour** label (a stripe on its row),
* **search** the list by title/body,
* **copy** a note's body to the clipboard,
* **send** a note straight to the active terminal pane (great for reusable
  prompts / snippets -- ``send_to_terminal`` is wired in ``terminal_panel``),
* **duplicate** a note.

``Ctrl+N`` starts a note, ``Ctrl+F`` jumps to the search box.

Keep :func:`note_icon` -- the sidebar's "Notes" nav button reuses it.
"""

from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import theme
from notes_store import NOTE_COLORS, Note, NotesStore, derive_title

__all__ = ["NotesPanel", "note_icon"]

#: Delay between the last keystroke and the autosave write.
_AUTOSAVE_MS = 600

#: Order the colour swatches appear in the editor.
_COLOR_ORDER = ["", "blue", "green", "yellow", "peach", "mauve", "red"]


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


def _draw_icon(kind: str, px: int = 15, color: Optional[str] = None) -> QIcon:
    """Small monochrome glyphs for the editor's action buttons -- drawn for the
    same reason as :func:`note_icon` (emoji render broken here)."""
    c = QColor(color or theme.color("text_muted"))
    px = max(10, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    u = px / 16.0
    p.setBrush(Qt.NoBrush)
    pen = p.pen()
    pen.setColor(c)
    pen.setWidthF(1.5 * u)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)

    if kind == "pin":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        star = QPainterPath()
        cx, cy, outer, inner = 8 * u, 8 * u, 6.6 * u, 2.7 * u
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            r = outer if i % 2 == 0 else inner
            pt = (cx + r * math.cos(ang), cy + r * math.sin(ang))
            star.moveTo(*pt) if i == 0 else star.lineTo(*pt)
        star.closeSubpath()
        p.drawPath(star)
    elif kind == "copy":
        p.drawRoundedRect(QRectF(2.5 * u, 2.5 * u, 8 * u, 8 * u), 1.6 * u, 1.6 * u)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.color("window_bg")))
        p.drawRoundedRect(QRectF(5.5 * u, 5.5 * u, 8 * u, 8 * u), 1.6 * u, 1.6 * u)
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(5.5 * u, 5.5 * u, 8 * u, 8 * u), 1.6 * u, 1.6 * u)
    elif kind == "send":
        # a chevron pointing into a small terminal frame
        p.drawRoundedRect(QRectF(1.6 * u, 3 * u, 12.8 * u, 10 * u), 1.6 * u, 1.6 * u)
        arrow = QPainterPath()
        arrow.moveTo(5 * u, 6 * u)
        arrow.lineTo(8.2 * u, 8 * u)
        arrow.lineTo(5 * u, 10 * u)
        p.drawPath(arrow)
        p.drawLine(9 * u, 10 * u, 11.5 * u, 10 * u)
    elif kind == "duplicate":
        p.drawRoundedRect(QRectF(2 * u, 2 * u, 8 * u, 10 * u), 1.4 * u, 1.4 * u)
        p.drawRoundedRect(QRectF(6 * u, 4 * u, 8 * u, 10 * u), 1.4 * u, 1.4 * u)
    elif kind == "search":
        p.drawEllipse(QRectF(2.6 * u, 2.6 * u, 8 * u, 8 * u))
        p.drawLine(9.4 * u, 9.4 * u, 13 * u, 13 * u)
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


def _color_hex(key: str) -> str:
    return NOTE_COLORS.get(key or "", "") or ""


# ---------------------------------------------------------------------------
# QSS
# ---------------------------------------------------------------------------

def _qss() -> str:
    t = theme.color
    return f"""
QWidget#notesPanel {{ background: {t('window_bg')}; }}
QLabel#notesTitle {{ color: {t('text')}; font-size: 20px; font-weight: 800; }}
QLabel#notesCount {{
    color: {t('text_faint')}; font-size: 12px; font-weight: 700;
    padding: 1px 8px; border: 1px solid {t('border')}; border-radius: 9px;
}}
QLabel#notesBody {{ color: {t('text_muted')}; font-size: 12px; }}
QLabel#notesSaved {{ color: {t('text_faint')}; font-size: 11px; }}
QLabel#notesMeta {{ color: {t('text_faint')}; font-size: 11px; }}
QLabel#notesEmpty {{ color: {t('text_muted')}; font-size: 13px; }}
QLabel#swatchLabel {{ color: {t('text_faint')}; font-size: 11px; font-weight: 700; }}

QLineEdit#noteSearch {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 9px;
    padding: 7px 10px; font-size: 12px;
}}
QLineEdit#noteSearch:focus {{ border-color: {t('accent')}; }}

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
QLabel#rowPin {{ color: {t('accent')}; font-size: 11px; }}

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
    selection-background-color: {t('accent')}; selection-color: {t('on_accent')};
}}
QPlainTextEdit#noteBody:focus {{ border-color: {t('accent')}; }}

QToolButton#noteAction {{
    background: {t('surface')}; color: {t('text_muted')};
    border: 1px solid {t('border')}; border-radius: 7px;
    padding: 5px 10px; font-size: 11px; font-weight: 700;
}}
QToolButton#noteAction:hover {{ border-color: {t('accent')}; color: {t('text')}; }}
QToolButton#noteAction:checked {{
    background: {t('accent_soft_bg')}; border-color: {t('accent')}; color: {t('accent_text')};
}}
QToolButton#noteAction:disabled {{ color: {t('text_faint')}; border-color: {t('border')}; }}

QPushButton {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 7px; padding: 7px 14px; font-size: 12px;
}}
QPushButton:hover {{ border-color: {t('accent')}; }}
QPushButton:disabled {{ color: {t('text_faint')}; border-color: {t('border')}; }}
QPushButton#newNote {{ font-weight: 700; }}
QPushButton#danger {{ background: transparent; color: {t('danger')}; border: none; padding: 4px 2px; }}
QPushButton#danger:hover {{ color: {t('danger')}; text-decoration: underline; }}

QPushButton#swatch {{
    border-radius: 9px; padding: 0; min-width: 18px; max-width: 18px;
    min-height: 18px; max-height: 18px;
}}
"""


# ---------------------------------------------------------------------------
# Note list row
# ---------------------------------------------------------------------------

class _NoteRow(QFrame):
    """The widget shown for one note in the list: colour stripe · (pin) title
    / preview / time."""

    def __init__(self, note: Note, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._stripe = QFrame()
        self._stripe.setFixedWidth(3)
        outer.addWidget(self._stripe)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(2)
        outer.addWidget(body, 1)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(5)
        self._pin = QLabel()
        self._pin.setObjectName("rowPin")
        self._pin.setPixmap(_draw_icon("pin", 10, theme.color("accent")).pixmap(10, 10))
        top.addWidget(self._pin, 0, Qt.AlignVCenter)
        self._title = QLabel(note.display_title)
        self._title.setObjectName("rowTitle")
        top.addWidget(self._title, 1)
        self._time = QLabel(_relative_time(note.updated))
        self._time.setObjectName("rowTime")
        top.addWidget(self._time, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(top)

        self._preview = QLabel(note.preview or "No additional text")
        self._preview.setObjectName("rowPreview")
        lay.addWidget(self._preview)

        self.update_from(note)

    def update_from(self, note: Note) -> None:
        self._title.setText(note.display_title)
        self._preview.setText(note.preview or "No additional text")
        self._time.setText(_relative_time(note.updated))
        self._pin.setVisible(note.pinned)
        hexc = _color_hex(note.color)
        self._stripe.setStyleSheet(
            f"background: {hexc};" if hexc else "background: transparent;"
        )


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class NotesPanel(QWidget):
    """Full-area panel shown when the sidebar's "Notes" nav item is active."""

    #: The note count changed (create / delete). Carries the new count.
    count_changed = Signal(int)

    #: "Send to terminal" -- the current note's body. The panel emits it;
    #: :class:`terminal_panel.TerminalPanel` inserts it at the active pane.
    send_to_terminal = Signal(str)

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
        self._filter = ""

        self.setObjectName("notesPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("Notes")
        title.setObjectName("notesTitle")
        head.addWidget(title, 0, Qt.AlignVCenter)
        self._count_pill = QLabel("0")
        self._count_pill.setObjectName("notesCount")
        head.addWidget(self._count_pill, 0, Qt.AlignVCenter)
        head.addStretch(1)
        outer.addLayout(head)

        body = QLabel(
            "Scratch space that sticks around — prompts, checklists, snippets. "
            "Pin the ones you reach for, or send one straight to a terminal. "
            "Saved on this machine."
        )
        body.setObjectName("notesBody")
        body.setWordWrap(True)
        outer.addWidget(body)

        split = QHBoxLayout()
        split.setSpacing(16)
        outer.addLayout(split, 1)

        # -- left: search + list + new button --
        left = QVBoxLayout()
        left.setSpacing(8)

        self._new_btn = QPushButton("+  New note")
        self._new_btn.setObjectName("newNote")
        self._new_btn.setCursor(Qt.PointingHandCursor)
        self._new_btn.clicked.connect(self._on_new)
        left.addWidget(self._new_btn)

        self._search = QLineEdit()
        self._search.setObjectName("noteSearch")
        self._search.setPlaceholderText("Search notes")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(_draw_icon("search"), QLineEdit.LeadingPosition)
        self._search.textChanged.connect(self._on_search)
        left.addWidget(self._search)

        self._list = QListWidget()
        self._list.setObjectName("noteList")
        self._list.setFixedWidth(252)
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

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._pin_btn = self._action_button("pin", "Pin", checkable=True)
        self._pin_btn.toggled.connect(self._on_pin_toggled)
        self._copy_btn = self._action_button("copy", "Copy")
        self._copy_btn.clicked.connect(self._on_copy)
        self._send_btn = self._action_button("send", "Send to terminal")
        self._send_btn.clicked.connect(self._on_send)
        self._dup_btn = self._action_button("duplicate", "Duplicate")
        self._dup_btn.clicked.connect(self._on_duplicate)
        for b in (self._pin_btn, self._copy_btn, self._send_btn, self._dup_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        ed.addLayout(actions)

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

        swatches = QHBoxLayout()
        swatches.setSpacing(6)
        lbl = QLabel("Label")
        lbl.setObjectName("swatchLabel")
        swatches.addWidget(lbl)
        self._color_btns: dict[str, QPushButton] = {}
        for key in _COLOR_ORDER:
            b = QPushButton()
            b.setObjectName("swatch")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip("No label" if not key else key.capitalize())
            b.clicked.connect(lambda _c=False, k=key: self._on_color_pick(k))
            self._color_btns[key] = b
            swatches.addWidget(b)
        swatches.addStretch(1)
        ed.addLayout(swatches)

        footer = QHBoxLayout()
        self._saved_label = QLabel("")
        self._saved_label.setObjectName("notesSaved")
        footer.addWidget(self._saved_label, 0)
        self._meta_label = QLabel("")
        self._meta_label.setObjectName("notesMeta")
        footer.addWidget(self._meta_label, 1, Qt.AlignLeft)
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

        # Keep the "3m ago" stamps honest while the panel is open.
        self._tick = QTimer(self)
        self._tick.setInterval(60_000)
        self._tick.timeout.connect(self._refresh_times)
        self._tick.start()

        self._paint_swatches()

        sc_new = QShortcut(QKeySequence("Ctrl+N"), self)
        sc_new.activated.connect(self._on_new)
        sc_find = QShortcut(QKeySequence("Ctrl+F"), self)
        sc_find.activated.connect(
            lambda: self._search.setFocus(Qt.ShortcutFocusReason))

        self.reload()

    # -- small builders -------------------------------------------------

    def _action_button(self, icon: str, text: str, *, checkable: bool = False) -> QToolButton:
        b = QToolButton()
        b.setObjectName("noteAction")
        b.setText(f" {text}")
        b.setIcon(_draw_icon(icon))
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setCheckable(checkable)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _paint_swatches(self) -> None:
        for key, btn in self._color_btns.items():
            hexc = _color_hex(key)
            if hexc:
                btn.setStyleSheet(
                    f"QPushButton#swatch {{ background: {hexc}; border: 2px solid transparent; }}"
                    f"QPushButton#swatch:checked {{ border: 2px solid {theme.color('text')}; }}"
                )
            else:
                bd = theme.color("border")
                btn.setStyleSheet(
                    f"QPushButton#swatch {{ background: {theme.color('surface')}; border: 1px dashed {bd}; }}"
                    f"QPushButton#swatch:hover {{ border-color: {theme.color('accent')}; }}"
                    f"QPushButton#swatch:checked {{ border: 2px solid {theme.color('text')}; }}"
                )

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

            self._apply_filter()
            target = self._current_id or (notes[0].id if notes else None)
            if not self._is_visible_id(target):
                target = self._first_visible_id()
            self._select_id(target)
        finally:
            self._loading = False
        self._count_pill.setText(str(len(self._store)))
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
            self._pin_btn.setChecked(bool(note and note.pinned))
            self._pin_btn.setText(" Pinned" if (note and note.pinned) else " Pin")
            active = note.color if note else ""
            for key, btn in self._color_btns.items():
                btn.setChecked(key == active)
        finally:
            self._loading = False
        self._dirty = False
        for b in (self._pin_btn, self._copy_btn, self._send_btn, self._dup_btn,
                  self._delete_btn):
            b.setEnabled(note is not None)
        for btn in self._color_btns.values():
            btn.setEnabled(note is not None)
        if note:
            self._saved_label.setText(f"Edited {_relative_time(note.updated)}")
        else:
            self._saved_label.setText("")
        self._refresh_meta()

    # -- editing ------------------------------------------------------

    def _on_edited(self, *_a) -> None:
        if self._loading or self._current_id is None:
            return
        self._dirty = True
        self._saved_label.setText("Saving…")
        self._refresh_meta()
        self._autosave.start()

    def _refresh_meta(self) -> None:
        text = self._body_edit.toPlainText()
        words = len(text.split())
        chars = len(text)
        self._meta_label.setText(
            f"{words} word{'s' * (words != 1)} · {chars} char{'s' * (chars != 1)}"
            if self._current_id is not None else ""
        )

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
            # NB: the list is only re-sorted on reload() / pin toggle -- never
            # from here. flush() runs inside currentItemChanged (row switch) and
            # rebuilding a QListWidget from its own signal is a crash risk.

    def _refresh_row(self, note: Note) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == note.id:
                widget = self._list.itemWidget(item)
                if isinstance(widget, _NoteRow):
                    widget.update_from(note)
                    item.setSizeHint(widget.sizeHint())
                return

    def _refresh_times(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            note = self._store.get(item.data(Qt.UserRole))
            widget = self._list.itemWidget(item)
            if note is not None and isinstance(widget, _NoteRow):
                widget.update_from(note)
        if self._current_id is not None:
            note = self._store.get(self._current_id)
            if note is not None and not self._dirty:
                self._saved_label.setText(f"Edited {_relative_time(note.updated)}")

    def _resort_rows(self) -> None:
        """Reorder the list widget to match the store (pinned / recency) without
        rebuilding the row widgets -- keeps the selection and any pulse state."""
        want = [n.id for n in self._store.all()]
        have = [self._list.item(i).data(Qt.UserRole) for i in range(self._list.count())]
        if want == have:
            return
        cur = self._current_id
        self._loading = True
        try:
            self._list.clear()
            for note in self._store.all():
                item = QListWidgetItem(self._list)
                item.setData(Qt.UserRole, note.id)
                row = _NoteRow(note)
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
            self._apply_filter()
        finally:
            self._loading = False
        self._select_row_only(cur)

    def _select_row_only(self, note_id: Optional[str]) -> None:
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.UserRole) == note_id:
                self._loading = True
                self._list.setCurrentRow(i)
                self._loading = False
                return

    # -- search --------------------------------------------------------

    def _on_search(self, text: str) -> None:
        self._filter = text or ""
        self._apply_filter()
        if not self._is_visible_id(self._current_id):
            self.flush()
            self._select_id(self._first_visible_id())

    def _apply_filter(self) -> None:
        needle = self._filter.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            note = self._store.get(item.data(Qt.UserRole))
            item.setHidden(note is not None and not note.matches(needle))

    def _is_visible_id(self, note_id: Optional[str]) -> bool:
        if note_id is None:
            return False
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == note_id:
                return not item.isHidden()
        return False

    def _first_visible_id(self) -> Optional[str]:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if not item.isHidden():
                return item.data(Qt.UserRole)
        return None

    # -- list / buttons ------------------------------------------------

    def _on_row_changed(self, current: QListWidgetItem, _previous) -> None:
        if self._loading:
            return
        self.flush()
        note_id = current.data(Qt.UserRole) if current is not None else None
        self._load_into_editor(note_id)

    def _on_new(self) -> None:
        self.flush()
        if self._search.text():
            self._search.clear()  # so the fresh note is visible
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

    def _on_pin_toggled(self, checked: bool) -> None:
        if self._loading or self._current_id is None:
            return
        self.flush()
        note = self._store.update(self._current_id, pinned=checked)
        self._pin_btn.setText(" Pinned" if checked else " Pin")
        if note is not None:
            self._refresh_row(note)
            self._resort_rows()

    def _on_color_pick(self, key: str) -> None:
        if self._loading or self._current_id is None:
            return
        current = self._store.get(self._current_id)
        # Clicking the active swatch again clears the label.
        new = "" if (current is not None and current.color == key) else key
        self.flush()
        note = self._store.update(self._current_id, color=new)
        for k, btn in self._color_btns.items():
            btn.setChecked(k == new)
        if note is not None:
            self._refresh_row(note)

    def _on_copy(self) -> None:
        text = self._body_edit.toPlainText()
        if not text:
            return
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(text)
        self._saved_label.setText("Copied to clipboard")

    def _on_send(self) -> None:
        self.flush()
        text = self._body_edit.toPlainText().strip()
        if not text:
            self._saved_label.setText("Nothing to send")
            return
        self.send_to_terminal.emit(text)

    def _on_duplicate(self) -> None:
        if self._current_id is None:
            return
        self.flush()
        copy = self._store.duplicate(self._current_id)
        if copy is not None:
            self._current_id = copy.id
            self.reload()
            self.count_changed.emit(len(self._store))

    def _sync_visibility(self, count: int) -> None:
        has_notes = count > 0
        self._editor.setVisible(has_notes)
        self._empty.setVisible(not has_notes)

    # -- lifecycle -------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._tick.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._tick.stop()
        self.flush()
        super().hideEvent(event)

    def apply_theme(self) -> None:
        self.setStyleSheet(_qss())
        for icon, btn in (
            ("pin", self._pin_btn), ("copy", self._copy_btn),
            ("send", self._send_btn), ("duplicate", self._dup_btn),
        ):
            btn.setIcon(_draw_icon(icon))
        self._paint_swatches()
        if self._current_id is not None:
            active = (self._store.get(self._current_id) or Note(id="")).color
            for key, b in self._color_btns.items():
                b.setChecked(key == active)
