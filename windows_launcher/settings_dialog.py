"""Settings -- app-wide preferences that otherwise only live in ``config.json``:
appearance (light / dark / system, terminal font size), startup behaviour,
updates, agent trust, and voice input. The remaining toolbar-adjacent settings
(shell, layout) keep their own control there.

:class:`SettingsPanel` is the real thing: a settings-app-style two-pane widget
-- a column of category buttons on the left (``Appearance`` / ``Startup`` /
``Updates`` / ``Agents`` / ``Voice input``), each showing its own controls on
the right (one ``QStackedWidget`` page per category, so only one category's
worth of controls is ever on screen -- Voice input alone has a dozen-plus
settings, and the previous single long scrolling list made them hard to find).
``terminal_panel.py`` embeds one permanently in the main window's
``_main_stack``, swapped in by the gear button exactly the way the Plugins and
Notes sidebar entries swap in their own panel -- Settings is a page of the app,
not a separate popup window.

:class:`SettingsDialog` is a thin modal wrapper around the same panel, kept for
anything that still wants Settings as a traditional dialog (and for the
existing test suite, which drives it directly).

Every change is written straight into the ``config`` dict passed in and
persisted with :func:`config.save_config` as it happens -- there is no
separate "Apply"/"Done" step, the same as Plugins and Notes. Theme and font
size take effect immediately (:attr:`SettingsPanel.theme_changed` /
:attr:`SettingsPanel.font_size_changed`); voice settings reach the
already-built engine via ``TerminalPanel._apply_voice_settings`` on
:attr:`SettingsPanel.voice_settings_changed`. Wears the panel's blue accent and
follows the active light / dark :mod:`theme`.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import theme
from config import CONFIG_RANGES, save_config

__all__ = ["SettingsPanel", "SettingsDialog"]

#: (label, builder-method-name) for the left-hand nav, in display order.
_CATEGORIES = [
    ("Appearance", "_build_appearance_page"),
    ("Startup", "_build_startup_page"),
    ("Updates", "_build_updates_page"),
    ("Agents", "_build_agents_page"),
    ("Voice input", "_build_voice_page"),
]


class SettingsPanel(QWidget):
    """The settings UI itself: a category nav + the selected category's
    controls. Embed this directly (see ``terminal_panel._build_body``) or via
    :class:`SettingsDialog` for a modal popup."""

    #: A theme radio was picked; the new key ("system"/"light"/"dark").
    theme_changed = Signal(str)
    #: The font stepper changed; the new size in px.
    font_size_changed = Signal(int)
    #: Any voice_* setting changed -- the caller re-syncs the (already built)
    #: voice engine / global hotkey / overlay against the new config.
    voice_settings_changed = Signal()

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
        self._voice_widgets: list[QWidget] = []
        self._voice_pro_hint: Optional[QLabel] = None
        self._upd_conns: list = []
        self._dl = None
        self._pages: list[QWidget] = []

        self.setObjectName("settingsPanel")
        self._apply_style()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- left: one button per category -----------------------------------
        nav = QWidget(self)
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(176)
        nav_col = QVBoxLayout(nav)
        nav_col.setContentsMargins(12, 16, 12, 12)
        nav_col.setSpacing(2)

        nav_title = QLabel("Settings")
        nav_title.setObjectName("h1")
        nav_col.addWidget(nav_title)
        nav_col.addSpacing(10)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: list[QPushButton] = []

        self._stack = QStackedWidget(self)

        for i, (label, builder_name) in enumerate(_CATEGORIES):
            page = self._make_page(label, getattr(self, builder_name))
            self._pages.append(page)
            self._stack.addWidget(page)

            btn = QPushButton(label, nav)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, idx=i: self.show_page(idx))
            self._nav_group.addButton(btn, i)
            self._nav_buttons.append(btn)
            nav_col.addWidget(btn)

        nav_col.addStretch(1)
        root.addWidget(nav)

        # -- right: the selected category's controls, scrollable -------------
        content_scroll = QScrollArea(self)
        content_scroll.setObjectName("settingsScroll")
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QFrame.NoFrame)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content_scroll.setWidget(self._stack)
        self._content_scroll = content_scroll
        root.addWidget(content_scroll, 1)

        self._wire_updates()

    # -- page scaffolding ---------------------------------------------------

    def _make_page(self, label: str, builder: Callable[[QVBoxLayout], None]) -> QWidget:
        """One category's page: a heading (mirrors its nav button) + its
        controls, built by ``builder``."""
        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(6)

        heading = QLabel(label)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        layout.addSpacing(10)

        builder(layout)

        layout.addStretch(1)
        return page

    def show_page(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        # A category switch always starts at the top of its own controls, even
        # if the previous one was scrolled down.
        self._content_scroll.verticalScrollBar().setValue(0)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)

    # Old name, kept as an alias -- some call sites/tests reach for it.
    _show_page = show_page

    def reset_to_first_page(self) -> None:
        """Back to Appearance -- called each time the panel is shown, so
        reopening Settings doesn't strand the user on whatever category they
        left it on last (matches a fresh dialog always opening on page one)."""
        self.show_page(0)

    def content_height_hint(self) -> int:
        """The tallest category's natural height -- used only by
        :class:`SettingsDialog` to size its floating window."""
        return max((p.sizeHint().height() for p in self._pages), default=360)

    # -- Appearance -----------------------------------------------------------

    def _build_appearance_page(self, outer: QVBoxLayout) -> None:
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
        outer.addSpacing(14)

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

    # -- Startup --------------------------------------------------------------

    def _build_startup_page(self, outer: QVBoxLayout) -> None:
        self._splash = self._check(
            outer, "Play the launch animation", self._config.get("show_splash", True)
        )
        self._splash.toggled.connect(lambda v: self._set("show_splash", bool(v)))

        self._wizard = self._check(
            outer, "Open straight to my last setup (skip the wizard)",
            self._config.get("skip_wizard", False),
        )
        self._wizard.toggled.connect(lambda v: self._set("skip_wizard", bool(v)))

    # -- Updates ----------------------------------------------------------------

    def _build_updates_page(self, outer: QVBoxLayout) -> None:
        self._auto_upd = self._check(
            outer, "Check for a newer AgentDeck on launch",
            self._config.get("auto_check_updates", True),
        )
        self._auto_upd.toggled.connect(lambda v: self._set("auto_check_updates", bool(v)))
        outer.addSpacing(10)

        outer.addWidget(QLabel("Channel"))
        chan_row = QHBoxLayout()
        chan_row.setSpacing(8)
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
        outer.addSpacing(10)
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

    # -- Agents -----------------------------------------------------------------

    def _build_agents_page(self, outer: QVBoxLayout) -> None:
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

    # -- Voice input --------------------------------------------------------

    _VAD_LABELS = [("High sensitivity", 1), ("Medium sensitivity", 2), ("Low sensitivity", 3)]

    def _voice_check(self, outer: QVBoxLayout, text: str, key: str, default) -> QCheckBox:
        box = self._check(outer, text, self._config.get(key, default))
        box.toggled.connect(lambda v, k=key: self._set_voice(k, bool(v)))
        self._voice_widgets.append(box)
        return box

    def _build_voice_page(self, outer: QVBoxLayout) -> None:
        import voice_models

        self._voice_enable = self._voice_check(
            outer, "Enable voice-to-text (Ctrl+Shift+X)", "voice_input_enabled", True
        )
        outer.addSpacing(10)

        # Microphone -------------------------------------------------------
        try:
            from voice_engine import AudioDeviceManager
        except Exception:  # noqa: BLE001
            AudioDeviceManager = None

        outer.addWidget(QLabel("Microphone"))
        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
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
            lambda _i: self._set_voice("voice_mic_device", self._voice_mic.currentData())
        )
        self._voice_widgets.append(self._voice_mic)
        mic_row.addWidget(self._voice_mic, 1)
        outer.addLayout(mic_row)
        outer.addSpacing(10)

        # Model ----------------------------------------------------------
        outer.addWidget(QLabel("Speech model"))
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self._voice_model = QComboBox()
        for name, (label, size) in voice_models.MODEL_LABELS.items():
            self._voice_model.addItem(label, name)
        self._voice_model.currentIndexChanged.connect(self._on_voice_model_pick)
        self._voice_widgets.append(self._voice_model)
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
        outer.addSpacing(10)

        # Language -----------------------------------------------------
        outer.addWidget(QLabel("Language"))
        lang_row = QHBoxLayout()
        lang_row.setSpacing(8)
        self._voice_lang = QComboBox()
        self._voice_lang.addItem("Auto-detect (needs a multilingual model)", "auto")
        self._voice_lang.addItem("English", "en")
        li = self._voice_lang.findData(self._config.get("voice_language", "auto"))
        self._voice_lang.setCurrentIndex(li if li >= 0 else 0)
        self._voice_lang.currentIndexChanged.connect(
            lambda _i: self._set_voice("voice_language", self._voice_lang.currentData())
        )
        self._voice_widgets.append(self._voice_lang)
        lang_row.addWidget(self._voice_lang, 1)
        outer.addLayout(lang_row)
        outer.addSpacing(10)

        # VAD sensitivity --------------------------------------------
        outer.addWidget(QLabel("Mic sensitivity"))
        vad_row = QHBoxLayout()
        vad_row.setSpacing(8)
        self._voice_vad = QComboBox()
        for label, val in self._VAD_LABELS:
            self._voice_vad.addItem(label, val)
        vi = self._voice_vad.findData(int(self._config.get("voice_vad_aggressiveness", 2) or 2))
        self._voice_vad.setCurrentIndex(vi if vi >= 0 else 1)
        self._voice_vad.currentIndexChanged.connect(
            lambda _i: self._set_voice(
                "voice_vad_aggressiveness", int(self._voice_vad.currentData())
            )
        )
        self._voice_widgets.append(self._voice_vad)
        vad_row.addWidget(self._voice_vad, 1)
        outer.addLayout(vad_row)
        outer.addSpacing(14)

        outer.addWidget(self._section("Transcript"))
        self._voice_post = self._voice_check(
            outer, "Tidy up each phrase (capitalise, drop the trailing period)",
            "voice_post_processing", True,
        )
        self._voice_partial = self._voice_check(
            outer, "Show words in the capsule as you speak",
            "voice_show_partial", True,
        )
        self._voice_cmds = self._voice_check(
            outer, "Recognise voice commands (“scratch that”, “send”, “new line”)",
            "voice_commands_enabled", True,
        )
        self._voice_punct = self._voice_check(
            outer, "Spoken punctuation (“period” → “.”)",
            "voice_spoken_punctuation", True,
        )
        self._voice_autosend = self._voice_check(
            outer, "Auto-send after each phrase (skips the review step)",
            "voice_auto_send", False,
        )
        self._voice_fixups = self._voice_check(
            outer, "Fix common command words — experimental (“get” → “git”)",
            "voice_command_fixups", False,
        )
        self._voice_fallback = self._voice_check(
            outer, "If the mic drops out, switch to the default device",
            "voice_mic_autofallback", True,
        )
        outer.addSpacing(14)

        # Global hotkey ------------------------------------------------
        outer.addWidget(self._section("Global hotkey"))
        self._voice_global = self._voice_check(
            outer, "Works when AgentDeck isn't in front",
            "voice_global_hotkey_enabled", True,
        )
        outer.addSpacing(8)

        outer.addWidget(QLabel("Hotkey"))
        hk_row = QHBoxLayout()
        hk_row.setSpacing(8)
        self._voice_hotkey = QKeySequenceEdit(
            QKeySequence(self._config.get("voice_hotkey", "Ctrl+Shift+X"))
        )
        self._voice_hotkey.setMaximumSequenceLength(1)
        self._voice_hotkey.editingFinished.connect(self._on_voice_hotkey_edited)
        self._voice_widgets.append(self._voice_hotkey)
        hk_row.addWidget(self._voice_hotkey, 1)
        outer.addLayout(hk_row)
        outer.addSpacing(8)

        outer.addWidget(QLabel("When pressed"))
        tgt_row = QHBoxLayout()
        tgt_row.setSpacing(8)
        self._voice_target = QComboBox()
        self._voice_target.addItem("Focus AgentDeck and dictate", "agentdeck")
        self._voice_target.addItem("Dictate into the foreground app", "foreground")
        ti = self._voice_target.findData(self._config.get("voice_global_target", "agentdeck"))
        self._voice_target.setCurrentIndex(ti if ti >= 0 else 0)
        self._voice_target.currentIndexChanged.connect(
            lambda _i: self._set_voice("voice_global_target", self._voice_target.currentData())
        )
        self._voice_widgets.append(self._voice_target)
        tgt_row.addWidget(self._voice_target, 1)
        outer.addLayout(tgt_row)

        vhint = QLabel(
            "Press Ctrl+Shift+X anywhere in AgentDeck to dictate into the focused "
            "pane. Say your command, review it, then press Enter to run."
        )
        vhint.setObjectName("hint")
        vhint.setWordWrap(True)
        outer.addWidget(vhint)

        self._voice_pro_hint = QLabel("Voice-to-text is part of AgentDeck Pro.")
        self._voice_pro_hint.setObjectName("hint")
        outer.addWidget(self._voice_pro_hint)
        self._apply_voice_pro_gate()

    def set_voice_pro(self, enabled: bool) -> None:
        """Re-gate the voice controls -- called whenever the plan resolves or
        changes while the panel is already built (it's built once and kept
        alive for the app's lifetime, unlike the old per-open dialog)."""
        enabled = bool(enabled)
        if enabled == self._voice_pro:
            return
        self._voice_pro = enabled
        self._apply_voice_pro_gate()

    def _apply_voice_pro_gate(self) -> None:
        for w in self._voice_widgets:
            w.setEnabled(self._voice_pro)
        if self._voice_pro_hint is not None:
            self._voice_pro_hint.setVisible(not self._voice_pro)

    def _set_voice(self, key: str, value) -> None:
        self._set(key, value)
        self.voice_settings_changed.emit()

    def _on_voice_hotkey_edited(self) -> None:
        seq = self._voice_hotkey.keySequence().toString(QKeySequence.PortableText)
        if seq:
            self._set_voice("voice_hotkey", seq)

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
        self._set_voice("voice_model", self._current_voice_model())
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
        the panel that owns the updater -- this only adds an inline status line
        and the manual trigger. Wired once for the panel's lifetime (it is
        built once and kept alive, unlike the old per-open modal dialog), so
        there is normally nothing to tear down; :meth:`teardown` exists for
        :class:`SettingsDialog`, which still builds a fresh panel per open.
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

    def teardown(self) -> None:
        """Drop the updater connections. Only needed by a short-lived owner
        (:class:`SettingsDialog`); the embedded panel lives for the app's
        session and never calls this."""
        for sig, slot in self._upd_conns:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._upd_conns = []

    # Old name, kept as an alias.
    _teardown_updates = teardown

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
        mode = button.property("theme_key")
        self._set("theme", mode)
        self.theme_changed.emit(mode)

    def _bump_font(self, delta: int) -> None:
        size = max(self._font_lo, min(self._font_hi, self._font_size + delta))
        if size == self._font_size:
            return
        self._font_size = size
        self._sync_font_label()
        self._set("font_size", size)
        self.font_size_changed.emit(size)

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
            QWidget#settingsPanel {{ background: {t('card_bg')}; }}
            QWidget#settingsNav {{
                background: {t('card_raised')};
                border-right: 1px solid {t('card_border')};
            }}
            QScrollArea#settingsScroll {{ background: transparent; border: none; }}
            QWidget#settingsPage {{ background: {t('card_bg')}; }}
            QScrollBar:vertical {{
                background: transparent; width: 10px; margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {t('card_border')}; border-radius: 5px; min-height: 28px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {t('text_muted')}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
            QLabel {{ color: {t('dialog_text')}; font-size: 12px; }}
            QLabel#h1 {{ font-size: 16px; font-weight: 700; }}
            QLabel#pageTitle {{ font-size: 14px; font-weight: 700; }}
            QLabel#section {{
                color: {t('text_muted')}; font-size: 10px; font-weight: 700;
                letter-spacing: 1px; padding-top: 2px;
            }}
            QLabel#hint {{ color: {t('text_muted')}; font-size: 11px; }}
            QLabel#fontValue {{ color: {t('dialog_text')}; font-size: 12px; font-weight: 600; }}
            QPushButton#navBtn {{
                background: transparent; color: {t('text_muted')};
                border: none; border-radius: 7px;
                padding: 9px 12px; font-size: 12px; font-weight: 600;
                text-align: left;
            }}
            QPushButton#navBtn:hover {{
                background: {t('surface_hover')}; color: {t('dialog_text')};
            }}
            QPushButton#navBtn:checked {{
                background: {t('accent_soft_bg')}; color: {blue};
            }}
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
            QKeySequenceEdit {{
                background: {t('card_raised')}; color: {t('dialog_text')};
                border: 1px solid {t('card_border')}; border-radius: 7px;
                padding: 6px 10px; font-size: 12px;
            }}
            QKeySequenceEdit:focus {{ border-color: {blue}; }}
            QProgressBar {{
                background: {t('card_raised')}; color: {t('dialog_text')};
                border: 1px solid {t('card_border')}; border-radius: 7px;
                text-align: center; font-size: 11px; min-height: 22px;
            }}
            QProgressBar::chunk {{ background: {blue}; border-radius: 6px; }}
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

    def apply_theme(self) -> None:
        """Re-run the stylesheet after an app-wide theme flip."""
        self._apply_style()


