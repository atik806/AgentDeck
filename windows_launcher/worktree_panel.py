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
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRectF, QRunnable, QSize, Qt, QThreadPool, Signal
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
QListWidget#wtList::item {{
    /* rows carry their own padding inside _WorktreeRow; padding here would
       shrink the item widget's rect and clip the branch line. */
    border-radius: 7px; padding: 0; margin: 1px 0;
}}
QListWidget#wtFiles::item {{
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

#: How long a probe may stay in flight before :meth:`WorktreePanel._kick_probe`
#: stops treating it as a reason to skip the next one. One batch is ~13 ``git``
#: processes per worktree, each with its own 30s timeout, so a slow-but-healthy
#: probe can legitimately run for a while; past this it is stuck (a git that
#: never returned, a thread pool with nothing free), and without the escape
#: hatch the panel would never refresh again for the rest of the session.
_PROBE_STUCK_AFTER = 120.0


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


class _DiffSignals(QObject):
    done = Signal(int, str)  # generation, diff text (or an error message)


class _DiffTask(QRunnable):
    """Render one file's (or the whole worktree's) diff off the UI thread.

    ``diff_text`` can take real time on a large change -- up to its own 60s
    timeout -- and previously ran synchronously on every file-list click.
    """

    def __init__(self, generation: int, worktree_path: str, base: str, file_path: Optional[str]):
        super().__init__()
        self._gen = generation
        self._worktree_path = worktree_path
        self._base = base
        self._file_path = file_path
        self.signals = _DiffSignals()

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            text = gw.diff_text(self._worktree_path, self._base, path=self._file_path)
        except Exception as exc:  # noqa: BLE001 - a diff must never crash the pool
            # Not just GitError: the worktree folder can be discarded (or merged
            # away) between diff_text's isdir() check and git actually starting
            # in it, which surfaces as a raw OSError -- WinError 267,
            # NotADirectoryError -- from the spawn. An exception escaping a
            # QRunnable takes the whole app down, and the result is discarded by
            # the generation check anyway.
            text = f"(could not read diff: {exc})"
        try:
            self.signals.done.emit(self._gen, text or "(no changes against base)")
        except RuntimeError:
            pass  # the panel was torn down while we were diffing


