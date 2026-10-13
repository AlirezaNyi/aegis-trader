"""Market-data gateway implementing MarketDataPort using REST (+ optional WS buffer)."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from datetime import datetime

from aegis.interfaces.clock import Clock, SystemClock
from aegis.interfaces.market_data import MarketDataPort
from aegis.market_data.metrics import MarketDataMetrics
from aegis.market_data.quality import CandleStreamTracker
from aegis.market_data.rest_client import ToobitRestMarketDataClient
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef, NormalizedMarketEvent
from aegis.schemas.metadata import SymbolMetadata

# Engineering cap for optional WS-side event buffer — not a risk-policy value.
_EVENT_BUFFER_MAXLEN = 1024


class MarketDataGateway(MarketDataPort):
    def __init__(
        self,
        rest: ToobitRestMarketDataClient,
        *,
        metrics: MarketDataMetrics | None = None,
        tracker: CandleStreamTracker | None = None,
        clock: Clock | None = None,
        event_buffer: list[NormalizedMarketEvent] | deque[NormalizedMarketEvent] | None = None,
    ) -> None:
        self._rest = rest
        self.metrics = metrics or MarketDataMetrics()
        self.tracker = tracker or CandleStreamTracker()
        self._clock = clock or SystemClock()
        if event_buffer is None:
            self._events: deque[NormalizedMarketEvent] = deque(maxlen=_EVENT_BUFFER_MAXLEN)
        elif isinstance(event_buffer, deque):
            self._events = event_buffer
        else:
            self._events = deque(event_buffer, maxlen=_EVENT_BUFFER_MAXLEN)
        self._metadata_cache: list[SymbolMetadata] | None = None

    def refresh_metadata(self) -> list[SymbolMetadata]:
        self._metadata_cache = self._rest.get_exchange_info()
        return list(self._metadata_cache)

    def get_metadata(self) -> Sequence[SymbolMetadata]:
        if self._metadata_cache is None:
            return self.refresh_metadata()
        return list(self._metadata_cache)

    def get_candles(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Sequence[Candle]:
        # REST historical batches are returned as-is. Stream tracker duplicate
        # filtering is for incremental WS paths only — applying it here drops the
        # previous newest bar on re-poll and gaps the soak window.
        candles = list(
            self._rest.get_klines(
                instrument,
                interval,
                limit=limit,
                start_time=start_time,
                end_time=end_time,
            )
        )
        for candle in candles:
            self.metrics.record_event(
                received_at=candle.received_at,
                exchange_event_at=candle.open_time,
            )
        # Freshness uses the newest open_time only — do not observe the whole batch
        # (would spam gap/out-of-order metrics on every historical re-fetch).
        if candles:
            self.tracker.observe(candles[-1])
        return candles

    def stream_events(self) -> Sequence[NormalizedMarketEvent]:
        return list(self._events)

    def freshness(
        self,
        *,
        symbol: str,
        interval: Timeframe,
    ) -> dict[str, object]:
        now = self._clock.now()
        stale = self.tracker.check_stale(now=now, symbol=symbol, interval=interval)
        snap = self.metrics.snapshot(now)
        snap["stale"] = stale is not None
        snap["stale_detail"] = None if stale is None else stale.detail
        return snap