class SettingsDialog(QDialog):
    """A modal popup wrapping :class:`SettingsPanel`, plus a Done button.

    ``terminal_panel.py`` no longer opens Settings this way (the gear button
    embeds a :class:`SettingsPanel` in the main window instead, like Plugins /
    Notes) -- this stays for anything that still wants a traditional dialog,
    and for the test suite. Attribute access not found on the dialog itself
    falls through to the panel (``d._voice_model`` etc. keep working).
    """

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
        self.setWindowTitle("Settings")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._panel = SettingsPanel(
            config, self,
            updater=updater, current_version=current_version,
            voice_enabled=voice_enabled,
        )
        root.addWidget(self._panel, 1)

        foot = QWidget(self)
        foot.setObjectName("settingsFoot")
        foot.setStyleSheet(
            f"QWidget#settingsFoot {{ background: {theme.color('card_bg')};"
            f" border-top: 1px solid {theme.color('card_border')}; }}"
        )
        btn_row = QHBoxLayout(foot)
        btn_row.setContentsMargins(22, 10, 22, 14)
        btn_row.addStretch(1)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.setStyleSheet(
            f"QPushButton#primary {{ background: {theme.color('accent')};"
            f" color: {theme.color('on_accent')}; border-color: {theme.color('accent')};"
            " font-weight: 700; padding: 8px 18px; border-radius: 7px; }"
        )
        done.clicked.connect(self.accept)
        btn_row.addWidget(done)
        root.addWidget(foot)

        self.setMinimumWidth(660)
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """Size to the tallest category, never taller than the screen the
        parent window (if any) actually lives on -- a plain ``self.screen()``
        before the dialog has been placed can resolve to the wrong monitor in
        a multi-display setup."""
        parent = self.parentWidget()
        screen = None
        if parent is not None and parent.window() is not None:
            screen = parent.window().screen()
        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        avail_h = geo.height() if geo else 900
        avail_w = geo.width() if geo else 1200
        content = self._panel.content_height_hint() + 64  # + footer
        width = min(720, max(self.minimumWidth(), avail_w - 120))
        self.resize(width, min(content, max(420, avail_h - 120)))

    def done(self, result: int) -> None:            # noqa: D102 - Qt override
        self._panel.teardown()
        super().done(result)

    def closeEvent(self, event):                     # noqa: D102 - Qt override
        self._panel.teardown()
        super().closeEvent(event)

    def __getattr__(self, name):
        # Anything not found on the dialog itself (the old flat attribute
        # surface -- _voice_model, _stack, _bump_font, ...) lives on the panel.
        return getattr(self.__dict__["_panel"], name)
