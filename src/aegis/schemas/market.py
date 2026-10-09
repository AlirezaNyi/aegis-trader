"""Market data contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from aegis.schemas.common import ApiModel, MarketType, Timeframe


class InstrumentRef(ApiModel):
    exchange: str = "toobit"
    market_type: MarketType
    symbol: str


class NormalizedMarketEvent(ApiModel):
    exchange: str = "toobit"
    market_type: MarketType
    symbol: str
    event_time: datetime
    received_at: datetime
    channel: str
    payload: dict[str, Any] = Field(default_factory=dict)
    sequence: int | None = None


class Candle(ApiModel):
    instrument_id: UUID | None = None
    instrument: InstrumentRef
    interval: Timeframe
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None = None
    trade_count: int | None = None
    is_final: bool
    source: str
    received_at: datetime
