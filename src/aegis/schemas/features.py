"""Feature snapshot contracts (features-v1)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from aegis.schemas.common import ApiModel, MarketType, Timeframe
from aegis.schemas.market import InstrumentRef


class FeatureValueStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    UNAVAILABLE = "unavailable"


class FeatureValue(ApiModel):
    value: Decimal | None = None
    status: FeatureValueStatus
    reason: str | None = None


class FeatureSnapshot(ApiModel):
    schema_version: str = "features-v1"
    snapshot_id: UUID
    instrument: InstrumentRef
    market_type: MarketType
    timeframe: Timeframe
    as_of: datetime
    include_intrabar: bool = False
    candle_count: int
    gap_detected: bool = False
    features: dict[str, FeatureValue] = Field(default_factory=dict)
    created_at: datetime
