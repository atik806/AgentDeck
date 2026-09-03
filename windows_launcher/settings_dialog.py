"""The Settings dialog -- opened from the toolbar's gear button.

A single place for the app-wide preferences that otherwise only live in
``config.json``: appearance (light / dark / system, terminal font size),
startup behaviour, updates, and the Claude Code folder-trust shortcut. The
remaining toolbar-adjacent settings (shell, layout) keep their own control
there.

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
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

import theme
from config import CONFIG_RANGES, save_config

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
        voice_enabled: bool = True,
    ):
        super().__init__(parent)
        self._config = config
        self._updater = updater
        self._current_version = (current_version or "").lstrip("v")
        self._voice_pro = bool(voice_enabled)
        self._upd_conns: list = []
        self._dl = None

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

        # -- Terminal font size --------------------------------------------
        outer.addWidget(QLabel("Terminal font size"))
        self._font_lo, self._font_hi = CONFIG_RANGES.get("font_size", (6, 48))
        self._font_size = max(
            self._font_lo,
            min(self._font_hi, int(self._config.get("font_size", 11) or 11)),
        )
        font_row = QHBoxLayout()
        font_row.setSpacing(8)
        self._font_minus = QPushButton("−")
        self._font_minus.setObjectName("stepper")
        self._font_minus.setFixedSize(30, 28)
        self._font_minus.setToolTip("Smaller")
        self._font_minus.clicked.connect(lambda: self._bump_font(-1))
        self._font_value = QLabel()
        self._font_value.setObjectName("fontValue")
        self._font_value.setAlignment(Qt.AlignCenter)
        self._font_value.setMinimumWidth(52)
        self._font_plus = QPushButton("+")
        self._font_plus.setObjectName("stepper")
        self._font_plus.setFixedSize(30, 28)
        self._font_plus.setToolTip("Larger")
        self._font_plus.clicked.connect(lambda: self._bump_font(1))
        font_row.addWidget(self._font_minus)
        font_row.addWidget(self._font_value)
        font_row.addWidget(self._font_plus)
        font_row.addStretch(1)
        outer.addLayout(font_row)
        hint = QLabel("Applies to every open workspace and new panes.")
        hint.setObjectName("hint")
        outer.addWidget(hint)
        self._sync_font_label()
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
        outer.addSpacing(10)

        # -- Voice input --------------------------------------------------
        self._build_voice_section(outer)

        outer.addSpacing(14)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.clicked.connect(self.accept)
        btn_row.addWidget(done)
        outer.addLayout(btn_row)

    # -- voice ------------------------------------------------------------

    _VAD_LABELS = [("High sensitivity", 1), ("Medium sensitivity", 2), ("Low sensitivity", 3)]

    def _build_voice_section(self, outer: QVBoxLayout) -> None:
        import voice_models

        outer.addWidget(self._section("Voice input"))

        self._voice_enable = self._check(
            outer, "Enable voice-to-text (Ctrl+Shift+X)",
            self._config.get("voice_input_enabled", True),
        )
        self._voice_enable.toggled.connect(
            lambda v: self._set("voice_input_enabled", bool(v))
        )

        # Microphone -------------------------------------------------------
        try:
            from voice_engine import AudioDeviceManager
        except Exception:  # noqa: BLE001
            AudioDeviceManager = None

        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        mic_row.addWidget(QLabel("Microphone"))
        self._voice_mic = QComboBox()
        self._voice_mic.addItem("System default", None)
        cur_mic = self._config.get("voice_mic_device")
        if AudioDeviceManager is not None:
            try:
                for dev in AudioDeviceManager.list_input_devices():
                    self._voice_mic.addItem(dev["name"], dev["name"])
            except Exception:  # noqa: BLE001
                pass
        else:
            self._voice_mic.setEnabled(False)
            self._voice_mic.setToolTip("Audio libraries aren't installed.")
        sel = self._voice_mic.findData(cur_mic)
        self._voice_mic.setCurrentIndex(sel if sel >= 0 else 0)
        self._voice_mic.currentIndexChanged.connect(
            lambda _i: self._set("voice_mic_device", self._voice_mic.currentData())
        )
        mic_row.addWidget(self._voice_mic, 1)
        outer.addLayout(mic_row)

        # Model ----------------------------------------------------------
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        model_row.addWidget(QLabel("Speech model"))
        self._voice_model = QComboBox()
        for name, (label, size) in voice_models.MODEL_LABELS.items():
            self._voice_model.addItem(label, name)
        self._voice_model.currentIndexChanged.connect(self._on_voice_model_pick)
        model_row.addWidget(self._voice_model, 1)
        outer.addLayout(model_row)

        dl_row = QHBoxLayout()
        dl_row.setSpacing(8)
        self._voice_dl_btn = QPushButton("Download now")
        self._voice_dl_btn.clicked.connect(self._on_voice_download)
        dl_row.addWidget(self._voice_dl_btn)
        self._voice_dl_bar = QProgressBar()
        self._voice_dl_bar.setRange(0, 100)
        self._voice_dl_bar.setValue(0)
        self._voice_dl_bar.setTextVisible(True)
        self._voice_dl_bar.hide()
        dl_row.addWidget(self._voice_dl_bar, 1)
        outer.addLayout(dl_row)
        self._sync_voice_model_combo()

        # Language -----------------------------------------------------
        lang_row = QHBoxLayout()
        lang_row.setSpacing(8)
        lang_row.addWidget(QLabel("Language"))
        self._voice_lang = QComboBox()
        self._voice_lang.addItem("Auto-detect (needs a multilingual model)", "auto")
        self._voice_lang.addItem("English", "en")
        li = self._voice_lang.findData(self._config.get("voice_language", "auto"))
        self._voice_lang.setCurrentIndex(li if li >= 0 else 0)
        self._voice_lang.currentIndexChanged.connect(
            lambda _i: self._set("voice_language", self._voice_lang.currentData())
        )
        lang_row.addWidget(self._voice_lang, 1)
        outer.addLayout(lang_row)

        # VAD sensitivity --------------------------------------------
        vad_row = QHBoxLayout()
        vad_row.setSpacing(8)
        vad_row.addWidget(QLabel("Mic sensitivity"))
        self._voice_vad = QComboBox()
        for label, val in self._VAD_LABELS:
            self._voice_vad.addItem(label, val)
        vi = self._voice_vad.findData(int(self._config.get("voice_vad_aggressiveness", 2) or 2))
        self._voice_vad.setCurrentIndex(vi if vi >= 0 else 1)
        self._voice_vad.currentIndexChanged.connect(
            lambda _i: self._set("voice_vad_aggressiveness", int(self._voice_vad.currentData()))
        )
        vad_row.addWidget(self._voice_vad, 1)
        outer.addLayout(vad_row)

        self._voice_post = self._check(
            outer, "Tidy up each phrase (capitalise, drop the trailing period)",
            self._config.get("voice_post_processing", True),
        )
        self._voice_post.toggled.connect(
            lambda v: self._set("voice_post_processing", bool(v))
        )

        self._voice_fallback = self._check(
            outer, "If the mic drops out, switch to the default device",
            self._config.get("voice_mic_autofallback", True),
        )
        self._voice_fallback.toggled.connect(
            lambda v: self._set("voice_mic_autofallback", bool(v))
        )

        vhint = QLabel(
            "Press Ctrl+Shift+X anywhere in AgentDeck to dictate into the focused "
            "pane. Say your command, review it, then press Enter to run."
        )
        vhint.setObjectName("hint")
        vhint.setWordWrap(True)
        outer.addWidget(vhint)

        if not self._voice_pro:
            for w in (self._voice_enable, self._voice_mic, self._voice_model,
                      self._voice_lang, self._voice_vad, self._voice_post,
                      self._voice_fallback):
                w.setEnabled(False)
            pro = QLabel("Voice-to-text is part of AgentDeck Pro.")
            pro.setObjectName("hint")
            outer.addWidget(pro)

    def _current_voice_model(self) -> str:
        return self._voice_model.currentData() or "auto"

    def _sync_voice_model_combo(self) -> None:
        """Reflect the stored choice + which models are on disk."""
        import voice_models

        want = str(self._config.get("voice_model", "auto") or "auto")
        idx = self._voice_model.findData(want)
        self._voice_model.blockSignals(True)
        self._voice_model.setCurrentIndex(idx if idx >= 0 else 0)
        self._voice_model.blockSignals(False)
        try:
            import voice_download
        except Exception:  # noqa: BLE001
            voice_download = None
        for i in range(self._voice_model.count()):
            name = self._voice_model.itemData(i)
            label = voice_models.MODEL_LABELS.get(name, (name, ""))[0]
            if name != "auto" and voice_download is not None \
                    and voice_download.model_is_downloaded(name):
                label += "  ·  downloaded"
            self._voice_model.setItemText(i, label)
        sel = self._current_voice_model()
        on_disk = (
            sel != "auto" and voice_download is not None
            and voice_download.model_is_downloaded(sel)
        )
        self._voice_dl_btn.setEnabled(
            self._voice_pro and sel != "auto" and not on_disk
            and not (self._dl is not None and self._dl.busy)
        )
        self._voice_dl_btn.setText("Downloaded" if on_disk else "Download now")

    def _on_voice_model_pick(self, _idx: int) -> None:
        self._set("voice_model", self._current_voice_model())
        self._sync_voice_model_combo()

    def _on_voice_download(self) -> None:
        sel = self._current_voice_model()
        if sel == "auto":
            return
        try:
            from voice_download import ModelDownloadController
        except Exception as exc:  # noqa: BLE001
            self._voice_dl_bar.show()
            self._voice_dl_bar.setFormat(f"unavailable: {exc}")
            return
        if self._dl is None:
            self._dl = ModelDownloadController(self)
            self._dl.progress.connect(self._voice_dl_bar.setValue)
            self._dl.busy_changed.connect(self._on_voice_dl_busy)
            self._dl.finished.connect(self._on_voice_dl_finished)
            self._dl.failed.connect(self._on_voice_dl_failed)
        self._voice_dl_bar.setFormat("%p%")
        self._voice_dl_bar.setValue(0)
        self._voice_dl_bar.show()
        self._dl.download(sel)

    def _on_voice_dl_busy(self, busy: bool) -> None:
        self._voice_dl_btn.setEnabled(not busy)

    def _on_voice_dl_finished(self, _name: str) -> None:
        self._voice_dl_bar.setValue(100)
        self._sync_voice_model_combo()

    def _on_voice_dl_failed(self, message: str) -> None:
        self._voice_dl_bar.setFormat(f"failed: {message}")
        self._sync_voice_model_combo()

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

    def _bump_font(self, delta: int) -> None:
        size = max(self._font_lo, min(self._font_hi, self._font_size + delta))
        if size == self._font_size:
            return
        self._font_size = size
        self._sync_font_label()
        self._set("font_size", size)

    def _sync_font_label(self) -> None:
        self._font_value.setText(f"{self._font_size} px")
        self._font_minus.setEnabled(self._font_size > self._font_lo)
        self._font_plus.setEnabled(self._font_size < self._font_hi)

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
            QLabel#fontValue {{ color: {t('dialog_text')}; font-size: 12px; font-weight: 600; }}
            QPushButton#stepper {{ padding: 0; font-size: 15px; font-weight: 700; }}
            QPushButton#stepper:disabled {{ color: {t('text_muted')}; border-color: {t('card_border')}; }}
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
