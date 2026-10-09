"""Market data port — Toobit adapters live under aegis.market_data."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef, NormalizedMarketEvent


class MarketDataPort(Protocol):
    def get_candles(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
    ) -> Sequence[Candle]:
        """Return candles from a verified source."""

    def stream_events(self) -> Sequence[NormalizedMarketEvent]:
        """Return buffered market events."""


class NullMarketDataPort:
    """Stub that refuses to invent exchange data when no adapter is configured."""

    def get_candles(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
    ) -> Sequence[Candle]:
        raise NotImplementedError(
            "No market-data adapter configured. "
            f"Requested {instrument.symbol} {interval.value} limit={limit}."
        )

    def stream_events(self) -> Sequence[NormalizedMarketEvent]:
        raise NotImplementedError("No market-data adapter configured.")
