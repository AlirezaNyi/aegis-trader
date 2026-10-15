"""Evidence package and analyst/Jev result contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
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


class MarketContext(ApiModel):
    """Descriptive price levels from finalized candles (no look-ahead).

    ``atr_14`` is in absolute price units; ``atr_pct_of_close`` is a percent
    (``0.42`` means 0.42%). Descriptive only; not a trade signal or sizing input.
    """

    last_close: Decimal
    recent_high: Decimal
    recent_low: Decimal
    lookback_candles: int
    atr_14: Decimal | None = None
    atr_pct_of_close: Decimal | None = None
    spread_bps: Decimal | None = None
    spread_status: Literal["ok", "unavailable"] = "unavailable"
    as_of: datetime


HTF_SMA_LOOKBACKS: tuple[int, ...] = (20, 50, 100, 200)


class HigherTimeframeContext(ApiModel):
    """Descriptive higher-timeframe (e.g. daily) regime levels (no look-ahead).

    Built only from **completed** higher-timeframe bars whose ``close_time`` is at or
    before the trading package ``as_of``; a bar still forming is excluded. ``as_of``
    is the ``close_time`` of the last completed bar used. ``sma`` maps each fixed
    lookback in :data:`HTF_SMA_LOOKBACKS` (as a string key, e.g. ``"50"``) to the
    simple mean of the last N closes, or ``None`` when fewer than N bars exist.
    Descriptive only; not a trade signal or sizing input.
    """

    timeframe: Timeframe
    as_of: datetime
    last_close: Decimal
    sma: dict[str, Decimal | None] = Field(default_factory=dict)
    bars_available: int


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
    market_context: MarketContext | None = None
    htf_context: HigherTimeframeContext | None = None
