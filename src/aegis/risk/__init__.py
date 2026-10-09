"""Deterministic Risk Engine — sole new-trade APPROVE/REJECT authority."""

from aegis.risk.context import RiskContext
from aegis.risk.engine import evaluate_risk
from aegis.risk.factory import build_risk_policy_from_settings
from aegis.risk.handoff import (
    RiskHandoffDenied,
    assert_may_submit_to_order_manager,
    may_submit_to_order_manager,
)
from aegis.risk.persist import persist_risk_decision
from aegis.risk.policy import (
    FINANCIAL_PARAM_IDS,
    NEW_TRADE_REQUIRED_PARAM_IDS,
    ParamStatus,
    PolicyParam,
    RiskPolicy,
    default_draft_policy,
    require_approved,
    with_approved_params,
)

__all__ = [
    "FINANCIAL_PARAM_IDS",
    "NEW_TRADE_REQUIRED_PARAM_IDS",
    "ParamStatus",
    "PolicyParam",
    "RiskContext",
    "RiskHandoffDenied",
    "RiskPolicy",
    "assert_may_submit_to_order_manager",
    "build_risk_policy_from_settings",
    "default_draft_policy",
    "evaluate_risk",
    "may_submit_to_order_manager",
    "persist_risk_decision",
    "require_approved",
    "with_approved_params",
]
