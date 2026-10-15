"""Deterministic research strategies (no I/O, no LLM, no order authority)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aegis.schemas.evidence import EvidencePackage
from aegis.strategy.rules import (
    BUY_RATIONALE,
    NO_TRADE_PREFIX,
    STRATEGY_ID,
    STRATEGY_VERSION,
    NoTradeReason,
    RuleParams,
    decide,
)
from aegis.strategy.rules_v2 import (
    ALLOWED_REGIME_N,
    BUY_RATIONALE_V2,
    STRATEGY_ID_V2,
    STRATEGY_VERSION_V2,
    NoTradeReasonV2,
    RuleParamsV2,
    decide_v2,
)
from aegis.supervisor.decision import SupervisorDecision

STRATEGY_KEY = "strategy"


def decide_any(package: EvidencePackage, params: RuleParams | RuleParamsV2) -> SupervisorDecision:
    """Dispatch to :func:`decide` (rules-v1) or :func:`decide_v2` by params type."""
    if isinstance(params, RuleParamsV2):
        return decide_v2(package, params)
    if isinstance(params, RuleParams):
        return decide(package, params)
    msg = f"unsupported params type: {type(params).__name__}"
    raise TypeError(msg)


def params_from_dict(data: Mapping[str, Any]) -> RuleParams | RuleParamsV2:
    """Parse params by the ``strategy`` key (absent -> ``rules-v1`` for backward compat)."""
    strategy = data.get(STRATEGY_KEY, STRATEGY_VERSION)
    if strategy == STRATEGY_VERSION_V2:
        return RuleParamsV2.from_dict(data)
    if strategy == STRATEGY_VERSION:
        return RuleParams.from_dict({k: v for k, v in data.items() if k != STRATEGY_KEY})
    msg = f"unknown strategy: {strategy!r}"
    raise ValueError(msg)


def strategy_version_of(params: RuleParams | RuleParamsV2) -> str:
    """Strategy logic version for the params type."""
    return STRATEGY_VERSION_V2 if isinstance(params, RuleParamsV2) else STRATEGY_VERSION


def max_hold_bars_of(params: RuleParams | RuleParamsV2) -> int | None:
    """Time stop in trading bars (rules-v2), or ``None`` (rules-v1 has no time stop)."""
    return params.max_hold_bars if isinstance(params, RuleParamsV2) else None


__all__ = [
    "ALLOWED_REGIME_N",
    "BUY_RATIONALE",
    "BUY_RATIONALE_V2",
    "NO_TRADE_PREFIX",
    "STRATEGY_ID",
    "STRATEGY_ID_V2",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "STRATEGY_VERSION_V2",
    "NoTradeReason",
    "NoTradeReasonV2",
    "RuleParams",
    "RuleParamsV2",
    "decide",
    "decide_any",
    "decide_v2",
    "max_hold_bars_of",
    "params_from_dict",
    "strategy_version_of",
]
