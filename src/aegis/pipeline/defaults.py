"""Derive code-owned proposal fields from the approved risk policy.

Only APPROVED owner values are used. Nothing here invents a number: when a
parameter is missing or unapproved, the field is left empty so the Risk Engine
rejects the proposal (fail closed).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from aegis.risk.policy import ParamStatus, RiskPolicy
from aegis.supervisor.decision import ProposalDefaults

_FALLBACK_TTL = timedelta(minutes=5)


def _approved_value(policy: RiskPolicy, param_id: str) -> Any | None:
    param = policy.get(param_id)
    if param is None or param.status != ParamStatus.APPROVED:
        return None
    return param.value


def _single_choice(value: Any) -> str | None:
    """Return the only allowed option; ambiguous sets are left to the owner."""
    if isinstance(value, str):
        return value
    if isinstance(value, (set, frozenset, list, tuple)) and len(value) == 1:
        return str(next(iter(value)))
    return None


def proposal_defaults_from_policy(policy: RiskPolicy) -> ProposalDefaults:
    entry_conditions: dict[str, Any] = {}
    order_type = _single_choice(_approved_value(policy, "RP-ORDER-TYPES-ALLOWED"))
    if order_type is not None:
        entry_conditions["order_type"] = order_type
    tif = _single_choice(_approved_value(policy, "RP-TIME-IN-FORCE-ALLOWED"))
    if tif is not None:
        entry_conditions["time_in_force"] = tif

    sizing: dict[str, Any] = {}
    method = _approved_value(policy, "RP-SIZING-METHOD")
    if method == "fixed_notional":
        notional = _approved_value(policy, "RP-MAX-NOTIONAL-PER-ORDER")
        if notional is not None:
            sizing = {"method": "fixed_notional", "notional": str(notional)}

    ttl = _FALLBACK_TTL
    ttl_ms = _approved_value(policy, "RP-PROPOSAL-TTL-MS")
    if ttl_ms is not None:
        ttl = timedelta(milliseconds=int(ttl_ms))

    return ProposalDefaults(entry_conditions=entry_conditions, sizing=sizing, ttl=ttl)
