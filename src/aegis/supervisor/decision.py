"""Compact LLM decision contract and deterministic TradeProposal assembly.

The LLM only fills market-judgement fields (action, direction, prices, rationale).
Identity, sizing, order type, time-in-force, and expiry are filled by code from
the evidence package and owner-approved policy defaults. The model never sets
notional, leverage, or execution parameters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis.risk.policy import SIZING_METHOD_PCT_CAPITAL, SIZING_METHOD_RISK_PCT_CAPITAL
from aegis.schemas.evidence import EvidencePackage, JevResult
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal

_MAX_TEXT = 2000
_CENT = Decimal("0.01")
_BPS = Decimal("10000")


class SupervisorDecision(BaseModel):
    """What the LLM is allowed to decide. Unknown keys are dropped, never trusted."""

    model_config = ConfigDict(extra="ignore")

    action: ProposalAction
    direction: TradeDirection | None = None
    entry_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    rationale: str = ""
    invalidation: str = ""
    confidence_note: str = ""

    @field_validator("rationale", "invalidation", "confidence_note", mode="before")
    @classmethod
    def _bounded_text(cls, value: Any) -> str:
        """Free text never drives a decision: coerce and truncate, don't reject."""
        if value is None:
            return ""
        text = value if isinstance(value, str) else str(value)
        return text[:_MAX_TEXT]


SUPERVISOR_DECISION_KEYS: tuple[str, ...] = tuple(SupervisorDecision.model_fields)


@dataclass(frozen=True)
class ProposalDefaults:
    """Code-owned proposal fields derived from the approved risk policy.

    Empty mappings mean "not configured"; the Risk Engine then rejects the
    proposal (fail closed) instead of the model guessing values.
    """

    entry_conditions: Mapping[str, Any] = field(default_factory=dict)
    sizing: Mapping[str, Any] = field(default_factory=dict)
    ttl: timedelta = timedelta(minutes=5)
    # Approved max stop distance in percent, shown to the LLM as a hint only;
    # the Risk Engine still enforces it.
    max_stop_distance_pct: Decimal | None = None
    # Policy 1.1 sizing inputs, copied by the caller from APPROVED policy values
    # (USDT). None ⇒ not configured: pct/risk sizing then leaves ``notional``
    # unset so the Risk Engine rejects (fail closed).
    position_cap_usdt: Decimal | None = None
    risk_budget_usdt: Decimal | None = None
    # Modeled round-trip cost in bps (2*fee + spread + 2*slippage). Used for
    # risk-based sizing and as a prompt hint; Risk recomputes from RiskContext.
    cost_bps_round_trip: Decimal | None = None
    # Approved minimum net reward:risk; prompt hint only (Risk enforces it).
    min_reward_risk: Decimal | None = None


