"""Instrument metadata from verified exchangeInfo."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import Field

from aegis.schemas.common import ApiModel, MarketType


class SymbolMetadata(ApiModel):
    exchange: str = "toobit"
    market_type: MarketType
    symbol: str
    status: str
    base_asset: str
    quote_asset: str
    base_asset_precision: str | None = None
    quote_precision: str | None = None
    tick_size: Decimal | None = None
    step_size: Decimal | None = None
    min_qty: Decimal | None = None
    min_notional: Decimal | None = None
    contract_multiplier: Decimal | None = None
    inverse: bool | None = None
    filters: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
