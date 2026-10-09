"""Validation stage tests — fail closed on stale / gap / non-final data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef
from aegis.validation import validate_candles


def _instrument() -> InstrumentRef:
    return InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")


def _candle(
    open_time: datetime,
    close: str = "100",
    *,
    is_final: bool = True,
    received_at: datetime | None = None,
) -> Candle:
    return Candle(
        instrument=_instrument(),
        interval=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
        open=Decimal(close),
        high=Decimal(close) + Decimal("1"),
        low=Decimal(close) - Decimal("1"),
        close=Decimal(close),
        volume=Decimal("100"),
        is_final=is_final,
        source="fixture",
        received_at=received_at or (open_time + timedelta(seconds=1)),
    )


def test_finalized_fresh_series_ok() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = [_candle(start + timedelta(minutes=i), str(100 + i)) for i in range(5)]
    now = candles[-1].received_at + timedelta(milliseconds=100)
    result = validate_candles(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
        stale_after_ms=5000,
    )
    assert result.ok is True
    assert result.market_integrity_ok is True
    assert len(result.candles) == 5
    assert result.block_reasons == ()


def test_excludes_non_final_and_blocks_empty() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    open_only = [_candle(start, is_final=False)]
    now = start + timedelta(seconds=2)
    result = validate_candles(
        open_only,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
    )
    assert result.ok is False
    assert "no_finalized_candles" in result.block_reasons


def test_stale_blocks() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = [_candle(start + timedelta(minutes=i)) for i in range(3)]
    now = candles[-1].received_at + timedelta(seconds=30)
    result = validate_candles(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
        stale_after_ms=5000,
    )
    assert result.ok is False
    assert "stale_market_data" in result.block_reasons
    assert result.market_data_age_ms is not None
    assert result.market_data_age_ms > 5000


def test_gap_blocks_integrity() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = [
        _candle(start),
        _candle(start + timedelta(minutes=1)),
        _candle(start + timedelta(minutes=5)),  # gap
    ]
    now = candles[-1].received_at + timedelta(milliseconds=50)
    result = validate_candles(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
        stale_after_ms=60_000,
    )
    assert result.ok is False
    assert result.market_integrity_ok is False
    assert "candle_gap" in result.block_reasons
