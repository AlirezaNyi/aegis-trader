"""Market data port — Toobit adapters live under aegis.market_data."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef, NormalizedMarketEvent


class MarketDataPort(Protocol):
    """Async candle source. `stream_events` only reads an in-memory buffer (sync)."""

    async def get_candles(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Sequence[Candle]:
        """Return candles from a verified source."""

    def stream_events(self) -> Sequence[NormalizedMarketEvent]:
        """Return buffered market events."""


class NullMarketDataPort:
    """Stub that refuses to invent exchange data when no adapter is configured."""

    async def get_candles(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Sequence[Candle]:
        _ = start_time, end_time
        raise NotImplementedError(
            "No market-data adapter configured. "
            f"Requested {instrument.symbol} {interval.value} limit={limit}."
        )

    def stream_events(self) -> Sequence[NormalizedMarketEvent]:
        raise NotImplementedError("No market-data adapter configured.")
