"""Pick a repo + PR and the review options, for the GitHub review capability.

On accept, :meth:`result_payload` returns a plain dict the panel forwards to
``terminal_panel`` as ``review_ready`` -- which spawns a pane running the review
agent with the GitHub MCP server injected and the review brief as its task.

Blue accent, like ``new_workspace_dialog`` / ``account_dialog`` -- an in-app
action, not the front door.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

import theme

try:
    from github_api import list_open_prs, parse_pr_url
except Exception:  # noqa: BLE001
    list_open_prs = None  # type: ignore

    def parse_pr_url(url):  # type: ignore
        return None

__all__ = ["GitHubReviewDialog"]

_FOCI = [("bugs", "Bugs"), ("security", "Security"), ("tests", "Tests"),
         ("style", "Style"), ("perf", "Performance")]


class _PrFetch(QThread):
    """One open-PR listing, off the GUI thread.

    Both calls it makes block: ``_valid_token_blocking`` for up to 6s (it may
    refresh over the network) and ``list_open_prs`` for up to 20s. On the GUI
    thread that freezes the dialog; the repo box is editable, so it would do so
    once per keystroke.
    """

    ready = Signal(str, list)   # repo, [pr, ...]

    def __init__(self, github, repo: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._gh = github
        self._repo = repo

    def run(self) -> None:
        prs: list = []
        try:
            token = self._gh._valid_token_blocking()  # noqa: SLF001 - controller helper
        except Exception:  # noqa: BLE001
            token = None
        if token and list_open_prs is not None and not self.isInterruptionRequested():
            try:
                prs = list_open_prs(token, self._repo)
            except Exception:  # noqa: BLE001
                prs = []
        if not self.isInterruptionRequested():
            self.ready.emit(self._repo, prs)


class GitHubReviewDialog(QDialog):
    def __init__(self, github, *, preselect_repo: str = "", parent=None):
        super().__init__(parent)
        self._gh = github
        self._payload: Optional[dict] = None

        self.setWindowTitle("Review a pull request")
        self.setModal(True)
        self.setMinimumWidth(440)
        t = theme.color
        self.setStyleSheet(
            f"""
            QDialog {{ background: {t('card_bg')}; }}
            QLabel {{ color: {t('dialog_text')}; font-size: 12px; }}
            QLabel#h {{ font-size: 14px; font-weight: 700; }}
            QLineEdit, QComboBox {{
                background: {t('surface')}; color: {t('text')};
                border: 1px solid {t('border')}; border-radius: 6px; padding: 6px 9px; font-size: 12px;
            }}
            QLineEdit:focus, QComboBox:focus {{ border-color: {t('accent')}; }}
            QCheckBox {{ color: {t('dialog_text')}; font-size: 12px; spacing: 6px; }}
            QPushButton {{
                background: {t('surface')}; color: {t('text')};
                border: 1px solid {t('border')}; border-radius: 7px; padding: 8px 16px; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {t('accent')}; }}
            QPushButton#primary {{
                background: {t('accent')}; color: {t('on_accent')};
                border-color: {t('accent')}; font-weight: 700;
            }}
            QPushButton#primary:hover {{ background: {t('accent_hover')}; }}
            """
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)

        h = QLabel("Review a pull request")
        h.setObjectName("h")
        lay.addWidget(h)

        lay.addWidget(QLabel("Repository (owner/name)"))
        self._repo = QComboBox()
        self._repo.setEditable(True)
        self._repo.currentTextChanged.connect(self._on_repo_changed)
        lay.addWidget(self._repo)

        lay.addWidget(QLabel("Pull request"))
        self._pr = QComboBox()
        self._pr.setEditable(True)
        lay.addWidget(self._pr)
        hint = QLabel("Pick an open PR, or type a number / paste a PR URL.")
        hint.setStyleSheet(f"color: {t('text_faint')}; font-size: 10px;")
        lay.addWidget(hint)

        lay.addWidget(QLabel("Focus"))
        frow = QHBoxLayout()
        self._focus: dict[str, QCheckBox] = {}
        for key, label in _FOCI:
            cb = QCheckBox(label)
            cb.setChecked(key in ("bugs", "security", "tests"))
            self._focus[key] = cb
            frow.addWidget(cb)
        frow.addStretch(1)
        lay.addLayout(frow)

        orow = QHBoxLayout()
        self._post = QCheckBox("Post the review to GitHub")
        self._post.toggled.connect(self._on_post_toggled)
        orow.addWidget(self._post)
        orow.addStretch(1)
        orow.addWidget(QLabel("as"))
        self._event = QComboBox()
        self._event.addItem("Comment", "comment")
        self._event.addItem("Request changes", "request_changes")
        self._event.addItem("Approve", "approve")
        self._event.setEnabled(False)
        orow.addWidget(self._event)
        lay.addLayout(orow)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self._go = QPushButton("Start review")
        self._go.setObjectName("primary")
        self._go.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addStretch(1)
        btns.addWidget(self._go)
        lay.addLayout(btns)

        # The repo combo is editable, so currentTextChanged fires per keystroke.
        # Coalesce a burst of those into one fetch.
        self._pending_repo = ""
        self._pr_fetch: Optional[_PrFetch] = None
        self._pr_timer = QTimer(self)
        self._pr_timer.setSingleShot(True)
        self._pr_timer.setInterval(400)
        self._pr_timer.timeout.connect(self._fetch_prs)

        self._populate_repos(preselect_repo)

    # -- population ---------------------------------------------------

    def _populate_repos(self, preselect: str) -> None:
        repos = []
        # The panel already fetched repos into its list; re-fetch is cheap but
        # async. Use whatever the controller cached if exposed, else just allow
        # free text.
        cached = getattr(self._gh, "_last_repos", None)
        if isinstance(cached, list):
            repos = [r.get("full_name") for r in cached if isinstance(r, dict)]
        for r in repos:
            if r:
                self._repo.addItem(r)
        if preselect:
            self._repo.setCurrentText(preselect)

    def _on_repo_changed(self, repo: str) -> None:
        """Queue a PR fetch for ``repo`` -- never run one inline (see
        :class:`_PrFetch`)."""
        self._pending_repo = (repo or "").strip()
        self._pr.clear()
        self._pr_timer.stop()
        if self._pending_repo and "/" in self._pending_repo and list_open_prs is not None:
            self._pr_timer.start()

    def _fetch_prs(self) -> None:
        repo = self._pending_repo
        if not repo or self._gh is None:
            return
        self._detach_fetch()
        # Parented to the controller, not to this dialog: an in-flight request
        # can outlive a dialog the user closes, and a QThread destroyed while
        # still running takes the process with it.
        owner = self._gh if isinstance(self._gh, QObject) else None
        worker = _PrFetch(self._gh, repo, parent=owner)
        worker.ready.connect(self._fill_prs)
        worker.finished.connect(worker.deleteLater)
        self._pr_fetch = worker
        worker.start()

    def _detach_fetch(self) -> None:
        """Let the in-flight fetch finish unheard. Doesn't wait -- ``requests``
        won't notice an interruption request mid-call, and waiting here would
        put the freeze straight back."""
        worker, self._pr_fetch = self._pr_fetch, None
        if worker is None:
            return
        try:
            worker.requestInterruption()
            worker.ready.disconnect(self._fill_prs)
        except (RuntimeError, TypeError):
            # The worker finished and ``deleteLater`` already took the C++ object
            # out from under this handle -- there is nothing left to detach.
            pass

    def _fill_prs(self, repo: str, prs: list) -> None:
        if repo != self._pending_repo:   # a later keystroke already won
            return
        self._pr.clear()
        for pr in prs:
            self._pr.addItem(f"#{pr['number']} — {pr['title']}", pr["number"])

    def done(self, result: int) -> None:  # noqa: D102 - QDialog override
        self._pr_timer.stop()
        self._detach_fetch()
        super().done(result)

    def _on_post_toggled(self, on: bool) -> None:
        self._event.setEnabled(on)

    # -- result -----------------------------------------------------

    def _accept(self) -> None:
        repo = (self._repo.currentText() or "").strip()
        pr_text = (self._pr.currentText() or "").strip()

        parsed = parse_pr_url(pr_text) or parse_pr_url(repo)
        if parsed:
            repo, pr_number = parsed
        else:
            data = self._pr.currentData()
            if isinstance(data, int):
                pr_number = data
            else:
                digits = "".join(ch for ch in pr_text if ch.isdigit())
                pr_number = int(digits) if digits else 0

        if not repo or "/" not in repo or pr_number <= 0:
            self._go.setText("Enter a repo and PR number")
            return

        self._payload = {
            "repo": repo,
            "pr_number": pr_number,
            "options": {
                "post": self._post.isChecked(),
                "event": self._event.currentData() if self._post.isChecked() else "comment",
                "focus": [k for k, cb in self._focus.items() if cb.isChecked()],
            },
        }
        self.accept()

    def result_payload(self) -> Optional[dict]:
        return self._payload
