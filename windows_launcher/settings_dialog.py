"""The Settings dialog -- opened from the toolbar's gear button.

A single place for the app-wide preferences that otherwise only live in
``config.json``: appearance (light / dark / system), startup behaviour, updates,
and the Claude Code folder-trust shortcut. Toolbar-adjacent settings that
already have their own control (shell, layout, font size) are left there.

Each change is written straight into the ``config`` dict passed in and
persisted with :func:`config.save_config`; the panel re-reads what it needs
after ``exec()`` returns. Wears the panel's blue accent and follows the active
light / dark :mod:`theme`.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

import theme
from config import save_config

__all__ = ["SettingsDialog"]


class SettingsDialog(QDialog):
    """App-wide preferences. Writes into ``config`` as the user toggles."""

    def __init__(self, config: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config

        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._apply_style()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(6)

        title = QLabel("Settings")
        title.setObjectName("h1")
        outer.addWidget(title)
        outer.addSpacing(6)

        # -- Appearance ------------------------------------------------------
        outer.addWidget(self._section("Appearance"))
        outer.addWidget(QLabel("Theme"))

        pref = str(self._config.get("theme", "system") or "system").lower()
        self._theme_group = QButtonGroup(self)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        for key, text in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            rb = QRadioButton(text)
            rb.setProperty("theme_key", key)
            rb.setChecked(pref == key)
            self._theme_group.addButton(rb)
            theme_row.addWidget(rb)
        theme_row.addStretch(1)
        outer.addLayout(theme_row)
        self._theme_group.buttonToggled.connect(self._on_theme_pick)
        outer.addSpacing(10)

        # -- Startup -------------------------------------------------------
        outer.addWidget(self._section("Startup"))
        self._splash = self._check(
            outer, "Play the launch animation", self._config.get("show_splash", True)
        )
        self._splash.toggled.connect(lambda v: self._set("show_splash", bool(v)))

        self._wizard = self._check(
            outer, "Open straight to my last setup (skip the wizard)",
            self._config.get("skip_wizard", False),
        )
        self._wizard.toggled.connect(lambda v: self._set("skip_wizard", bool(v)))
        outer.addSpacing(10)

        # -- Updates ------------------------------------------------------
        outer.addWidget(self._section("Updates"))
        self._auto_upd = self._check(
            outer, "Check for a newer AgentDeck on launch",
            self._config.get("auto_check_updates", True),
        )
        self._auto_upd.toggled.connect(lambda v: self._set("auto_check_updates", bool(v)))

        chan_row = QHBoxLayout()
        chan_row.setSpacing(8)
        chan_row.addWidget(QLabel("Channel"))
        self._channel = QComboBox()
        self._channel.addItem("Stable", "stable")
        self._channel.addItem("Beta (pre-releases)", "beta")
        idx = self._channel.findData(self._config.get("update_channel", "stable"))
        self._channel.setCurrentIndex(max(0, idx))
        self._channel.currentIndexChanged.connect(
            lambda _i: self._set("update_channel", self._channel.currentData())
        )
        chan_row.addWidget(self._channel, 1)
        outer.addLayout(chan_row)
        outer.addSpacing(10)

        # -- Agents ------------------------------------------------------
        outer.addWidget(self._section("Agents"))
        self._pretrust = self._check(
            outer,
            "Pre-accept Claude Code's “trust this folder?” prompt",
            self._config.get("pretrust_agent_folder", False),
        )
        self._pretrust.toggled.connect(lambda v: self._set("pretrust_agent_folder", bool(v)))
        hint = QLabel(
            "Skips the prompt for the folder you pick. A folder that ships its "
            "own .claude/ config is never auto-trusted."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        outer.addSpacing(14)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.clicked.connect(self.accept)
        btn_row.addWidget(done)
        outer.addLayout(btn_row)

    # -- construction helpers ------------------------------------------------

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("section")
        return lbl

    def _check(self, layout, text: str, value) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(bool(value))
        layout.addWidget(box)
        return box

    # -- persistence -------------------------------------------------------

    def _set(self, key: str, value) -> None:
        self._config[key] = value
        try:
            save_config(self._config)
        except Exception:  # noqa: BLE001 - a read-only config dir must not crash us
            pass

    def _on_theme_pick(self, button, checked: bool) -> None:
        if not checked:
            return
        self._set("theme", button.property("theme_key"))

    # -- styling ---------------------------------------------------------

    def _apply_style(self) -> None:
        t = theme.color
        blue = t("accent")
        self.setStyleSheet(
            f"""
            QDialog {{ background: {t('card_bg')}; }}
            QLabel {{ color: {t('dialog_text')}; font-size: 12px; }}
            QLabel#h1 {{ font-size: 16px; font-weight: 700; }}
            QLabel#section {{
                color: {t('text_muted')}; font-size: 10px; font-weight: 700;
                letter-spacing: 1px; padding-top: 2px;
            }}
            QLabel#hint {{ color: {t('text_muted')}; font-size: 11px; }}
            QCheckBox, QRadioButton {{ color: {t('dialog_text')}; font-size: 12px; spacing: 8px; }}
            QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}
            QComboBox {{
                background: {t('card_raised')}; color: {t('dialog_text')};
                border: 1px solid {t('card_border')}; border-radius: 7px;
                padding: 6px 10px; font-size: 12px;
            }}
            QComboBox:focus {{ border-color: {blue}; }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background: {t('menu_bg')}; color: {t('dialog_text')};
                border: 1px solid {t('menu_border')};
                selection-background-color: {blue}; selection-color: {t('on_accent')};
            }}
            QPushButton {{
                background: {t('card_raised')}; color: {t('dialog_text')};
                border: 1px solid {t('card_border')}; border-radius: 7px;
                padding: 8px 18px; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {blue}; }}
            QPushButton#primary {{
                background: {blue}; color: {t('on_accent')}; border-color: {blue};
                font-weight: 700;
            }}
            """
        )
