"""The "new workspace" dialog -- shown every time a workspace is created.

Adding a workspace from the UI (the toolbar's ＋ Workspace, the sidebar's +, or
Ctrl+Shift+N) opens this first: pick the coding agent to auto-run in the new
workspace's terminals and how many terminals to open. It is deliberately small
-- one dropdown, one spinbox -- and wears the panel's blue accent, not the
setup wizard's amber, because it is an in-app action rather than the front door.

:meth:`result_choice` returns ``{agent_key, agent_custom, agent_command,
count}``; ``exec()`` is ``Accepted`` only when the user clicks *Create
workspace*.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QSpinBox,
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
from workspace import MAX_PANES

__all__ = ["NewWorkspaceDialog"]

import theme

def _BG() -> str: return theme.color("card_bg")
def _CARD() -> str: return theme.color("card_raised")
def _BORDER() -> str: return theme.color("card_border")
def _TEXT() -> str: return theme.color("dialog_text")
def _MUTED() -> str: return theme.color("text_muted")
def _BLUE() -> str: return theme.color("accent")
def _BLUE_HI() -> str: return theme.color("accent_hover")


class NewWorkspaceDialog(QDialog):
    """Ask which agent (and how many terminals) a new workspace should open."""

    def __init__(
        self,
        *,
        default_name: str = "",
        default_agent: str = PLAIN_KEY,
        default_custom: str = "",
        default_count: int = 4,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._result: Optional[dict] = None

        self.setWindowTitle("New workspace")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(
            f"""
            QDialog {{ background: {_BG()}; }}
            QLabel {{ color: {_TEXT()}; }}
            QLabel#h1 {{ font-size: 16px; font-weight: 700; }}
            QLabel#sub, QLabel#note {{ color: {_MUTED()}; font-size: 11px; }}
            QComboBox, QLineEdit, QSpinBox {{
                background: {_CARD()}; color: {_TEXT()};
                border: 1px solid {_BORDER()}; border-radius: 7px;
                padding: 7px 10px; font-size: 12px;
            }}
            QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{
                border-color: {_BLUE()};
            }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background: {theme.color('menu_bg')}; color: {_TEXT()};
                border: 1px solid {theme.color('menu_border')};
                selection-background-color: {_BLUE()}; selection-color: {theme.color('on_accent')};
            }}
            QPushButton {{
                background: {_CARD()}; color: {_TEXT()};
                border: 1px solid {_BORDER()}; border-radius: 7px;
                padding: 8px 16px; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {_BLUE()}; }}
            QPushButton#primary {{
                background: {_BLUE()}; color: {theme.color('on_accent')}; border-color: {_BLUE()};
                font-weight: 700;
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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)

        title = QLabel("New workspace")
        title.setObjectName("h1")
        outer.addWidget(title)
        sub = QLabel(
            "Pick an agent to run in every terminal of the new workspace."
        )
        sub.setObjectName("sub")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        outer.addSpacing(4)
        outer.addWidget(self._label("Name"))
        self._name = QLineEdit(default_name, self)
        self._name.setPlaceholderText(default_name or "Workspace")
        # Prefilled with the next "Workspace N" -- selected so the user can just
        # type over it, or leave it and press Create.
        self._name.selectAll()
        outer.addWidget(self._name)

        outer.addSpacing(4)
        outer.addWidget(self._label("Agent"))

        self._installed: dict[str, bool] = {}
        self._combo = QComboBox(self)
        for key, label, command, ok in all_agents():
            self._installed[key] = ok
            suffix = "" if ok else "   ·  not installed"
            self._combo.addItem(f"{label}  —  {command}{suffix}", key)
        self._combo.addItem("Plain shell — no agent", PLAIN_KEY)
        self._combo.addItem("Custom command…", CUSTOM_KEY)
        start = self._combo.findData(default_agent)
        if start < 0:
            start = self._combo.findData(PLAIN_KEY)
        self._combo.setCurrentIndex(max(0, start))
        self._combo.currentIndexChanged.connect(self._sync)
        outer.addWidget(self._combo)

        self._custom = QLineEdit(default_custom, self)
        self._custom.setPlaceholderText("e.g.  aider --model sonnet")
        self._custom.textChanged.connect(self._sync)
        outer.addWidget(self._custom)

        #: InstallHint for a not-installed pick, built lazily.
        self._hint: Optional[InstallHint] = None
        self._hint_key = ""
        self._hint_slot = QVBoxLayout()
        outer.addLayout(self._hint_slot)

        outer.addSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._label("Terminals"))
        self._count = QSpinBox(self)
        self._count.setRange(1, MAX_PANES)
        self._count.setValue(max(1, min(MAX_PANES, int(default_count))))
        self._count.valueChanged.connect(self._sync)
        row.addWidget(self._count)
        row.addStretch(1)
        outer.addLayout(row)

        self._note = QLabel("")
        self._note.setObjectName("note")
        self._note.setWordWrap(True)
        outer.addWidget(self._note)

        outer.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        self._create = QPushButton("Create workspace", self)
        self._create.setObjectName("primary")
        self._create.setDefault(True)
        self._create.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self._create)
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

    def _agent_key(self) -> str:
        return self._combo.currentData()

    def _real_missing(self) -> bool:
        key = self._agent_key()
        return key not in (PLAIN_KEY, CUSTOM_KEY) and not self._installed.get(key, True)

    def _show_hint(self) -> None:
        key = self._agent_key()
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
        key = self._agent_key()
        is_custom = key == CUSTOM_KEY
        self._custom.setVisible(is_custom)

        missing = self._real_missing()
        if missing:
            self._show_hint()
        elif self._hint is not None:
            self._hint.setVisible(False)

        command = resolve_agent(key, self._custom.text())
        n = self._count.value()
        plural = "s" if n != 1 else ""
        if missing:
            self._note.setText(
                f"{agent_label(key)} isn't installed — follow the steps above, "
                f"then Re-check."
            )
        elif command:
            self._note.setText(f"Runs  {command}  in {n} terminal{plural}.")
        else:
            self._note.setText(f"Opens {n} plain shell{plural}.")

        self._create.setEnabled(
            not missing
            and not (is_custom and not self._custom.text().strip())
        )

    def _accept(self) -> None:
        key = self._agent_key()
        custom = self._custom.text().strip()
        self._result = {
            "name": self._name.text().strip(),
            "agent_key": key,
            "agent_custom": custom,
            "agent_command": resolve_agent(key, custom),
            "count": self._count.value(),
        }
        self.accept()

    # -- result ------------------------------------------------------------

    def result_choice(self) -> Optional[dict]:
        """The picked ``{agent_key, agent_custom, agent_command, count}``."""
        return dict(self._result) if self._result is not None else None
