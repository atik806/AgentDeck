"""Offline tests for entitlements.py -- the Free vs Pro gate.

    .venv\\Scripts\\python.exe test_entitlements.py
"""

import math
import sys
from datetime import datetime, timedelta, timezone

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

print("[6] plan_active folds in the expiry date")
_past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
_future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
check("pro + no expiry -> active", e.plan_active("pro", None) is True)
check("pro + future expiry -> active", e.plan_active("pro", _future) is True)
check("pro + past expiry -> not active", e.plan_active("pro", _past) is False)
check("free + future expiry -> still not active", e.plan_active("free", _future) is False)
check("pro + trailing-Z expiry parses",
      e.plan_active("pro", "2099-01-01T00:00:00Z") is True)
check("pro + past trailing-Z expiry parses",
      e.plan_active("pro", "2000-01-01T00:00:00.500Z") is False)
check("pro + naive (assumed UTC) future string",
      e.plan_active("pro", "2099-01-01T00:00:00") is True)
check("pro + garbage expiry -> treated as no expiry",
      e.plan_active("pro", "not a date") is True)
check("explicit now= is honoured",
      e.plan_active("pro", _future,
                    now=datetime.now(timezone.utc) + timedelta(days=2)) is False)
check("plan_expiry(None) is None", e.plan_expiry(None) is None)
check("plan_expiry('') is None", e.plan_expiry("") is None)
check("plan_expiry returns an aware datetime",
      e.plan_expiry(_future) is not None and e.plan_expiry(_future).tzinfo is not None)

print("[7] the free trial gate")
_t_past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
_t_future = (datetime.now(timezone.utc) + timedelta(days=3, hours=2)).isoformat()
check("trial_active(None) -> True (fail-open)", e.trial_active(None) is True)
check("trial_active(garbage) -> True (fail-open)", e.trial_active("nope") is True)
check("trial_active(future) -> True", e.trial_active(_t_future) is True)
check("trial_active(past) -> False", e.trial_active(_t_past) is False)
check("trial_days_left(None) is None", e.trial_days_left(None) is None)
check("trial_days_left(future ~3d) == 3",
      e.trial_days_left(_t_future) == 3)
check("trial_days_left(past) is negative",
      e.trial_days_left(_t_past) < 0)
check("TRIAL_DAYS is 7", e.TRIAL_DAYS == 7)
check("access_allowed(free, future trial) -> True",
      e.access_allowed("free", _t_future) is True)
check("access_allowed(free, ended trial) -> False",
      e.access_allowed("free", _t_past) is False)
check("access_allowed(pro, ended trial) -> True (active Pro overrides)",
      e.access_allowed("pro", _t_past) is True)
check("access_allowed(pro + lapsed plan, ended trial) -> False",
      e.access_allowed("pro", _t_past, _past) is False)
check("access_allowed(pro + live plan, ended trial) -> True",
      e.access_allowed("pro", _t_past, _future) is True)

print("[5] PRO_MAX_PANES stays in step with workspace.MAX_PANES")
try:
    from workspace import MAX_PANES
    check("PRO_MAX_PANES == workspace.MAX_PANES", e.PRO_MAX_PANES == MAX_PANES)
except Exception as exc:  # noqa: BLE001 - Qt may be unavailable in isolation
    print(f"  skip  workspace import ({exc})")

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
