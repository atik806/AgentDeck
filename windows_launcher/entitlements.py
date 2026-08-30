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

__all__ = [
    "UPGRADE_URL",
    "FREE_MAX_WORKSPACES",
    "FREE_MAX_PANES",
    "PRO_MAX_PANES",
    "is_pro",
    "max_workspaces",
    "max_panes",
    "voice_enabled",
    "cloud_sync_enabled",
    "auto_update_enabled",
    "per_workspace_config_enabled",
    "upgrade_hint",
]

UPGRADE_URL = "https://vibeflow.tech/agentdeck"

FREE_MAX_WORKSPACES = 1
FREE_MAX_PANES = 4
#: Kept in step with ``workspace.MAX_PANES`` -- the hard ceiling for everyone.
PRO_MAX_PANES = 16

#: Every profile.plan value that unlocks Pro. Matches ``navbar.AccountChip``.
_PRO_PLANS = frozenset({"pro", "paid", "team", "plus"})


def is_pro(plan: str | None) -> bool:
    """True when ``plan`` grants Pro features."""
    return str(plan or "").strip().lower() in _PRO_PLANS


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
