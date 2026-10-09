"""Feature Engine (features-v1) tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from aegis.features import compute_features, select_candles
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.features import FeatureValueStatus
from aegis.schemas.market import Candle, InstrumentRef


def _instrument() -> InstrumentRef:
    return InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")


def _candle(
    open_time: datetime,
    close: str,
    *,
    volume: str = "100",
    is_final: bool = True,
    interval: Timeframe = Timeframe.M1,
) -> Candle:
    return Candle(
        instrument=_instrument(),
        interval=interval,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
        open=Decimal(close),
        high=Decimal(close) + Decimal("1"),
        low=Decimal(close) - Decimal("1"),
        close=Decimal(close),
        volume=Decimal(volume),
        is_final=is_final,
        source="fixture",
        received_at=open_time + timedelta(seconds=1),
    )


def _series(n: int, start: datetime, *, base: int = 100) -> list[Candle]:
    return [
        _candle(start + timedelta(minutes=i), str(base + i), volume=str(100 + i))
        for i in range(n)
    ]


def test_feature_snapshot_deterministic() -> None:
    start = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    candles = _series(25, start)
    as_of = candles[-1].close_time
    sid = uuid4()
    a = compute_features(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        as_of=as_of,
        snapshot_id=sid,
        created_at=as_of,
    )
    b = compute_features(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        as_of=as_of,
        snapshot_id=sid,
        created_at=as_of,
    )
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    assert a.features["sma_20"].status == FeatureValueStatus.OK
    assert a.features["ret_1"].status == FeatureValueStatus.OK
    assert a.features["rsi_14"].status == FeatureValueStatus.OK
    assert a.features["atr_14"].status == FeatureValueStatus.OK
    assert a.features["realized_vol_20"].status == FeatureValueStatus.OK
    assert a.features["volume_ratio"].status == FeatureValueStatus.OK


def test_excludes_open_and_future_candles() -> None:
    start = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    candles = _series(20, start)
    open_bar = _candle(
        start + timedelta(minutes=20),
        "999",
        is_final=False,
    )
    future = _candle(start + timedelta(minutes=30), "50")
    as_of = candles[-1].close_time
    selected = select_candles([*candles, open_bar, future], as_of=as_of)
    assert open_bar not in selected
    assert future not in selected
    assert len(selected) == 20

    snap = compute_features(
        [*candles, open_bar, future],
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        as_of=as_of,
    )
    # Future/open must not change SMA vs finalized-only series
    snap_base = compute_features(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        as_of=as_of,
        snapshot_id=snap.snapshot_id,
        created_at=snap.created_at,
    )
    assert snap.features["sma_20"].value == snap_base.features["sma_20"].value


def test_as_of_lookahead_guard() -> None:
    start = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    candles = _series(25, start)
    early = candles[9].close_time
    snap = compute_features(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        as_of=early,
    )
    assert snap.candle_count == 10
    assert snap.features["sma_20"].status == FeatureValueStatus.INSUFFICIENT_SAMPLE
    assert snap.features["sma_20"].value is None


def test_gap_marks_features_unavailable() -> None:
    start = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    first = _series(10, start)
    # Skip 5 minutes → gap
    second_start = start + timedelta(minutes=15)
    second = _series(15, second_start, base=200)
    candles = [*first, *second]
    as_of = candles[-1].close_time
    snap = compute_features(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        as_of=as_of,
    )
    assert snap.gap_detected is True
    # Warm-up window of last 20 includes the gap between first and second block
    assert snap.features["sma_20"].status == FeatureValueStatus.UNAVAILABLE
    assert snap.features["sma_20"].value is None


def test_insufficient_sample_not_fabricated_zero() -> None:
    start = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    candles = _series(5, start)
    snap = compute_features(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        as_of=candles[-1].close_time,
    )
    for name in ("sma_20", "ema_20", "rsi_14", "atr_14", "realized_vol_20", "volume_sma_20"):
        assert snap.features[name].status == FeatureValueStatus.INSUFFICIENT_SAMPLE
        assert snap.features[name].value is None


def test_include_intrabar_flag_recorded() -> None:
    start = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    candles = _series(20, start)
    open_bar = _candle(start + timedelta(minutes=20), "130", is_final=False)
    as_of = open_bar.close_time
    snap = compute_features(
        [*candles, open_bar],
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        as_of=as_of,
        include_intrabar=True,
    )
    assert snap.include_intrabar is True
    assert snap.candle_count == 21
