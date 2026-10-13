"""MarketContext block on EvidencePackage (descriptive price levels)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from aegis.evidence import build_evidence_package, build_market_context
from aegis.features import compute_features
from aegis.jev.redact import redact_evidence_package
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.features import FeatureSnapshot, FeatureValue, FeatureValueStatus
from aegis.schemas.market import Candle, InstrumentRef

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _instrument() -> InstrumentRef:
    return InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")


def _candle(
    i: int,
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
    is_final: bool = True,
) -> Candle:
    open_time = START + timedelta(minutes=i)
    c = Decimal(close)
    return Candle(
        instrument=_instrument(),
        interval=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
        open=c,
        high=Decimal(high) if high is not None else c + Decimal("1"),
        low=Decimal(low) if low is not None else c - Decimal("1"),
        close=c,
        volume=Decimal("100"),
        is_final=is_final,
        source="fixture",
        received_at=open_time + timedelta(seconds=1),
    )


def _snapshot(as_of: datetime, atr: Decimal | None) -> FeatureSnapshot:
    features: dict[str, FeatureValue] = {}
    if atr is not None:
        features["atr_14"] = FeatureValue(value=atr, status=FeatureValueStatus.OK)
    else:
        features["atr_14"] = FeatureValue(
            value=None, status=FeatureValueStatus.INSUFFICIENT_SAMPLE, reason="warm-up"
        )
    return FeatureSnapshot(
        snapshot_id=uuid4(),
        instrument=_instrument(),
        market_type=MarketType.SPOT,
        timeframe=Timeframe.M1,
        as_of=as_of,
        candle_count=0,
        features=features,
        created_at=as_of,
    )


def test_market_context_values() -> None:
    candles = [_candle(i, str(100 + i)) for i in range(25)]
    snap = _snapshot(candles[-1].close_time, Decimal("2"))
    ctx = build_market_context(candles, snap, lookback=20)
    assert ctx is not None
    assert ctx.last_close == Decimal("124")
    # window = candles 5..24 -> closes 105..124, high close+1, low close-1
    assert ctx.recent_high == Decimal("125")
    assert ctx.recent_low == Decimal("104")
    assert ctx.lookback_candles == 20
    assert ctx.atr_14 == Decimal("2")
    assert ctx.atr_pct_of_close == (Decimal("2") / Decimal("124") * 100).quantize(
        Decimal("0.0001")
    )
    assert ctx.atr_pct_of_close == Decimal("1.6129")
    assert ctx.spread_bps is None
    assert ctx.spread_status == "unavailable"
    assert ctx.as_of == candles[-1].close_time


def test_market_context_short_history_and_spread() -> None:
    candles = [_candle(i, "50") for i in range(3)]
    snap = _snapshot(candles[-1].close_time, None)
    ctx = build_market_context(candles, snap, spread_bps=Decimal("1.5"))
    assert ctx is not None
    assert ctx.lookback_candles == 3
    assert ctx.atr_14 is None
    assert ctx.atr_pct_of_close is None
    assert ctx.spread_bps == Decimal("1.5")
    assert ctx.spread_status == "ok"


def test_market_context_ignores_non_final_and_future() -> None:
    candles = [_candle(i, str(100 + i)) for i in range(10)]
    as_of = candles[-1].close_time
    intrabar = _candle(10, "999", high="2000", low="1", is_final=False)
    future = _candle(11, "500", high="600", low="1")
    snap = _snapshot(as_of, None)
    ctx = build_market_context([*candles, intrabar, future], snap)
    assert ctx is not None
    assert ctx.last_close == Decimal("109")
    assert ctx.recent_high == Decimal("110")
    assert ctx.recent_low == Decimal("99")
    assert ctx.lookback_candles == 10
    assert ctx.as_of == as_of


def test_market_context_none_without_eligible_candles() -> None:
    snap = _snapshot(START, None)
    assert build_market_context([], snap) is None
    only_intrabar = [_candle(0, "100", is_final=False)]
    assert build_market_context(only_intrabar, _snapshot(START + timedelta(hours=1), None)) is None


def test_package_with_candles_and_redaction_keeps_context() -> None:
    candles = [_candle(i, str(100 + i)) for i in range(25)]
    as_of = candles[-1].close_time
    snap = compute_features(
        candles, instrument=_instrument(), timeframe=Timeframe.M1, as_of=as_of
    )
    pkg = build_evidence_package(
        snap, [], correlation_id="c1", candles=candles, spread_bps=Decimal("2.5")
    )
    assert pkg.schema_version == "1"
    assert pkg.market_context is not None
    assert pkg.market_context.atr_14 == snap.features["atr_14"].value
    dumped = redact_evidence_package(pkg)
    mc = dumped["market_context"]
    assert isinstance(mc, dict)
    assert "[REDACTED]" not in mc.values()
    assert set(mc) == {
        "last_close",
        "recent_high",
        "recent_low",
        "lookback_candles",
        "atr_14",
        "atr_pct_of_close",
        "spread_bps",
        "spread_status",
        "as_of",
    }
    assert Decimal(mc["last_close"]) == Decimal("124")
    assert mc["spread_status"] == "ok"


def test_package_without_candles_has_no_context() -> None:
    candles = [_candle(i, str(100 + i)) for i in range(25)]
    snap = _snapshot(candles[-1].close_time, Decimal("2"))
    pkg = build_evidence_package(snap, [], correlation_id="c2")
    assert pkg.market_context is None
    assert redact_evidence_package(pkg)["market_context"] is None
