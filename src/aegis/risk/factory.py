"""Risk policy factory — draft only until owner-approved loader exists."""

from __future__ import annotations

from aegis.config.settings import Settings
from aegis.risk.policy import RiskPolicy, default_draft_policy


def build_risk_policy_from_settings(settings: Settings) -> RiskPolicy:
    """Always return the draft policy. Do not invent numeric env limits."""
    _ = settings
    return default_draft_policy()
