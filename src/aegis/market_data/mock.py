"""Deterministic fixture/mock market-data port for tests (no network)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aegis.interfaces.market_data import MarketDataPort
from aegis.market_data.timeutil import interval_to_milliseconds
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef, NormalizedMarketEvent


def build_candle_series(
    instrument: InstrumentRef,
    interval: Timeframe,
    *,
    start: datetime,
    count: int,
    received_at: datetime | None = None,
    finalize_all_but_last: bool = True,
) -> list[Candle]:
    step_ms = interval_to_milliseconds(interval.value)
    recv = received_at or datetime.now(UTC)
    candles: list[Candle] = []
    for i in range(count):
        open_ms = int(start.timestamp() * 1000) + i * step_ms
        open_time = datetime.fromtimestamp(open_ms / 1000.0, tz=UTC)
        close_time = datetime.fromtimestamp((open_ms + step_ms - 1) / 1000.0, tz=UTC)
        price = Decimal("100") + Decimal(i)
        is_final = True
        if finalize_all_but_last and i == count - 1:
            is_final = False
        candles.append(
            Candle(
                instrument=instrument,
                interval=interval,
                open_time=open_time,
                close_time=close_time,
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price + Decimal("0.5"),
                volume=Decimal("10"),
                quote_volume=Decimal("1000"),
                trade_count=5,
                is_final=is_final,
                source="fixture",
                received_at=recv,
            )
        )
    return candles


class FixtureMarketDataPort(MarketDataPort):
    def __init__(self, candles: Sequence[Candle] | None = None) -> None:
        self._candles = list(candles or [])
        self._events: list[NormalizedMarketEvent] = []

    def seed_default(
        self,
        symbol: str = "ETHUSDT",
        market_type: MarketType = MarketType.SPOT,
    ) -> None:
        instrument = InstrumentRef(market_type=market_type, symbol=symbol)
        start = datetime(2024, 1, 1, tzinfo=UTC)
        self._candles = build_candle_series(instrument, Timeframe.M1, start=start, count=5)

    def get_candles(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
    ) -> Sequence[Candle]:
        matched = [
            c
            for c in self._candles
            if c.instrument.symbol == instrument.symbol
            and c.instrument.market_type == instrument.market_type
            and c.interval == interval
        ]
        return matched[-limit:]

    def stream_events(self) -> Sequence[NormalizedMarketEvent]:
        return list(self._events)

    def inject_event(self, event: NormalizedMarketEvent) -> None:
        self._events.append(event)


def gap_series(instrument: InstrumentRef, interval: Timeframe) -> list[Candle]:
    """Two candles with a one-bar gap between them."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    first = build_candle_series(
        instrument,
        interval,
        start=start,
        count=1,
        finalize_all_but_last=False,
    )[0].model_copy(update={"is_final": True})
    step = timedelta(milliseconds=interval_to_milliseconds(interval.value) * 2)
    second_start = start + step
    second = build_candle_series(
        instrument, interval, start=second_start, count=1, finalize_all_but_last=False
    )[0].model_copy(update={"is_final": True})
    return [first, second]