def _positive(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not dec.is_finite() or dec <= 0:
        return None
    return dec


def _round_down_cents(value: Decimal) -> Decimal | None:
    try:
        rounded = value.quantize(_CENT, rounding=ROUND_DOWN)
    except InvalidOperation:
        return None
    return rounded if rounded > 0 else None


def pct_capital_notional(position_cap_usdt: Decimal | None) -> Decimal | None:
    """Notional for ``pct_capital`` sizing: the position cap, rounded down to cents."""
    cap = _positive(position_cap_usdt)
    return None if cap is None else _round_down_cents(cap)


def risk_sized_notional(
    *,
    entry_price: Decimal | None,
    stop_loss: Decimal | None,
    position_cap_usdt: Decimal | None,
    risk_budget_usdt: Decimal | None,
    cost_bps_round_trip: Decimal | None,
) -> Decimal | None:
    """Notional for ``risk_pct_capital`` sizing, rounded DOWN to cents.

    ``min(cap, budget * entry / (|entry - SL| + Cu))`` with
    ``Cu = entry * cost_bps_round_trip / 10000``. Any missing / non-positive
    input (negative cost) returns None so no notional is set (Risk rejects).
    """
    entry = _positive(entry_price)
    stop = _positive(stop_loss)
    cap = _positive(position_cap_usdt)
    budget = _positive(risk_budget_usdt)
    if entry is None or stop is None or cap is None or budget is None:
        return None
    if cost_bps_round_trip is None:
        return None
    try:
        cost_bps = Decimal(str(cost_bps_round_trip))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not cost_bps.is_finite() or cost_bps < 0:
        return None
    cost_per_unit = entry * cost_bps / _BPS
    risk_per_unit = abs(entry - stop) + cost_per_unit
    if risk_per_unit <= 0:
        return None
    by_risk = budget * entry / risk_per_unit
    return _round_down_cents(min(cap, by_risk))


def _trade_sizing(decision: SupervisorDecision, defaults: ProposalDefaults) -> dict[str, Any]:
    """Code-owned sizing. ``fixed_notional`` and unknown methods copy defaults as-is."""
    sizing = dict(defaults.sizing)
    method = sizing.get("method")
    notional: Decimal | None
    if method == SIZING_METHOD_PCT_CAPITAL:
        notional = pct_capital_notional(defaults.position_cap_usdt)
    elif method == SIZING_METHOD_RISK_PCT_CAPITAL:
        notional = risk_sized_notional(
            entry_price=decision.entry_price,
            stop_loss=decision.stop_loss,
            position_cap_usdt=defaults.position_cap_usdt,
            risk_budget_usdt=defaults.risk_budget_usdt,
            cost_bps_round_trip=defaults.cost_bps_round_trip,
        )
    else:
        return sizing
    # Never let a stale default notional survive when the method's math cannot run.
    sizing.pop("notional", None)
    if notional is not None:
        sizing["notional"] = str(notional)
    return sizing


def is_compact_decision(payload: Any) -> bool:
    """Full TradeProposal payloads carry ``proposal_id``; compact ones do not."""
    return isinstance(payload, dict) and "proposal_id" not in payload


def proposal_from_decision(
    decision: SupervisorDecision,
    package: EvidencePackage,
    jev_result: JevResult,
    *,
    defaults: ProposalDefaults,
    strategy_id: str,
    strategy_version: str,
    now: datetime,
) -> TradeProposal:
    """Assemble a TradeProposal; the Risk Engine still decides APPROVE/REJECT."""
    is_trade = decision.action in {ProposalAction.BUY, ProposalAction.SELL}
    entry_conditions: dict[str, Any] = {}
    sizing: dict[str, Any] = {}
    if is_trade:
        entry_conditions = dict(defaults.entry_conditions)
        if decision.entry_price is not None:
            entry_conditions["entry_price"] = str(decision.entry_price)
        sizing = _trade_sizing(decision, defaults)

    uncertainty: dict[str, Any] = {"model_confidence_uncalibrated": True}
    if decision.confidence_note:
        uncertainty["model_confidence_note"] = decision.confidence_note
    if decision.rationale:
        uncertainty["rationale"] = decision.rationale

    direction = decision.direction
    if not is_trade:
        direction = TradeDirection.FLAT

    return TradeProposal(
        proposal_id=uuid4(),
        correlation_id=package.correlation_id,
        instrument=package.instrument,
        direction=direction,
        timeframe=package.timeframe,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        action=decision.action,
        entry_conditions=entry_conditions,
        expires_at=now + defaults.ttl,
        stop_loss=decision.stop_loss if is_trade else None,
        take_profit=decision.take_profit if is_trade else None,
        sizing=sizing,
        leverage=None,
        evidence_refs=list(package.feature_refs),
        analyst_results=list(package.analyst_evidence),
        jev_result=jev_result,
        uncertainty=uncertainty,
        invalidation={"model_note": decision.invalidation} if decision.invalidation else {},
        supervisor_model_meta={"output_contract": "SupervisorDecision"},
        created_at=package.created_at,
    )
