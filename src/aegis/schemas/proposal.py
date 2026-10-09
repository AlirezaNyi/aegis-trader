"""Trade proposal contract (internal). Actions are not 1:1 with exchange sides."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from aegis.schemas.common import ApiModel, Timeframe
from aegis.schemas.evidence import AnalystEvidence, JevResult
from aegis.schemas.market import InstrumentRef


class ProposalAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    NO_TRADE = "NO_TRADE"


class TradeDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class TradeProposal(ApiModel):
    proposal_id: UUID
    correlation_id: str
    instrument: InstrumentRef
    direction: TradeDirection | None = None
    timeframe: Timeframe
    strategy_id: str
    strategy_version: str
    action: ProposalAction
    entry_conditions: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    sizing: dict[str, Any] = Field(default_factory=dict)
    leverage: Decimal | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    analyst_results: list[AnalystEvidence] = Field(default_factory=list)
    jev_result: JevResult
    uncertainty: dict[str, Any] = Field(default_factory=dict)
    invalidation: dict[str, Any] = Field(default_factory=dict)
    supervisor_model_meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
