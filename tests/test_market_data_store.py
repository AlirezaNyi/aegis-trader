"""Candle store (fail-open) and backfill window math. No live DB required."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from aegis.db.models import MarketCandleRow
from aegis.market_data.backfill import (
    backfill_instrument,
    backfill_windows,
    floor_to_interval,
)
from aegis.market_data.candle_store import (
    CandleStore,
    build_upsert_statement,
    final_row_values,
    row_to_candle,
)
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef

ADA = InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT")
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _candle(open_time: datetime, *, final: bool = True, close: str = "1.5") -> Candle:
    return Candle(
        instrument=ADA,
        interval=Timeframe.M5,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=5) - timedelta(milliseconds=1),
        open=Decimal("1.0"),
        high=Decimal("2.0"),
        low=Decimal("0.5"),
        close=Decimal(close),
        volume=Decimal("10"),
        quote_volume=None,
        trade_count=None,
        is_final=final,
        source="rest",
        received_at=T0,
    )


# --- store -----------------------------------------------------------------


def test_store_disabled_without_url() -> None:
    store = CandleStore(None)
    assert store.enabled is False
    assert store.upsert_final([_candle(T0)]) == 0
    assert store.load_range(ADA, Timeframe.M5, T0, T0 + timedelta(hours=1)) == []
    assert store.last_error is None


def test_store_does_not_retain_url() -> None:
    url = "postgresql+psycopg://u:secretpw@127.0.0.1:1/x"
    store = CandleStore(url)
    assert "secretpw" not in repr(vars(store))


def test_store_fail_open_when_db_unreachable() -> None:
    store = CandleStore("postgresql+psycopg://u:p@127.0.0.1:1/x")
    assert store.upsert_final([_candle(T0)]) == 0
    assert store.last_error is not None and store.last_error.startswith("upsert:")
    assert store.load_range(ADA, Timeframe.M5, T0, T0 + timedelta(hours=1)) == []
    assert store.last_error.startswith("load:")


def test_final_rows_filter_and_dedupe() -> None:
    rows = final_row_values(
        [
            _candle(T0, close="1.1"),
            _candle(T0, close="9.9"),  # duplicate key: first wins
            _candle(T0 + timedelta(minutes=5), final=False),
            _candle(T0 + timedelta(minutes=10)),
        ]
    )
    assert [r["open_time"] for r in rows] == [T0, T0 + timedelta(minutes=10)]
    assert rows[0]["close"] == Decimal("1.1")
    assert rows[0]["market_type"] == "spot" and rows[0]["interval"] == "5m"


def test_upsert_statement_is_on_conflict_do_nothing() -> None:
    stmt = build_upsert_statement(final_row_values([_candle(T0)]))
    sql = str(stmt.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    assert "ON CONFLICT ON CONSTRAINT uq_market_candles_key DO NOTHING" in sql
    assert "DO UPDATE" not in sql


def test_row_roundtrip_to_final_candle() -> None:
    values = final_row_values([_candle(T0)])[0]
    candle = row_to_candle(MarketCandleRow(**values))
    assert candle.is_final is True
    assert candle.open_time == T0
    assert candle.close == Decimal("1.5")
    assert candle.instrument == ADA


# --- backfill windows ------------------------------------------------------


def test_floor_to_interval() -> None:
    value = datetime(2026, 10, 1, 0, 7, 33, tzinfo=UTC)
    assert floor_to_interval(value, Timeframe.M5) == datetime(2026, 10, 1, 0, 5, tzinfo=UTC)


def test_windows_cover_range_without_overlap() -> None:
    start = T0
    end = T0 + timedelta(minutes=5 * 2500)
    windows = backfill_windows(start, end, Timeframe.M5)
    assert [w.bars for w in windows] == [1000, 1000, 500]
    assert windows[0].start == T0
    assert windows[0].end == T0 + timedelta(minutes=5 * 1000) - timedelta(milliseconds=1)
    for prev, nxt in zip(windows, windows[1:], strict=False):
        assert nxt.start == prev.end + timedelta(milliseconds=1)
    assert windows[-1].end == end - timedelta(milliseconds=1)


def test_windows_exclude_in_progress_bar() -> None:
    end = T0 + timedelta(minutes=12)  # floor -> 10:00, bar at 10 is in progress
    windows = backfill_windows(T0, end, Timeframe.M5)
    assert len(windows) == 1 and windows[0].bars == 2


def test_windows_empty_and_validation() -> None:
    assert backfill_windows(T0, T0, Timeframe.M1) == []
    with pytest.raises(ValueError):
        backfill_windows(T0.replace(tzinfo=None), T0, Timeframe.M1)
    with pytest.raises(ValueError):
        backfill_windows(T0, T0 + timedelta(days=1), Timeframe.M1, max_bars=1001)


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_klines(self, instrument: InstrumentRef, interval: Timeframe, **kw: Any) -> list[Candle]:
        self.calls.append(kw)
        if len(self.calls) == 2:
            return []
        if len(self.calls) == 3:
            raise RuntimeError("boom")
        start: datetime = kw["start_time"]
        return [_candle(start), _candle(start + timedelta(minutes=5), final=False)]


class _FakeStore:
    def __init__(self) -> None:
        self.batches: list[list[Candle]] = []

    def upsert_final(self, candles: list[Candle]) -> int:
        self.batches.append(list(candles))
        return len([c for c in candles if c.is_final])


def test_backfill_instrument_paginates_oldest_first() -> None:
    client = _FakeClient()
    store = _FakeStore()
    sleeps: list[float] = []
    result = backfill_instrument(
        client,  # type: ignore[arg-type]
        store,  # type: ignore[arg-type]
        ADA,
        Timeframe.M5,
        start=T0,
        end=T0 + timedelta(minutes=5 * 3500),
        delay_seconds=0.1,
        sleep=sleeps.append,
    )
    starts = [c["start_time"] for c in client.calls]
    assert starts == sorted(starts) and len(starts) == 4
    assert [c["limit"] for c in client.calls] == [1000, 1000, 1000, 500]
    assert sleeps == [0.1, 0.1, 0.1]
    assert result.windows == 4
    assert result.empty_windows == 1
    assert len(result.errors) == 1 and result.errors[0].endswith("RuntimeError")
    assert result.fetched == 4 and result.final == 2 and result.inserted == 2
    assert all(c.is_final for batch in store.batches for c in batch)
