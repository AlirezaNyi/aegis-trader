"""Candle quality: duplicates, gaps, out-of-order, stale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aegis.market_data.mock import build_candle_series, gap_series
from aegis.market_data.quality import CandleStreamTracker, DataQualityIssueKind
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef


def _candle(open_time: datetime, *, symbol: str = "ETHUSDT") -> Candle:
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol=symbol)
    return Candle(
        instrument=instrument,
        interval=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + timedelta(seconds=59, milliseconds=999),
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("0.5"),
        close=Decimal("1.5"),
        volume=Decimal("1"),
        is_final=True,
        source="fixture",
        received_at=open_time,
    )


def test_duplicate_detection() -> None:
    tracker = CandleStreamTracker()
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    c = _candle(t0)
    assert tracker.observe(c) == []
    issues = tracker.observe(c)
    assert any(i.kind == DataQualityIssueKind.DUPLICATE for i in issues)


def test_out_of_order_detection() -> None:
    tracker = CandleStreamTracker()
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=1)
    assert tracker.observe(_candle(t1)) == []
    issues = tracker.observe(_candle(t0))
    assert any(i.kind == DataQualityIssueKind.OUT_OF_ORDER for i in issues)


def test_gap_detection() -> None:
    tracker = CandleStreamTracker()
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    series = gap_series(instrument, Timeframe.M1)
    assert tracker.observe(series[0]) == []
    issues = tracker.observe(series[1])
    assert any(i.kind == DataQualityIssueKind.GAP for i in issues)


def test_stale_detection() -> None:
    tracker = CandleStreamTracker(stale_after_ms=1_000)
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    tracker.observe(_candle(t0))
    stale = tracker.check_stale(
        now=t0 + timedelta(seconds=5),
        symbol="ETHUSDT",
        interval=Timeframe.M1,
    )
    assert stale is not None
    assert stale.kind == DataQualityIssueKind.STALE


def test_fixture_series_incomplete_last() -> None:
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    candles = build_candle_series(
        instrument,
        Timeframe.M5,
        start=datetime(2024, 1, 1, tzinfo=UTC),
        count=3,
    )
    assert candles[0].is_final is True
    assert candles[-1].is_final is False