class _WorktreeRow(QFrame):
    """One worktree in the list: workspace · branch · +/- · ahead/behind."""

    def __init__(self, rec: WorktreeRecord, st: Optional[gw.WorktreeStatus],
                 deltas: "list[gw.FileDelta]", parent: QWidget | None = None,
                 *, pending: bool = False):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
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
        if pending:
            bits.append("checking…")
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
    rebase_requested = Signal(str)         # record id
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
        # {rec_id: (WorktreeStatus|None, [FileDelta])} -- every entry is written
        # by a background probe; a missing key means "not probed yet", which the
        # rows render as "checking…" rather than blocking on git. Nothing in
        # this panel shells out to git on the UI thread (see reload()).
        self._probe_cache: dict = {}
        self._probe_gen = 0
        # {generation: task} for every probe still running. A single "latest
        # task" attribute let an older task's signal carrier be collected on the
        # UI thread while its worker was still inside run() emitting from it.
        self._probe_tasks: dict = {}
        self._probe_inflight_gen: Optional[int] = None
        self._probe_started = 0.0
        self._diff_gen = 0
        self._diff_tasks: dict = {}   # same, for in-flight diffs
        # The file list the detail pane was last built from, so a poll tick can
        # tell "nothing changed" (leave the user's selection and scroll alone)
        # from "the agent wrote something" (rebuild and re-diff).
        self._file_sig: "list | None" = None
        self._detail_pending = False

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

        self._rebase_btn = QPushButton("Rebase onto base")
        self._rebase_btn.setCursor(Qt.PointingHandCursor)
        self._rebase_btn.clicked.connect(lambda: self._emit(self.rebase_requested))
        actions.addWidget(self._rebase_btn)

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
        """Rebuild the list from the store and re-probe git in the background.

        Nothing on this path touches git on the UI thread. Probing one worktree
        costs ~13 ``git`` processes -- about 0.6 s each on Windows, and up to a
        30 s timeout when one of them hangs on a worktree an agent is busy in --
        so the old synchronous probe froze the whole window for seconds every
        time this panel was opened and after every merge / discard / rebase.
        Rows paint immediately from the last probe (or as "checking…") and fill
        in when the background probe lands.
        """
        self._populate()
        self._kick_probe(force=True)

    def _populate(self, *, keep_view: bool = False) -> None:
        """Rebuild the rows. ``keep_view`` = a refresh, not a (re)open: leave
        the user's file selection and diff scroll position alone."""
        records = self._store.active()
        keep = self._current_id
        live = {r.id for r in records}
        # Drop probes for records that are gone, so the cache can't grow without
        # bound across a long session of create / merge / discard.
        self._probe_cache = {k: v for k, v in self._probe_cache.items() if k in live}
        self._list.blockSignals(True)
        self._list.clear()
        for rec in records:
            probed = self._probe(rec)
            st, deltas = probed if probed is not None else (None, [])
            item = QListWidgetItem(self._list)
            item.setData(Qt.UserRole, rec.id)
            row = _WorktreeRow(rec, st, deltas, pending=probed is None)
            hint = row.sizeHint()
            # +2 for the item's 1px top/bottom margin, which the view takes out
            # of the widget's rect rather than adding around it.
            item.setSizeHint(QSize(hint.width(), hint.height() + 2))
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
        self._list.blockSignals(False)

        self.count_changed.emit(len(records))
        self._sync_visibility(records)
        if records:
            target = keep if any(r.id == keep for r in records) else records[0].id
            self._select_id(target, keep_view=keep_view and target == keep)
        else:
            self._current_id = None

    def refresh_status(self) -> None:
        """Re-probe git for the visible rows off the UI thread, then re-render.

        The panel host calls this on a throttled timer while the view is on
        screen, and the Refresh button calls it directly. Returns at once --
        the probe itself runs on the thread pool.
        """
        shown = [self._list.item(i).data(Qt.UserRole) for i in range(self._list.count())]
        if shown != [r.id for r in self._store.active()]:
            self._populate(keep_view=True)  # rows created / removed elsewhere
        self._kick_probe()

    def _kick_probe(self, *, force: bool = False) -> None:
        """Start a background probe of every live worktree.

        Skipped while one is still in flight -- the host polls every few seconds
        and a batch can outlast a tick -- unless ``force`` (an explicit reload,
        whose caller has just changed something the running probe can't know
        about). A probe that never reports back stops blocking new ones after
        ``_PROBE_STUCK_AFTER``.
        """
        if self._probe_inflight_gen is not None and not force:
            if time.monotonic() - self._probe_started < _PROBE_STUCK_AFTER:
                return
        specs = [(r.id, r.path, r.base_branch)
                 for r in self._store.active() if r.dir_exists]
        if not specs or not gw.git_available():
            self._probe_inflight_gen = None
            return
        self._probe_gen += 1
        self._probe_inflight_gen = self._probe_gen
        self._probe_started = time.monotonic()
        task = _ProbeTask(self._probe_gen, specs)
        task.signals.done.connect(self._on_probe_refreshed)
        # Hold a reference so the signal-carrier QObject outlives the run.
        self._probe_tasks[self._probe_gen] = task
        QThreadPool.globalInstance().start(task)

    def _on_probe_refreshed(self, generation: int, results: dict) -> None:
        self._probe_tasks.pop(generation, None)
        if generation == self._probe_inflight_gen:
            self._probe_inflight_gen = None
        if generation != self._probe_gen:
            return  # a newer reload / refresh superseded this one
        self._probe_cache = dict(results)
        self._populate(keep_view=True)

    def flush(self) -> None:
        """No editable state -- present for symmetry with the other panels."""

    # -- probing ------------------------------------------------------

    def _probe(self, rec: WorktreeRecord):
        """The last probe of ``rec`` -- ``(status, deltas)`` -- or ``None`` when
        the background probe hasn't reported on it yet.

        Deliberately a plain cache read: probing shells out to git a dozen
        times, which belongs on the thread pool and never on the UI thread
        (see :meth:`reload`). A worktree that was probed but is unreadable is
        cached as ``(None, [])``, so ``None`` unambiguously means "pending".
        """
        if not rec.dir_exists or not gw.git_available():
            return None, []  # nothing to wait for -- don't leave it "checking…"
        return self._probe_cache.get(rec.id)

    # -- selection --------------------------------------------------

    def _select_id(self, wid: Optional[str], *, keep_view: bool = False) -> None:
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.UserRole) == wid:
                # Block the row-changed signal so _load_detail runs once, not
                # twice (once from setCurrentRow, once from the explicit call).
                self._list.blockSignals(True)
                self._list.setCurrentRow(i)
                self._list.blockSignals(False)
                self._load_detail(wid, keep_view=keep_view)
                return
        self._current_id = None
        self._load_detail(None)

    def _on_row_changed(self, current: QListWidgetItem, _prev) -> None:
        self._load_detail(current.data(Qt.UserRole) if current is not None else None)

    def _load_detail(self, wid: Optional[str], *, keep_view: bool = False) -> None:
        """Show ``wid`` in the detail pane.

        ``keep_view`` marks a background refresh of the row that is already
        open, as opposed to the user picking a row. Such a refresh must not
        yank the view out from under them: when the worktree's files haven't
        moved it only re-reads the status line and buttons, and when they have
        it rebuilds the file list around whatever file the user had selected.
        """
        self._current_id = wid
        rec = self._store.get(wid) if wid else None
        if rec is None:
            self._head.setText("")
            self._status_line.setText("")
            self._files.clear()
            self._diff.setPlainText("")
            self._file_sig = None
            self._detail_pending = False
            for b in (self._merge_btn, self._rebase_btn, self._pr_btn, self._open_btn, self._discard_btn):
                b.setEnabled(False)
            return

        base = rec.base_branch or "base"
        self._head.setText(f"{rec.short_branch}   →   {base}")
        probed = self._probe(rec)
        pending = probed is None
        st, deltas = probed if probed is not None else (None, [])

        sig = [(d.status, d.path, d.added, d.removed) for d in deltas]
        if keep_view and sig == self._file_sig:
            pass  # a poll tick with nothing new -- don't touch the file list
        else:
            was_pending = self._detail_pending
            selected = self._selected_file()
            self._file_sig = sig
            self._files.blockSignals(True)
            self._files.clear()
            whole = QListWidgetItem("Whole diff")
            whole.setData(Qt.UserRole, _WHOLE_DIFF)
            self._files.addItem(whole)
            for d in deltas:
                it = QListWidgetItem(f"{d.status}  {d.display_path}   +{d.added} −{d.removed}")
                it.setData(Qt.UserRole, d.path)
                self._files.addItem(it)
            row = 0
            if keep_view and selected not in (None, _WHOLE_DIFF):
                row = next((i for i in range(self._files.count())
                            if self._files.item(i).data(Qt.UserRole) == selected), 0)
            # Block through setCurrentRow so _on_file_changed doesn't fire and
            # kick off a redundant diff task -- the explicit _render_diff below
            # is the single source of truth for the initial selection.
            self._files.setCurrentRow(row)
            self._files.blockSignals(False)
            # The first probe landing only *names* the files that the diff we
            # kicked off on open already covers, so rebuild the list around it
            # but leave the diff -- and the request in flight for it -- alone.
            if not (keep_view and was_pending and not pending):
                path = self._files.item(row).data(Qt.UserRole) if self._files.count() else _WHOLE_DIFF
                self._render_diff(rec, None if path == _WHOLE_DIFF else path)

        self._detail_pending = pending
        self._status_line.setText(self._status_text(rec, st, deltas, pending=pending))

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

        rebase_ok = merge_ok and st is not None and st.behind > 0
        self._rebase_btn.setEnabled(bool(rebase_ok))
        if not self._merge_enabled():
            self._rebase_btn.setToolTip("Rebasing is a Pro feature")
        elif not (st is not None and st.behind > 0):
            self._rebase_btn.setToolTip("Base hasn't moved — nothing to rebase onto")
        else:
            self._rebase_btn.setToolTip("")

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

    def _selected_file(self) -> Optional[str]:
        """The path the file list is on, ``_WHOLE_DIFF``, or ``None``."""
        item = self._files.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    @staticmethod
    def _status_text(rec, st, deltas, *, pending: bool = False) -> str:
        if rec.status == "orphaned":
            return "This worktree's folder is gone. Discard the record to tidy up."
        if st is None:
            return "Checking this worktree…" if pending else "Worktree not readable."
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
            self._diff_gen += 1  # drop any in-flight diff for a row we just left
            self._diff.setPlainText("(worktree folder is not available)")
            return
        self._diff_gen += 1
        self._diff.setPlainText("Loading diff…")
        task = _DiffTask(self._diff_gen, rec.path, rec.base_branch, path)
        task.signals.done.connect(self._on_diff_ready)
        # Hold a reference so the signal-carrier QObject outlives the run.
        self._diff_tasks[self._diff_gen] = task
        QThreadPool.globalInstance().start(task)

    def _on_diff_ready(self, generation: int, text: str) -> None:
        self._diff_tasks.pop(generation, None)
        if generation != self._diff_gen:
            return  # a newer file selection / reload superseded this diff
        self._diff.setPlainText(text)

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
