"""Evidence package and analyst/Jev result contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from aegis.schemas.common import ApiModel, EvidenceStatus, MarketType, Timeframe
from aegis.schemas.market import InstrumentRef


class AnalystType(StrEnum):
    TECHNICAL = "technical"
    QUANTITATIVE = "quantitative"
    NEWS_SENTIMENT = "news_sentiment"
    ANALYTICAL_RISK = "analytical_risk"
    STRATEGY_RESEARCHER = "strategy_researcher"


class AnalystEvidence(ApiModel):
    analyst_type: AnalystType
    status: EvidenceStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_time: datetime
    sources: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None


class JevResult(ApiModel):
    evidence_package_id: UUID | None = None
    model: str | None = None
    status: EvidenceStatus
    answers: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    confidence_notes: str = (
        "Jev confidence is distribution concentration, not calibrated probability of profit."
    )


class EvidencePackage(ApiModel):
    schema_version: str = "1"
    package_id: UUID
    correlation_id: str
    created_at: datetime
    instrument: InstrumentRef
    market_type: MarketType
    timeframe: Timeframe
    as_of: datetime
    feature_refs: list[str] = Field(default_factory=list)
    analyst_evidence: list[AnalystEvidence] = Field(default_factory=list)
