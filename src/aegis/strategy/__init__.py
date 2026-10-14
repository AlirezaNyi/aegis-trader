"""Deterministic research strategies (no I/O, no LLM, no order authority)."""

from __future__ import annotations

from aegis.strategy.rules import (
    BUY_RATIONALE,
    NO_TRADE_PREFIX,
    STRATEGY_ID,
    STRATEGY_VERSION,
    NoTradeReason,
    RuleParams,
    decide,
)

__all__ = [
    "BUY_RATIONALE",
    "NO_TRADE_PREFIX",
    "STRATEGY_ID",
    "STRATEGY_VERSION",
    "NoTradeReason",
    "RuleParams",
    "decide",
]
