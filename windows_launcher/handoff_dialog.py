"""The "hand off this conversation" dialog.

Opened from a pane's ⤳ header button. Pick which agent should pick the
conversation up; the dialog works out whether that is a native *resume* of the
same agent (full history, a new pane running ``claude --resume …`` / …) or a
*cross-agent* handoff (the conversation rendered to Markdown and handed to a
different agent as its opening task).

Wears the panel's blue accent, like :mod:`new_workspace_dialog` -- it is an
in-app action, not the front door.

:meth:`result_choice` returns
``{source_key, source_dir, target_key, target_command, fork,
include_thinking, any_cwd}`` or ``None`` when cancelled.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import agent_sessions
import theme
from agents import PLAIN_KEY, agent_label, all_agents, resolve_agent
from agents_ui import InstallHint

__all__ = ["HandoffDialog"]


def _BG() -> str: return theme.color("card_bg")
def _CARD() -> str: return theme.color("card_raised")
def _BORDER() -> str: return theme.color("card_border")
def _TEXT() -> str: return theme.color("dialog_text")
def _MUTED() -> str: return theme.color("text_muted")
def _BLUE() -> str: return theme.color("accent")
def _BLUE_HI() -> str: return theme.color("accent_hover")


class HandoffDialog(QDialog):
    def __init__(
        self,
        *,
        source_key: str = "",
        source_dir: str = "",
        fork_default: bool = True,
        thinking_default: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._result: Optional[dict] = None

        self.setWindowTitle("Hand off conversation")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(
            f"""
            QDialog {{ background: {_BG()}; }}
            QLabel {{ color: {_TEXT()}; }}
            QLabel#h1 {{ font-size: 16px; font-weight: 700; }}
            QLabel#sub, QLabel#note {{ color: {_MUTED()}; font-size: 11px; }}
            QComboBox, QLineEdit {{
                background: {_CARD()}; color: {_TEXT()};
                border: 1px solid {_BORDER()}; border-radius: 7px;
                padding: 7px 10px; font-size: 12px;
            }}
            QComboBox:focus, QLineEdit:focus {{ border-color: {_BLUE()}; }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background: {theme.color('menu_bg')}; color: {_TEXT()};
                border: 1px solid {theme.color('menu_border')};
                selection-background-color: {_BLUE()};
                selection-color: {theme.color('on_accent')};
            }}
            QCheckBox {{ color: {_TEXT()}; font-size: 11px; spacing: 7px; }}
            QPushButton {{
                background: {_CARD()}; color: {_TEXT()};
                border: 1px solid {_BORDER()}; border-radius: 7px;
                padding: 8px 16px; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {_BLUE()}; }}
            QPushButton#primary {{
                background: {_BLUE()}; color: {theme.color('on_accent')};
                border-color: {_BLUE()}; font-weight: 700;
            }}
            QPushButton#primary:hover {{ background: {_BLUE_HI()};
                                         border-color: {_BLUE_HI()}; }}
            QPushButton#primary:disabled {{
                background: {theme.color('accent_soft_bg')};
                color: {theme.color('text_faint')};
                border-color: {theme.color('accent_soft_bg')};
            }}
            """
        )

        self._fork_default = fork_default

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(10)

        title = QLabel("Hand off conversation")
        title.setObjectName("h1")
        outer.addWidget(title)
        sub = QLabel(
            "Open a new pane in this workspace and carry the current "
            "conversation into it."
        )
        sub.setObjectName("sub")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        outer.addSpacing(4)
        outer.addWidget(self._label("From (source agent)"))
        self._source = QComboBox(self)
        for key, label, command, _ok in all_agents():
            self._source.addItem(f"{label}  —  {command}", key)
        self._source.addItem("Plain shell — no agent", PLAIN_KEY)
        idx = self._source.findData(source_key)
        self._source.setCurrentIndex(idx if idx >= 0 else self._source.count() - 1)
        self._source.currentIndexChanged.connect(self._sync)
        outer.addWidget(self._source)

        outer.addWidget(self._label("Source folder"))
        self._folder = QLineEdit(source_dir, self)
        self._folder.setPlaceholderText("folder the source agent ran in")
        outer.addWidget(self._folder)

        outer.addSpacing(4)
        outer.addWidget(self._label("To (target agent)"))
        self._installed: dict[str, bool] = {}
        self._target = QComboBox(self)
        for key, label, command, ok in all_agents():
            self._installed[key] = ok
            suffix = "" if ok else "   ·  not installed"
            self._target.addItem(f"{label}  —  {command}{suffix}", key)
        start = self._target.findData(source_key)
        self._target.setCurrentIndex(max(0, start))
        self._target.currentIndexChanged.connect(self._sync)
        outer.addWidget(self._target)

        self._hint: Optional[InstallHint] = None
        self._hint_key = ""
        self._hint_slot = QVBoxLayout()
        outer.addLayout(self._hint_slot)

        self._fork = QCheckBox("Fork the session — leave this pane's agent untouched", self)
        self._fork.setChecked(fork_default)
        outer.addWidget(self._fork)

        self._thinking = QCheckBox("Include the agent's own thinking / reasoning", self)
        self._thinking.setChecked(thinking_default)
        outer.addWidget(self._thinking)

        self._any_cwd = QCheckBox(
            "Use the most recent conversation if none is found for this folder", self
        )
        outer.addWidget(self._any_cwd)

        self._note = QLabel("")
        self._note.setObjectName("note")
        self._note.setWordWrap(True)
        outer.addWidget(self._note)

        outer.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        self._go = QPushButton("Hand off", self)
        self._go.setObjectName("primary")
        self._go.setDefault(True)
        self._go.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self._go)
        outer.addLayout(buttons)

        self._sync()

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {theme.color('text_muted')};"
        )
        return lbl

    def _source_key(self) -> str:
        return self._source.currentData()

    def _target_key(self) -> str:
        return self._target.currentData()

    def _same_agent(self) -> bool:
        s = self._source_key()
        return bool(s) and s != PLAIN_KEY and s == self._target_key()

    def _is_resume(self) -> bool:
        return self._same_agent() and agent_sessions.supports_resume(self._source_key())

    def _target_missing(self) -> bool:
        return not self._installed.get(self._target_key(), True)

    def _show_hint(self) -> None:
        key = self._target_key()
        if self._hint is not None and self._hint_key == key:
            self._hint.setVisible(True)
            return
        if self._hint is not None:
            self._hint.setParent(None)
            self._hint.deleteLater()
        self._hint = InstallHint(key, accent=_BLUE())
        self._hint_key = key
        self._hint.rechecked.connect(self._on_rechecked)
        self._hint_slot.addWidget(self._hint)

    def _on_rechecked(self, ok: bool) -> None:
        if ok:
            self._installed[self._hint_key] = True
        self._sync()

    def _sync(self) -> None:
        missing = self._target_missing()
        if missing:
            self._show_hint()
        elif self._hint is not None:
            self._hint.setVisible(False)

        resume = self._is_resume()
        # Fork only makes sense for a same-agent resume where the agent supports
        # it (claude / opencode).
        self._fork.setVisible(resume and self._source_key() in ("claude", "opencode"))
        self._thinking.setVisible(not resume)

        target = agent_label(self._target_key())
        if missing:
            self._note.setText(
                f"{target} isn't installed — follow the steps above, then Re-check."
            )
        elif resume:
            self._note.setText(
                f"Resumes {target}'s current session in a new pane, with the full "
                f"history."
            )
        else:
            src = agent_label(self._source_key()) if self._source_key() not in ("", PLAIN_KEY) else "the source"
            self._note.setText(
                f"Exports the conversation from {src} to a Markdown file under "
                f".agentdeck/ and starts {target} on it. The file may contain "
                f"secrets — it stays out of git but is written into the folder."
            )

        self._go.setEnabled(not missing and bool(resolve_agent(self._target_key())))

    def _accept(self) -> None:
        tkey = self._target_key()
        self._result = {
            "source_key": "" if self._source_key() == PLAIN_KEY else self._source_key(),
            "source_dir": self._folder.text().strip(),
            "target_key": tkey,
            "target_command": resolve_agent(tkey),
            "fork": self._fork.isChecked(),
            "include_thinking": self._thinking.isChecked(),
            "any_cwd": self._any_cwd.isChecked(),
        }
        self.accept()

    def result_choice(self) -> Optional[dict]:
        return dict(self._result) if self._result is not None else None
