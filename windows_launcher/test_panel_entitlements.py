"""Offline coverage for the Free/Pro gates wired into the panel.

Like ``test_panel_account.py`` this spins a real ``TerminalPanel`` (real
shells), so it ends in ``os._exit``.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_panel_entitlements.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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
import entitlements

_cfg = dict(DEFAULT_CONFIG)
_cfg["default_count"] = 3

acct = AccountController(_cfg)          # inert, plan == "free"
panel = TerminalPanel(_cfg, persist_settings=False, account=acct)
panel.resize(1200, 700)
panel.show()
for _ in range(5):
    app.processEvents()

# Never let the upsell dialog block the run.
_prompts = []
panel._prompt_upgrade = lambda feature, detail="": _prompts.append(feature)

print("[1] a Free panel opens one workspace capped at 4 panes")
check("one workspace", len(panel._workspaces) == 1)
check("workspace pane cap is 4", panel._workspaces[0].max_panes == entitlements.FREE_MAX_PANES)
check("started with default_count panes (<= cap)", panel._workspaces[0].pane_count == 3)

print("[2] Free can't add a 5th pane")
ws = panel._workspaces[0]
while ws.pane_count < ws.max_panes:
    ws.add_pane(focus=False)
check("filled to the cap", ws.pane_count == 4)
before = ws.pane_count
ws.add_pane(focus=False)
check("add_pane past the cap is a no-op", ws.pane_count == before)

print("[3] Free can't open a second workspace")
n = len(panel._workspaces)
panel._new_workspace_interactive()
check("no workspace created", len(panel._workspaces) == n)
check("upsell was shown", _prompts and "workspace" in _prompts[-1].lower())

print("[4] voice input is gated for Free")
_prompts.clear()
check("_voice_gated() true for Free", panel._voice_gated() is True)
check("voice upsell shown", _prompts == ["Voice-to-text input"])

print("[5] the plan resolving to Pro lifts every cap")
panel.account._plan = "pro"
panel._apply_entitlements()
check("workspace cap raised to 16",
      panel._workspaces[0].max_panes == entitlements.PRO_MAX_PANES)
_prompts.clear()
check("_voice_gated() false for Pro", panel._voice_gated() is False)
check("no upsell for Pro", _prompts == [])
panel._new_workspace_interactive  # (not calling — needs the modal dialog)

print("[5b] a lapsed plan_expires_at drops the panel back to Free limits")
from datetime import datetime, timedelta, timezone as _tz

panel.account._plan = "pro"
panel.account._plan_expires_at = (datetime.now(_tz.utc) - timedelta(days=1)).isoformat()
panel._apply_entitlements()
check("effective plan is free once expired", panel.account.plan == "free")
check("workspace cap back to 4",
      panel._workspaces[0].max_panes == entitlements.FREE_MAX_PANES)
_prompts.clear()
check("_voice_gated() true again for a lapsed plan", panel._voice_gated() is True)

panel.account._plan_expires_at = (datetime.now(_tz.utc) + timedelta(days=30)).isoformat()
panel._apply_entitlements()
check("a future expiry keeps Pro caps",
      panel._workspaces[0].max_panes == entitlements.PRO_MAX_PANES)
panel.account._plan_expires_at = None

print("[6] a Pro panel starts its first workspace at default_count")
acct2 = AccountController(_cfg)
acct2._plan = "pro"
panel2 = TerminalPanel(_cfg, persist_settings=False, account=acct2)
for _ in range(5):
    app.processEvents()
check("first workspace has default_count panes", panel2._workspaces[0].pane_count == 3)
check("Pro cap on that workspace", panel2._workspaces[0].max_panes == entitlements.PRO_MAX_PANES)

print("[7] the free-trial gate + countdown banner")
panel.account._plan = "free"
panel.account._plan_expires_at = None
panel.account._session = object()  # make is_signed_in truthy for the gate

# banner shows in the last 3 days
panel.account._trial_ends_at = (datetime.now(_tz.utc) + timedelta(days=2, hours=1)).isoformat()
panel.config["trial_banner_dismissed_on"] = 0
panel._refresh_trial_banner()
check("banner visible with 2 days left", panel._trial_banner.isVisibleTo(panel))
check("banner text mentions 2 days", "2 days" in panel._trial_banner._label.text())

# ...but not earlier than that
panel.account._trial_ends_at = (datetime.now(_tz.utc) + timedelta(days=6)).isoformat()
panel._refresh_trial_banner()
check("no banner with 6 days left", not panel._trial_banner.isVisibleTo(panel))

# a dismissal for today hides it
panel.account._trial_ends_at = (datetime.now(_tz.utc) + timedelta(days=1, hours=1)).isoformat()
import time as _time
panel.config["trial_banner_dismissed_on"] = int(_time.time() // 86400)
panel._refresh_trial_banner()
check("dismissed-today hides the banner", not panel._trial_banner.isVisibleTo(panel))
panel.config["trial_banner_dismissed_on"] = 0

# an ended trial on a Free account triggers the hard gate
_blocked = []
panel._enforce_trial_block = lambda: _blocked.append(1)
panel.account._trial_ends_at = (datetime.now(_tz.utc) - timedelta(minutes=1)).isoformat()
check("access denied once the trial ended", panel.account.access_allowed is False)
panel.setVisible(True)
panel._apply_entitlements()
check("_apply_entitlements invoked the trial gate", _blocked == [1])

# an active Pro plan sails past the gate
panel.account._plan = "pro"
panel.account._plan_expires_at = (datetime.now(_tz.utc) + timedelta(days=30)).isoformat()
_blocked.clear()
panel._apply_entitlements()
check("active Pro is never gated by an ended trial", _blocked == [])

print()
print(f"{_passed} passed, {_failed} failed")
sys.stdout.flush()
os._exit(1 if _failed else 0)
