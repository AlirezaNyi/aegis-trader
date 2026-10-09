"""Market-data gateway implementing MarketDataPort using REST (+ optional WS buffer)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from aegis.interfaces.clock import Clock, SystemClock
from aegis.interfaces.market_data import MarketDataPort
from aegis.market_data.metrics import MarketDataMetrics
from aegis.market_data.quality import CandleStreamTracker, DataQualityIssueKind
from aegis.market_data.rest_client import ToobitRestMarketDataClient
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef, NormalizedMarketEvent
from aegis.schemas.metadata import SymbolMetadata


class MarketDataGateway(MarketDataPort):
    def __init__(
        self,
        rest: ToobitRestMarketDataClient,
        *,
        metrics: MarketDataMetrics | None = None,
        tracker: CandleStreamTracker | None = None,
        clock: Clock | None = None,
        event_buffer: list[NormalizedMarketEvent] | None = None,
    ) -> None:
        self._rest = rest
        self.metrics = metrics or MarketDataMetrics()
        self.tracker = tracker or CandleStreamTracker()
        self._clock = clock or SystemClock()
        self._events = event_buffer if event_buffer is not None else []
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
        candles = self._rest.get_klines(
            instrument,
            interval,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )
        accepted: list[Candle] = []
        for candle in candles:
            self.metrics.record_event(
                received_at=candle.received_at,
                exchange_event_at=candle.open_time,
            )
            issues = self.tracker.observe(candle)
            for issue in issues:
                if issue.kind == DataQualityIssueKind.GAP:
                    self.metrics.record_gap()
                elif issue.kind == DataQualityIssueKind.DUPLICATE:
                    self.metrics.record_duplicate()
                elif issue.kind == DataQualityIssueKind.OUT_OF_ORDER:
                    self.metrics.record_out_of_order()
            if not any(i.kind == DataQualityIssueKind.DUPLICATE for i in issues):
                accepted.append(candle)
            self._events.append(
                NormalizedMarketEvent(
                    market_type=instrument.market_type,
                    symbol=instrument.symbol,
                    event_time=candle.open_time,
                    received_at=candle.received_at,
                    channel=f"kline_{interval.value}",
                    payload={
                        "open_time": candle.open_time.isoformat(),
                        "is_final": candle.is_final,
                    },
                )
            )
        return accepted

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
