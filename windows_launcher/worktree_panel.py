"""The WORKTREES view -- review and merge the isolated worktrees agents work in.

When a workspace is opened with "Isolate each terminal in its own git worktree"
ticked, every pane gets its own ``git worktree`` on a scratch branch (see
``terminal_panel._create_worktrees_for_workspace``). This panel is where the
user looks at what each agent produced and decides what to do with it:

    Merge to <base>   ·   Open PR   ·   Open in a pane   ·   Discard

The panel is a **view**: it reads the store and calls the read-only
``git_worktree`` helpers directly (diff / status), but every mutation goes out
as a signal for ``TerminalPanel`` to carry out (it owns the panes, the GitHub
plugin and the entitlement checks).

Keep :func:`worktree_icon` -- the sidebar's "Worktrees" nav button reuses it.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import git_worktree as gw
import theme
from worktree_store import WorktreeRecord, WorktreeStore

__all__ = ["WorktreePanel", "worktree_icon"]


def worktree_icon(px: int = 16, color: Optional[str] = None) -> QIcon:
    """A drawn branch/merge glyph -- emoji renders broken in this Qt build
    (same reason :func:`routines_panel.routine_icon` is drawn)."""
    color = color or theme.color("sidebar_text")
    px = max(8, int(px))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    u = px / 16.0
    col = QColor(color)

    pen = p.pen()
    pen.setColor(col)
    pen.setWidthF(1.7 * u)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    # Left rail (the base branch) and a branch that curves off it and rejoins.
    path = QPainterPath()
    path.moveTo(4.0 * u, 2.5 * u)
    path.lineTo(4.0 * u, 13.5 * u)
    p.drawPath(path)

    curve = QPainterPath()
    curve.moveTo(4.0 * u, 6.0 * u)
    curve.cubicTo(4.0 * u, 9.5 * u, 12.0 * u, 6.5 * u, 12.0 * u, 10.0 * u)
    p.drawPath(curve)

    p.setPen(Qt.NoPen)
    p.setBrush(col)
    for cx, cy in ((4.0, 2.5), (4.0, 13.5), (12.0, 10.0)):
        p.drawEllipse(QRectF((cx - 1.7) * u, (cy - 1.7) * u, 3.4 * u, 3.4 * u))
    p.end()
    return QIcon(pm)


# --------------------------------------------------------------------------- #
# Diff highlighter
# --------------------------------------------------------------------------- #

class _DiffHighlighter(QSyntaxHighlighter):
    """Minimal unified-diff colouring for the read-only diff view."""

    def __init__(self, document):
        super().__init__(document)
        self._add = self._fmt(theme.color("green"))
        self._del = self._fmt(theme.color("red"))
        self._hunk = self._fmt(theme.color("text_muted"), bold=True)
        self._meta = self._fmt(theme.color("text_faint"))

    @staticmethod
    def _fmt(color: str, *, bold: bool = False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(QFont.Bold)
        return f

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt naming
        if text.startswith(("+++ ", "--- ", "diff --git", "index ", "new file", "deleted file", "rename ")):
            self.setFormat(0, len(text), self._meta)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self._hunk)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self._add)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self._del)


# --------------------------------------------------------------------------- #
# QSS
# --------------------------------------------------------------------------- #

def _qss() -> str:
    t = theme.color
    return f"""
QWidget#worktreePanel {{ background: {t('window_bg')}; }}
QLabel#wtTitle {{ color: {t('text')}; font-size: 20px; font-weight: 800; }}
QLabel#wtBody {{ color: {t('text_muted')}; font-size: 12px; }}
QLabel#wtEmpty {{ color: {t('text_muted')}; font-size: 13px; }}
QLabel#wtDetailHead {{ color: {t('text')}; font-size: 14px; font-weight: 700; }}
QLabel#wtStatusLine {{ color: {t('text_muted')}; font-size: 11px; }}

