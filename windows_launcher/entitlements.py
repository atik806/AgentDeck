"""Plan-based feature gating -- the single source of truth for Free vs Pro.

Mirrors the pricing page at https://vibeflow.tech/agentdeck :

    Free                          Pro
    ----                          ---
    1 workspace                   unlimited workspaces & panes
    up to 4 terminal panes        voice-to-text input (Ctrl+Shift+X)
    all 12 coding agents          cloud settings & profile sync
    grid / column / row layouts   one-click background auto-updates
    manual updates                per-workspace folders & agents
    community support             priority email support

Qt-free on purpose (same rule as ``agents.py`` / ``supabase_auth.py``): plain
functions over a plan string, so it can be unit-tested headless and imported
from anywhere without pulling Qt in. The plan string comes from
``AccountController.plan`` (the ``plan`` column of ``public.profiles``).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

__all__ = [
    "UPGRADE_URL",
    "FREE_MAX_WORKSPACES",
    "FREE_MAX_PANES",
    "PRO_MAX_PANES",
    "TRIAL_DAYS",
    "is_pro",
    "plan_active",
    "plan_expiry",
    "trial_deadline",
    "trial_days_left",
    "trial_active",
    "access_allowed",
    "max_workspaces",
    "max_panes",
    "voice_enabled",
    "cloud_sync_enabled",
    "auto_update_enabled",
    "per_workspace_config_enabled",
    "upgrade_hint",
]

UPGRADE_URL = "https://vibeflow.tech/agentdeck"

#: The Free tier is a trial: this many days of Free-tier use from signup, then an
#: active Pro plan is required for the app to open. See :func:`access_allowed`.
TRIAL_DAYS = 7

FREE_MAX_WORKSPACES = 1
FREE_MAX_PANES = 4
#: Kept in step with ``workspace.MAX_PANES`` -- the hard ceiling for everyone.
PRO_MAX_PANES = 16

#: Every profile.plan value that unlocks Pro. Matches ``navbar.AccountChip``.
_PRO_PLANS = frozenset({"pro", "paid", "team", "plus"})


def is_pro(plan: str | None) -> bool:
    """True when ``plan`` is a paid plan *name*.

    Name only -- this does not consider whether the subscription has lapsed. For
    the "is this account actually entitled to Pro right now" question use
    :func:`plan_active`, which the app's ``AccountController.plan`` funnels
    through.
    """
    return str(plan or "").strip().lower() in _PRO_PLANS


def plan_expiry(expires_at: object) -> "datetime | None":
    """Parse a ``profiles.plan_expires_at`` value to an aware UTC datetime.

    Accepts ``None`` / ``""`` (-> ``None`` = never expires), a ``datetime``, or
    an ISO-8601 string (``2026-09-01T00:00:00+00:00``, a trailing ``Z``, with or
    without microseconds, or a naive string which is assumed to be UTC).
    Anything unparseable is treated as ``None`` rather than raising.
    """
    if expires_at is None or expires_at == "":
        return None
    if isinstance(expires_at, datetime):
        dt = expires_at
    else:
        text = str(expires_at).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utcnow(now: "datetime | None") -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now


def plan_active(
    plan: str | None,
    expires_at: object = None,
    *,
    now: "datetime | None" = None,
) -> bool:
    """True when ``plan`` is a Pro plan that has not passed its expiry.

    ``expires_at`` of ``None`` means the grant never expires (comps / team /
    lifetime). A past timestamp means Pro has lapsed and the account is Free.
    """
    if not is_pro(plan):
        return False
    exp = plan_expiry(expires_at)
    if exp is None:
        return True
    return _utcnow(now) < exp


#: ``trial_ends_at`` from the profile row parses exactly like ``plan_expires_at``.
trial_deadline = plan_expiry


def trial_days_left(trial_ends_at: object, *, now: "datetime | None" = None) -> "int | None":
    """Whole 24 h periods until the trial ends (floor).

    ``None`` when there is no deadline; ``0`` on the final day; negative once the
    trial has passed. Display only -- use :func:`trial_active` for the gate.
    """
    exp = trial_deadline(trial_ends_at)
    if exp is None:
        return None
    return math.floor((exp - _utcnow(now)).total_seconds() / 86400)


def trial_active(trial_ends_at: object, *, now: "datetime | None" = None) -> bool:
    """True while the account is still inside its free trial.

    **Fail-open:** a missing / unparseable ``trial_ends_at`` returns ``True`` --
    an old client, or a profile that never loaded, must never lock a user out.
    """
    exp = trial_deadline(trial_ends_at)
    if exp is None:
        return True
    return _utcnow(now) < exp


def access_allowed(
    plan: str | None,
    trial_ends_at: object,
    plan_expires_at: object = None,
    *,
    now: "datetime | None" = None,
) -> bool:
    """The master gate: may this account use AgentDeck at all right now?

    ``True`` when the account is on an active Pro plan (:func:`plan_active`) or
    still inside its trial (:func:`trial_active`).
    """
    return plan_active(plan, plan_expires_at, now=now) or trial_active(
        trial_ends_at, now=now
    )


def max_workspaces(plan: str | None) -> float:
    """How many workspaces this plan may open at once (``inf`` for Pro)."""
    return math.inf if is_pro(plan) else FREE_MAX_WORKSPACES


def max_panes(plan: str | None) -> int:
    """How many terminal panes a workspace may hold on this plan."""
    return PRO_MAX_PANES if is_pro(plan) else FREE_MAX_PANES


def voice_enabled(plan: str | None) -> bool:
    """Voice-to-text input (Ctrl+Shift+X) -- Pro only."""
    return is_pro(plan)


def cloud_sync_enabled(plan: str | None) -> bool:
    """Mirroring settings to / from the account (user_settings) -- Pro only.

    The profile itself (name, avatar, plan badge) is always fetched; only the
    *settings* push/pull is gated.
    """
    return is_pro(plan)


def auto_update_enabled(plan: str | None) -> bool:
    """Background 'check for updates on launch' -- Pro only.

    Free keeps the manual Update button; it just never checks on its own.
    """
    return is_pro(plan)


def per_workspace_config_enabled(plan: str | None) -> bool:
    """Per-workspace folders & agents -- Pro only (Free has one workspace)."""
    return is_pro(plan)


def upgrade_hint(feature: str) -> str:
    """A one-line status-bar nudge for a gated ``feature``."""
    return f"{feature} is a Pro feature — upgrade at {UPGRADE_URL}"
