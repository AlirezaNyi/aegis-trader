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
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis.schemas.evidence import EvidencePackage, JevResult
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal

_MAX_TEXT = 2000


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
        sizing = dict(defaults.sizing)

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