QListWidget#wtList, QListWidget#wtFiles {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 10px; padding: 4px;
    font-size: 12px; outline: none;
}}
QListWidget#wtList::item, QListWidget#wtFiles::item {{
    border-radius: 7px; padding: 6px 8px; margin: 1px 0;
}}
QListWidget#wtList::item:selected, QListWidget#wtFiles::item:selected {{
    background: {t('accent_soft_bg')};
}}
QListWidget#wtList::item:hover:!selected, QListWidget#wtFiles::item:hover:!selected {{
    background: {t('surface_hover')};
}}

QPlainTextEdit#wtDiff {{
    background: {t('surface')}; color: {t('text')};
    border: 1px solid {t('border')}; border-radius: 10px; padding: 8px;
    font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px;
}}

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
QPushButton#primary:disabled {{
    background: {t('accent_soft_bg')}; color: {t('text_faint')}; border-color: {t('accent_soft_bg')};
}}
QPushButton#danger {{ color: {t('danger')}; }}
QPushButton#danger:hover {{ border-color: {t('danger')}; }}
"""


_WHOLE_DIFF = "\x00whole"


class _ProbeSignals(QObject):
    done = Signal(int, dict)  # generation, {rec_id: (WorktreeStatus|None, [FileDelta])}


class _ProbeTask(QRunnable):
    """Probe ``git status`` + diff for a batch of worktrees off the UI thread.

    The Review panel's throttled poll would otherwise spawn ~6 ``git``
    processes per row synchronously every few seconds -- a visible stall on
    Windows once there are more than a couple of worktrees.
    """

    def __init__(self, generation: int, specs: "list[tuple[str, str, str]]"):
        super().__init__()
        self._gen = generation
        self._specs = specs
        self.signals = _ProbeSignals()

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        out: dict = {}
        for rid, path, base in self._specs:
            try:
                if path and os.path.isdir(path) and gw.git_available():
                    out[rid] = (gw.status(path, base), gw.diff_stat(path, base))
                else:
                    out[rid] = (None, [])
            except Exception:  # noqa: BLE001 - a probe must never crash the pool
                out[rid] = (None, [])
        try:
            self.signals.done.emit(self._gen, out)
        except RuntimeError:
            pass  # the panel was torn down while we were probing


class _WorktreeRow(QFrame):
    """One worktree in the list: workspace · branch · +/- · ahead/behind."""

    def __init__(self, rec: WorktreeRecord, st: Optional[gw.WorktreeStatus],
                 deltas: "list[gw.FileDelta]", parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(rec.workspace_name or "(worktree)")
        name.setStyleSheet(f"color: {theme.color('text')}; font-weight: 700; font-size: 12px;")
        top.addWidget(name, 1)
        tag = _status_tag(rec)
        if tag:
            lbl = QLabel(tag)
            lbl.setStyleSheet(f"color: {theme.color('text_faint')}; font-size: 10px;")
            top.addWidget(lbl, 0, Qt.AlignRight)
        lay.addLayout(top)

        added = sum(d.added for d in deltas)
        removed = sum(d.removed for d in deltas)
        bits = [rec.short_branch or "—"]
        if deltas:
            bits.append(f"{len(deltas)} file{'s' if len(deltas) != 1 else ''}  +{added} −{removed}")
        if st is not None:
            if st.ahead:
                bits.append(f"↑{st.ahead}")
            if st.behind:
                bits.append(f"↓{st.behind}")
            if st.dirty:
                bits.append("● uncommitted")
        sub = QLabel("   ·   ".join(bits))
        sub.setStyleSheet(f"color: {theme.color('text_muted')}; font-size: 11px;")
        lay.addWidget(sub)


def _status_tag(rec: WorktreeRecord) -> str:
    return {
        "detached": "kept",
        "merged": "merged",
        "orphaned": "missing",
        "pending_delete": "cleanup pending",
    }.get(rec.status, "")


class WorktreePanel(QWidget):
    """Full-area panel shown when the sidebar's "Worktrees" nav item is active."""

    count_changed = Signal(int)
    merge_requested = Signal(str)          # record id
    open_pr_requested = Signal(str)
    discard_requested = Signal(str)
    open_in_pane_requested = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        store: Optional[WorktreeStore] = None,
        config: Optional[dict] = None,
        repo_provider: Optional[Callable[[], object]] = None,
        github_connected: Optional[Callable[[], bool]] = None,
        merge_enabled: Optional[Callable[[], bool]] = None,
    ):
        super().__init__(parent)
        self._store = store if store is not None else WorktreeStore()
        self._config = config or {}
        self._repo_provider = repo_provider or (lambda: None)
        self._github_connected = github_connected or (lambda: False)
        self._merge_enabled = merge_enabled or (lambda: False)
        self._current_id: Optional[str] = None
        # {rec_id: (WorktreeStatus|None, [FileDelta])} -- populated synchronously
        # by reload() (an explicit open / post-mutation redraw) and refreshed
        # off-thread by refresh_status() (the panel host's throttled poll).
        self._probe_cache: dict = {}
        self._probe_gen = 0
        self._probe_task = None
        self._probe_inflight = False

        self.setObjectName("worktreePanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(14)

        title = QLabel("Worktrees")
        title.setObjectName("wtTitle")
        body = QLabel(
            "Isolated working copies your agents ran in — review the diff, then "
            "merge it back, open a PR, or throw it away."
        )
        body.setObjectName("wtBody")
        body.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(body)

        split = QHBoxLayout()
        split.setSpacing(16)
        outer.addLayout(split, 1)

        # -- left: the worktree list --
        left = QVBoxLayout()
        left.setSpacing(8)
        self._list = QListWidget()
        self._list.setObjectName("wtList")
        self._list.setFixedWidth(280)
        self._list.currentItemChanged.connect(self._on_row_changed)
        left.addWidget(self._list, 1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.refresh_status)
        left.addWidget(self._refresh_btn)
        split.addLayout(left)

        # -- right: detail / empty state --
        self._detail = self._build_detail()
        split.addWidget(self._detail, 1)

        self._empty = QLabel("")
        self._empty.setObjectName("wtEmpty")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        split.addWidget(self._empty, 1)

        self.reload()

    # -- detail construction ---------------------------------------------

    def _build_detail(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        self._head = QLabel("")
        self._head.setObjectName("wtDetailHead")
        v.addWidget(self._head)
        self._status_line = QLabel("")
        self._status_line.setObjectName("wtStatusLine")
        self._status_line.setWordWrap(True)
        v.addWidget(self._status_line)

        self._files = QListWidget()
        self._files.setObjectName("wtFiles")
        self._files.setFixedHeight(150)
        self._files.currentItemChanged.connect(self._on_file_changed)
        v.addWidget(self._files)

        self._diff = QPlainTextEdit()
        self._diff.setObjectName("wtDiff")
        self._diff.setReadOnly(True)
        self._diff.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._diff_hl = _DiffHighlighter(self._diff.document())
        v.addWidget(self._diff, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._merge_btn = QPushButton("Merge to base")
        self._merge_btn.setObjectName("primary")
        self._merge_btn.setCursor(Qt.PointingHandCursor)
        self._merge_btn.clicked.connect(lambda: self._emit(self.merge_requested))
        actions.addWidget(self._merge_btn)

        self._pr_btn = QPushButton("Open PR")
        self._pr_btn.setCursor(Qt.PointingHandCursor)
        self._pr_btn.clicked.connect(lambda: self._emit(self.open_pr_requested))
        actions.addWidget(self._pr_btn)

        self._open_btn = QPushButton("Open in pane")
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.clicked.connect(lambda: self._emit(self.open_in_pane_requested))
        actions.addWidget(self._open_btn)

        actions.addStretch(1)
        self._discard_btn = QPushButton("Discard")
        self._discard_btn.setObjectName("danger")
        self._discard_btn.setCursor(Qt.PointingHandCursor)
        self._discard_btn.clicked.connect(lambda: self._emit(self.discard_requested))
        actions.addWidget(self._discard_btn)
        v.addLayout(actions)

        return w

    def _emit(self, signal: Signal) -> None:
        if self._current_id:
            signal.emit(self._current_id)

    # -- data ----------------------------------------------------------

    def reload(self) -> None:
        """Re-read the store and rebuild the list, probing git synchronously.

        Called on an explicit open of the panel and after a mutation, where a
        one-off probe is fine and the data must be fresh. The recurring poll
        goes through :meth:`refresh_status` (off-thread) instead.
        """
        self._probe_cache = {}
        self._probe_gen += 1  # invalidate any in-flight background probe
        self._populate()

    def _populate(self) -> None:
        records = self._store.active()
        keep = self._current_id
        self._list.blockSignals(True)
        self._list.clear()
        for rec in records:
            st, deltas = self._probe(rec)
            item = QListWidgetItem(self._list)
            item.setData(Qt.UserRole, rec.id)
            row = _WorktreeRow(rec, st, deltas)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
        self._list.blockSignals(False)

        self.count_changed.emit(len(records))
        self._sync_visibility(records)
        if records:
            target = keep if any(r.id == keep for r in records) else records[0].id
            self._select_id(target)
        else:
            self._current_id = None

    def refresh_status(self) -> None:
        """Re-probe git for the visible rows off the UI thread, then re-render.

        The panel host calls this on a throttled timer while the view is on
        screen; a synchronous probe spawns ~6 ``git`` processes per row.
        """
        if self._probe_inflight:
            return  # a probe from the last tick is still running -- don't pile on
        records = self._store.active()
        specs = [(r.id, r.path, r.base_branch) for r in records if r.dir_exists]
        if not specs or not gw.git_available():
            self._populate()
            return
        self._probe_gen += 1
        self._probe_inflight = True
        task = _ProbeTask(self._probe_gen, specs)
        task.signals.done.connect(self._on_probe_refreshed)
        # Hold a reference so the signal-carrier QObject outlives the run.
        self._probe_task = task
        QThreadPool.globalInstance().start(task)

    def _on_probe_refreshed(self, generation: int, results: dict) -> None:
        self._probe_inflight = False
        if generation != self._probe_gen:
            return  # a newer reload / refresh superseded this one
        self._probe_cache = dict(results)
        self._populate()

    def flush(self) -> None:
        """No editable state -- present for symmetry with the other panels."""

    # -- probing ------------------------------------------------------

    def _probe(self, rec: WorktreeRecord):
        hit = self._probe_cache.get(rec.id)
        if hit is not None:
            return hit
        if not rec.dir_exists or not gw.git_available():
            return None, []
        try:
            result = (gw.status(rec.path, rec.base_branch),
                      gw.diff_stat(rec.path, rec.base_branch))
        except gw.GitError:
            return None, []
        self._probe_cache[rec.id] = result
        return result

    # -- selection --------------------------------------------------

    def _select_id(self, wid: Optional[str]) -> None:
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.UserRole) == wid:
                # Block the row-changed signal so _load_detail runs once, not
                # twice (once from setCurrentRow, once from the explicit call).
                self._list.blockSignals(True)
                self._list.setCurrentRow(i)
                self._list.blockSignals(False)
                self._load_detail(wid)
                return
        self._current_id = None
        self._load_detail(None)

    def _on_row_changed(self, current: QListWidgetItem, _prev) -> None:
        self._load_detail(current.data(Qt.UserRole) if current is not None else None)

    def _load_detail(self, wid: Optional[str]) -> None:
        self._current_id = wid
        rec = self._store.get(wid) if wid else None
        if rec is None:
            self._head.setText("")
            self._status_line.setText("")
            self._files.clear()
            self._diff.setPlainText("")
            for b in (self._merge_btn, self._pr_btn, self._open_btn, self._discard_btn):
                b.setEnabled(False)
            return

        base = rec.base_branch or "base"
        self._head.setText(f"{rec.short_branch}   →   {base}")
        st, deltas = self._probe(rec)

        self._files.blockSignals(True)
        self._files.clear()
        whole = QListWidgetItem("Whole diff")
        whole.setData(Qt.UserRole, _WHOLE_DIFF)
        self._files.addItem(whole)
        for d in deltas:
            it = QListWidgetItem(f"{d.status}  {d.display_path}   +{d.added} −{d.removed}")
            it.setData(Qt.UserRole, d.path)
            self._files.addItem(it)
        self._files.blockSignals(False)
        self._files.setCurrentRow(0)

        self._status_line.setText(self._status_text(rec, st, deltas))
        self._render_diff(rec, None)

        merge_ok = (
            self._merge_enabled()
            and rec.status in ("active", "detached")
            and rec.dir_exists
        )
        self._merge_btn.setEnabled(bool(merge_ok))
        if not self._merge_enabled():
            self._merge_btn.setToolTip("Merging is a Pro feature")
        else:
            self._merge_btn.setToolTip("")

        repo = self._repo_provider()
        slug = getattr(repo, "remote_slug", "") if repo is not None else ""
        pr_ok = bool(slug) and self._github_connected() and rec.dir_exists
        self._pr_btn.setEnabled(pr_ok)
        self._pr_btn.setToolTip(
            "" if pr_ok else "Connect GitHub (Plugins) and add an origin remote to open a PR"
        )
        self._open_btn.setEnabled(rec.dir_exists)
        self._discard_btn.setEnabled(rec.status in ("active", "detached", "orphaned"))
        if rec.pr_url:
            self._pr_btn.setText("View PR")
        else:
            self._pr_btn.setText("Open PR")

    @staticmethod
    def _status_text(rec, st, deltas) -> str:
        if rec.status == "orphaned":
            return "This worktree's folder is gone. Discard the record to tidy up."
        if st is None:
            return "Worktree not readable."
        added = sum(d.added for d in deltas)
        removed = sum(d.removed for d in deltas)
        parts = [f"{len(deltas)} file(s), +{added} −{removed}"]
        parts.append("clean" if not st.dirty else f"{st.dirty_files} uncommitted")
        if st.ahead:
            parts.append(f"{st.ahead} ahead")
        if st.behind:
            parts.append(f"{st.behind} behind — base moved")
        if rec.pr_url:
            parts.append(f"PR: {rec.pr_url}")
        return "  ·  ".join(parts)

    def _on_file_changed(self, current: QListWidgetItem, _prev) -> None:
        if current is None or not self._current_id:
            return
        rec = self._store.get(self._current_id)
        if rec is None:
            return
        which = current.data(Qt.UserRole)
        self._render_diff(rec, None if which == _WHOLE_DIFF else which)

    def _render_diff(self, rec: WorktreeRecord, path: Optional[str]) -> None:
        if not rec.dir_exists or not gw.git_available():
            self._diff.setPlainText("(worktree folder is not available)")
            return
        try:
            text = gw.diff_text(rec.path, rec.base_branch, path=path)
        except gw.GitError as exc:
            text = f"(could not read diff: {exc})"
        self._diff.setPlainText(text or "(no changes against base)")

    # -- visibility ------------------------------------------------

    def _sync_visibility(self, records) -> None:
        has = bool(records)
        self._detail.setVisible(has)
        self._empty.setVisible(not has)
        if not has:
            repo = self._repo_provider()
            if repo is None:
                self._empty.setText(
                    "The active workspace's folder isn't a git repository, so "
                    "there are no isolated worktrees.\n\nOpen a workspace from a "
                    "repo with “Isolate each terminal” ticked to use this."
                )
            else:
                self._empty.setText(
                    "No isolated worktrees yet.\n\nCreate a workspace with "
                    "“Isolate each terminal in its own git worktree” ticked — "
                    "each agent then works its own copy of the repo."
                )

    # -- lifecycle ---------------------------------------------

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)

    def apply_theme(self) -> None:
        self.setStyleSheet(_qss())
        self._diff_hl = _DiffHighlighter(self._diff.document())
