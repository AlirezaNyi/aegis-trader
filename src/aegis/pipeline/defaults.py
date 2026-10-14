"""Derive code-owned proposal fields from the approved risk policy.

Only APPROVED owner values are used. Nothing here invents a number: when a
parameter is missing or unapproved, the field is left empty so the Risk Engine
rejects the proposal (fail closed).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from aegis.risk.policy import (
    SIZING_METHOD_PCT_CAPITAL,
    SIZING_METHOD_RISK_PCT_CAPITAL,
    ParamStatus,
    RiskPolicy,
)
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


def _approved_decimal(policy: RiskPolicy, param_id: str) -> Decimal | None:
    value = _approved_value(policy, param_id)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def proposal_defaults_from_policy(
    policy: RiskPolicy,
    *,
    fee_bps: Decimal | None = None,
    slippage_bps: Decimal | None = None,
    spread_bps: Decimal | None = None,
) -> ProposalDefaults:
    """Code-owned proposal fields from APPROVED policy values only.

    ``fee_bps``/``slippage_bps``/``spread_bps`` describe the round-trip cost model
    used for risk-based sizing and the prompt hint; the Risk Engine recomputes
    costs from its own context. Missing inputs leave fields empty (fail closed).
    """
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
    elif method in {SIZING_METHOD_PCT_CAPITAL, SIZING_METHOD_RISK_PCT_CAPITAL}:
        # Notional is computed after the decision from the stop distance.
        sizing = {"method": method}

    cost_bps: Decimal | None = None
    if fee_bps is not None and slippage_bps is not None and spread_bps is not None:
        cost_bps = Decimal(2) * fee_bps + spread_bps + Decimal(2) * slippage_bps

    ttl = _FALLBACK_TTL
    ttl_ms = _approved_value(policy, "RP-PROPOSAL-TTL-MS")
    if ttl_ms is not None:
        ttl = timedelta(milliseconds=int(ttl_ms))

    max_stop_pct: Decimal | None = None
    stop_param = policy.get("RP-STOP-LOSS-MAX-DISTANCE")
    if (
        stop_param is not None
        and stop_param.status == ParamStatus.APPROVED
        and stop_param.value is not None
        and stop_param.unit in {"%", "percent", "pct"}
    ):
        max_stop_pct = Decimal(str(stop_param.value))

    return ProposalDefaults(
        entry_conditions=entry_conditions,
        sizing=sizing,
        ttl=ttl,
        max_stop_distance_pct=max_stop_pct,
        position_cap_usdt=_approved_decimal(policy, "RP-MAX-NOTIONAL-PER-ORDER"),
        risk_budget_usdt=_approved_decimal(policy, "RP-MAX-RISK-PER-TRADE"),
        cost_bps_round_trip=cost_bps,
        min_reward_risk=_approved_decimal(policy, "RP-MIN-REWARD-RISK"),
    )
