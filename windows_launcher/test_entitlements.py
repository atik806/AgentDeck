"""Offline tests for entitlements.py -- the Free vs Pro gate.

    .venv\\Scripts\\python.exe test_entitlements.py
"""

import math
import sys

import entitlements as e

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


print("[1] is_pro recognises the paid plan names, nothing else")
for p in ("pro", "Pro", "PRO", "paid", "team", "plus", " pro "):
    check(f"{p!r} -> pro", e.is_pro(p) is True)
for p in ("free", "", None, "trial", "expired", "basic", "pending"):
    check(f"{p!r} -> free", e.is_pro(p) is False)

print("[2] workspace + pane caps")
check("free: 1 workspace", e.max_workspaces("free") == 1)
check("pro: unlimited workspaces", e.max_workspaces("pro") == math.inf)
check("free: 4 panes", e.max_panes("free") == 4)
check("pro: 16 panes", e.max_panes("pro") == 16)
check("free pane cap matches FREE_MAX_PANES", e.max_panes("free") == e.FREE_MAX_PANES)

print("[3] feature gates")
check("voice: pro only", e.voice_enabled("pro") and not e.voice_enabled("free"))
check("cloud sync: pro only",
      e.cloud_sync_enabled("pro") and not e.cloud_sync_enabled("free"))
check("auto-update: pro only",
      e.auto_update_enabled("pro") and not e.auto_update_enabled("free"))
check("per-workspace config: pro only",
      e.per_workspace_config_enabled("pro") and not e.per_workspace_config_enabled("free"))

print("[4] upgrade hint carries the pricing URL")
check("hint mentions the feature", "Voice" in e.upgrade_hint("Voice"))
check("hint carries the URL", e.UPGRADE_URL in e.upgrade_hint("x"))
check("URL is the AgentDeck pricing page",
      e.UPGRADE_URL == "https://vibeflow.tech/agentdeck")

print("[5] PRO_MAX_PANES stays in step with workspace.MAX_PANES")
try:
    from workspace import MAX_PANES
    check("PRO_MAX_PANES == workspace.MAX_PANES", e.PRO_MAX_PANES == MAX_PANES)
except Exception as exc:  # noqa: BLE001 - Qt may be unavailable in isolation
    print(f"  skip  workspace import ({exc})")

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
