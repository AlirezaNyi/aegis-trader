"""Market data port — no network implementation in Phase 1."""

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
        """Return candles from a verified source (Phase 2+)."""

    def stream_events(self) -> Sequence[NormalizedMarketEvent]:
        """Return buffered market events (Phase 2+)."""


class NullMarketDataPort:
    """Stub that refuses fabricated exchange data."""

    def get_candles(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
    ) -> Sequence[Candle]:
        raise NotImplementedError(
            "Market data client is not implemented in Phase 1. "
            f"Requested {instrument.symbol} {interval.value} limit={limit}."
        )

    def stream_events(self) -> Sequence[NormalizedMarketEvent]:
        raise NotImplementedError("Market data streaming is not implemented in Phase 1.")
