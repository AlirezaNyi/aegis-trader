"""Risk policy factory — loads owner-approved versions or draft."""

from __future__ import annotations

from aegis.config.settings import Settings
from aegis.risk.owner_v1 import OWNER_V1_VERSION, owner_approved_policy_v1
from aegis.risk.policy import RiskPolicy, default_draft_policy


def build_risk_policy_from_settings(settings: Settings) -> RiskPolicy:
    """Load policy by ``AEGIS_RISK_POLICY_VERSION``.

    Unknown versions fail closed to the draft (no invented numbers).
    Live still requires non-draft + live gates; this factory never arms live.
    """
    version = settings.risk_policy_version.strip().lower()
    if version in {OWNER_V1_VERSION, "1", "1.0"}:
        return owner_approved_policy_v1()
    if version in {"", "draft", "0.1-draft"}:
        return default_draft_policy()
    # Fail closed: do not invent an unknown version's numbers.
    return default_draft_policy()
