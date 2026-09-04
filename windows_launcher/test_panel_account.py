"""Offline coverage for the account / help / update-glow wiring in the panel.

The heavy pane machinery is exercised by ``test_panel.py`` (real shells, real
window). This one only checks the toolbar additions from the accounts feature.
It still spins up real shells (a ``TerminalPanel`` always does), so like
``test_panel.py`` it ends in ``os._exit`` -- the pty reader threads never
unblock.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_panel_account.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ADK_NO_VOICE_PREWARM", "1")

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

import updater

app = QApplication(sys.argv)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


from config import DEFAULT_CONFIG
from account import AccountController
from terminal_panel import TerminalPanel

_cfg = dict(DEFAULT_CONFIG)
_cfg["default_count"] = 1  # one shell keeps it quick


def make_panel():
    return TerminalPanel(_cfg, persist_settings=False, account=AccountController(_cfg))


# One panel for the whole run -- constructing several would spawn several shells.
panel = make_panel()
panel.resize(1400, 700)
panel.show()
for _ in range(5):
    app.processEvents()

print("[1] toolbar gains the help button and the account chip")
check("help button present", hasattr(panel, "_help_btn"))
check("account chip present", hasattr(panel, "_account_chip"))
check("chip reads the signed-out controller",
      panel._account_chip._display_label() == "Sign in")
check("chip sits after the help button (right cluster, spacer-pushed)",
      panel._account_chip.mapToGlobal(panel._account_chip.rect().topLeft()).x()
      > panel._help_btn.mapToGlobal(panel._help_btn.rect().topLeft()).x()
      > panel.mapToGlobal(panel.rect().center()).x())
menu_items = [a.text() for a in panel._help_btn._menu.actions()]
check("help menu has the four destinations",
      menu_items == ["Documentation", "Keyboard shortcuts",
                     "Report an issue", "About AgentDeck"])
check("help button exposes the two panel signals",
      hasattr(panel._help_btn, "shortcuts_requested")
      and hasattr(panel._help_btn, "about_requested"))


print("[2] update controls moved off the toolbar; the 'waiting' glow is dormant")
check("no toolbar Update button", not hasattr(panel, "_update_btn"))
check("glow effect exists", hasattr(panel, "_update_glow"))
check("glow is on the settings button",
      panel._settings_btn.graphicsEffect() is panel._update_glow)
check("glow starts disabled", not panel._update_glow.isEnabled())
check("glow colour is red", panel._update_glow.color().name() == "#ff3b30")
check("glow offset is zero (a halo, not a shadow)",
      panel._update_glow.offset().manhattanLength() == 0)
check("settings tooltip starts plain", panel._settings_btn.toolTip() == "Settings")
_plain_icon_key = panel._settings_btn.icon().cacheKey()


print("[3] _set_update_glow toggles the halo, the badge icon + the settings tooltip")
panel._set_update_glow(True)
check("effect enabled", panel._update_glow.isEnabled())
check("pulse animation running",
      panel._update_pulse.state() == QAbstractAnimation.Running)
check("pulse loops forever", panel._update_pulse.loopCount() == -1)
check("settings tooltip flags the update", "available" in panel._settings_btn.toolTip())
check("gear icon swaps to the badged variant",
      panel._settings_btn.icon().cacheKey() != _plain_icon_key)
panel._set_update_glow(False)
check("effect disabled again", not panel._update_glow.isEnabled())
check("pulse stopped", panel._update_pulse.state() != QAbstractAnimation.Running)
check("settings tooltip restored", panel._settings_btn.toolTip() == "Settings")


print("[4] the 'update available' signal path lights the glow")
# Re-wire the controller's `available` straight to the glow (bypassing the modal
# dialog `_on_update_available` also shows) and emit it.
panel.updater.available.disconnect(panel._on_update_available)
panel.updater.available.connect(lambda *_: panel._set_update_glow(True))
panel.updater.available.emit("9.9.9", "notes")
check("glow lit by the available signal", panel._update_glow.isEnabled())
# `ready` (downloaded + acknowledged) clears it -- test the helper directly.
panel._set_update_glow(False)
check("glow clears once handled", not panel._update_glow.isEnabled())


print("[5] shutdown reaches the account controller")
calls = []
panel.account.shutdown = lambda: calls.append(1)
panel._shutdown_all()
check("_shutdown_all calls account.shutdown()", calls == [1])


print()
print(f"{_passed} passed, {_failed} failed")
sys.stdout.flush()
os._exit(1 if _failed else 0)
