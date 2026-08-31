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

    def __init__(
        self,
        config: dict,
        parent: Optional[QWidget] = None,
        *,
        updater=None,
        current_version: str = "",
    ):
        super().__init__(parent)
        self._config = config
        self._updater = updater
        self._current_version = (current_version or "").lstrip("v")
        self._upd_conns: list = []

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
        self._channel.setToolTip("A channel change takes effect after you restart AgentDeck.")
        self._channel.currentIndexChanged.connect(self._on_channel_pick)
        chan_row.addWidget(self._channel, 1)
        outer.addLayout(chan_row)

        # The manual "check now" control -- this is where updating lives now,
        # instead of a toolbar button.
        outer.addSpacing(4)
        upd_row = QHBoxLayout()
        upd_row.setSpacing(8)
        self._check_btn = QPushButton("Check for updates")
        self._check_btn.clicked.connect(self._on_check_updates)
        upd_row.addWidget(self._check_btn)
        upd_row.addStretch(1)
        outer.addLayout(upd_row)

        self._upd_status = QLabel("")
        self._upd_status.setObjectName("hint")
        self._upd_status.setWordWrap(True)
        outer.addWidget(self._upd_status)
        self._wire_updates()
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

    # -- updates -----------------------------------------------------------

    def _wire_updates(self) -> None:
        """Hook the "Check for updates" button up to the UpdateController.

        The controller's other outcomes (a modal "download?" prompt, the
        animated progress dialog, the "restart now?" prompt) are still driven by
        the panel -- this dialog only adds an inline status line and the manual
        trigger, and drops its connections again when it closes.
        """
        u = self._updater
        base = (
            f"You're on AgentDeck v{self._current_version}."
            if self._current_version else ""
        )
        if u is None:
            self._check_btn.setEnabled(False)
            self._upd_status.setText(base or "Updates aren't available in this build.")
            return
        if not getattr(u, "enabled", False):
            self._check_btn.setEnabled(False)
            self._upd_status.setText(
                getattr(u, "unavailable_reason", "") or "Updates are unavailable here."
            )
            return

        self._upd_status.setText(base)
        self._upd_conns = [
            (u.busy_changed, self._on_upd_busy),
            (u.up_to_date, self._on_upd_up_to_date),
            (u.available, self._on_upd_available),
            (u.progress, self._on_upd_progress),
            (u.ready, self._on_upd_ready),
            (u.error, self._on_upd_error),
        ]
        for sig, slot in self._upd_conns:
            sig.connect(slot)
        # Reflect a check that is already running (e.g. the launch check).
        if getattr(u, "busy", False):
            self._on_upd_busy(True)

    def _teardown_updates(self) -> None:
        for sig, slot in self._upd_conns:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._upd_conns = []

    def _on_channel_pick(self, _idx: int) -> None:
        self._set("update_channel", self._channel.currentData())
        if self._updater is not None and getattr(self._updater, "enabled", False):
            self._upd_status.setText("Restart AgentDeck for the channel change to take effect.")

    def _on_check_updates(self) -> None:
        if self._updater is None:
            return
        self._upd_status.setText("Checking for updates…")
        self._updater.check(silent=False)

    def _on_upd_busy(self, busy: bool) -> None:
        self._check_btn.setEnabled(not busy)
        if busy and "Downloading" not in self._upd_status.text():
            self._upd_status.setText("Checking for updates…")

    def _on_upd_up_to_date(self) -> None:
        self._upd_status.setText(
            f"AgentDeck v{self._current_version} is the latest version."
            if self._current_version else "You're on the latest version."
        )

    def _on_upd_available(self, version: str, _notes: str) -> None:
        self._upd_status.setText(f"AgentDeck {version} is available.")

    def _on_upd_progress(self, pct: int) -> None:
        self._upd_status.setText(f"Downloading update… {int(pct)}%")

    def _on_upd_ready(self, version: str) -> None:
        self._upd_status.setText(f"AgentDeck {version} downloaded — restart to finish.")

    def _on_upd_error(self, message: str) -> None:
        self._upd_status.setText(f"Update check failed: {message}")

    # -- Qt lifecycle -----------------------------------------------------

    def done(self, result: int) -> None:            # noqa: D102 - Qt override
        self._teardown_updates()
        super().done(result)

    def closeEvent(self, event):                     # noqa: D102 - Qt override
        self._teardown_updates()
        super().closeEvent(event)

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
