"""Desktop notifications for "a terminal needs you".

With several panes running agents, the one that stopped to ask a question --
or crashed, or finished -- is easy to miss when it isn't the pane on screen.
:class:`AttentionNotifier` turns a :mod:`pane_state` transition into:

* a **Windows toast** (via ``QSystemTrayIcon`` -- no extra dependency), when a
  system tray is available; clicking it raises AgentDeck and focuses the pane;
* a **taskbar flash** (``QApplication.alert``) always, so a minimised window
  still catches the eye;
* an optional short **beep**.

It is deliberately quiet: notifications only fire for a pane the user *isn't*
already looking at, each ``(key, state)`` pair is rate-limited, and the whole
thing is a no-op when the feature is switched off in Settings.

``TerminalPanel`` owns one instance, calls :meth:`configure` from
``_apply_entitlements`` / settings changes, and :meth:`notify` from
``_refresh_status`` when a pane changes state. The ``activated`` signal carries
the ``key`` (a pane id) back so the panel can focus that pane.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

__all__ = ["AttentionNotifier"]

#: Don't re-toast the same key more often than this (seconds). A pane that
#: flip-flops working/awaiting shouldn't machine-gun the user.
_THROTTLE_S = 12.0

#: How long the toast linger (ms). The OS may override.
_TOAST_MS = 6000


class AttentionNotifier(QObject):
    """Rate-limited desktop notifications for pane-state changes."""

    #: The user clicked a toast; carries the ``key`` passed to :meth:`notify`.
    activated = Signal(str)

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        icon: Optional[QIcon] = None,
        app_name: str = "AgentDeck",
    ):
        super().__init__(parent)
        self._enabled = False
        self._sound = False
        self._app_name = app_name
        self._icon = icon or QIcon()
        self._last: dict[str, float] = {}
        self._last_key: str = ""
        self._tray: Optional[QSystemTrayIcon] = None
        self._window = None

    def set_main_window(self, window) -> None:
        """The window to flash / raise. ``TerminalPanel`` sets this once."""
        self._window = window

    # -- configuration ---------------------------------------------------

    def configure(self, *, enabled: bool, sound: bool = False) -> None:
        self._enabled = bool(enabled)
        self._sound = bool(sound)
        if not self._enabled and self._tray is not None:
            self._tray.hide()

    @property
    def enabled(self) -> bool:
        return self._enabled

    # -- the one entry point -------------------------------------------

    def notify(self, key: str, title: str, body: str = "") -> bool:
        """Raise a notification for ``key``.

        Returns whether the notification was **dispatched** -- i.e. it passed
        the enable switch and the per-key throttle. Returns ``False`` (a no-op)
        when the feature is off or the same key fired within the throttle
        window. Whether a toast physically appears also depends on the OS having
        a system tray; that is not reflected here.
        """
        if not self._enabled:
            return False
        now = time.monotonic()
        prev = self._last.get(key, 0.0)
        if now - prev < _THROTTLE_S:
            return False
        self._last[key] = now
        self._last_key = key

        self._toast(title, body)
        self._flash_taskbar()
        if self._sound:
            QApplication.beep()
        return True

    def forget(self, key: str) -> None:
        """Drop a key's throttle history -- e.g. when its pane closes."""
        self._last.pop(key, None)

    # -- mechanics ----------------------------------------------------

    def _toast(self, title: str, body: str) -> bool:
        tray = self._ensure_tray()
        if tray is None:
            return False
        try:
            tray.showMessage(
                title or self._app_name,
                body,
                QSystemTrayIcon.MessageIcon.Information,
                _TOAST_MS,
            )
            return True
        except Exception:  # noqa: BLE001 - never let a toast crash a refresh tick
            return False

    def _ensure_tray(self) -> Optional[QSystemTrayIcon]:
        if self._tray is not None:
            if not self._tray.isVisible():
                self._tray.show()
            return self._tray
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self._icon, self)
        tray.setToolTip(self._app_name)
        menu = QMenu()
        menu.addAction("Show AgentDeck", lambda: self.activated.emit(self._last_key))
        tray.setContextMenu(menu)
        tray.messageClicked.connect(lambda: self.activated.emit(self._last_key))
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray = tray
        return tray

    def _on_tray_activated(self, reason) -> None:
        # A left click (Trigger) or double click on the tray icon raises the app.
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.activated.emit(self._last_key)

    def _flash_taskbar(self) -> bool:
        app = QApplication.instance()
        if app is None or self._window is None:
            return False
        try:
            # No flash when the window is already the foreground window --
            # the user is looking at AgentDeck, just not this pane.
            if not self._window.isActiveWindow():
                app.alert(self._window, 2000)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def shutdown(self) -> None:
        if self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None
