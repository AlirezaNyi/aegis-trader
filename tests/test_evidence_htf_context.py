"""HigherTimeframeContext (daily regime levels) on EvidencePackage — no look-ahead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aegis.evidence import build_evidence_package, build_htf_context
from aegis.features import compute_features
from aegis.jev.redact import redact_evidence_package
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef

DAY0 = datetime(2026, 1, 1, tzinfo=UTC)


def _instrument(symbol: str = "ETHUSDT") -> InstrumentRef:
    return InstrumentRef(market_type=MarketType.SPOT, symbol=symbol)


def _daily(
    i: int,
    close: str,
    *,
    is_final: bool = True,
    symbol: str = "ETHUSDT",
    interval: Timeframe = Timeframe.D1,
) -> Candle:
    open_time = DAY0 + timedelta(days=i)
    c = Decimal(close)
    return Candle(
        instrument=_instrument(symbol),
        interval=interval,
        open_time=open_time,
        close_time=open_time + timedelta(days=1) - timedelta(milliseconds=1),
        open=c,
        high=c + 1,
        low=c - 1,
        close=c,
        volume=Decimal("1000"),
        is_final=is_final,
        source="fixture",
        received_at=open_time + timedelta(days=1),
    )


def _days(n: int, start_close: int = 100) -> list[Candle]:
    return [_daily(i, str(start_close + i)) for i in range(n)]


def test_sma_values_and_insufficient_lookbacks_are_none() -> None:
    candles = _days(60)  # closes 100..159
    as_of = candles[-1].close_time
    ctx = build_htf_context(candles, as_of=as_of)
    assert ctx is not None
    assert ctx.timeframe == Timeframe.D1
    assert ctx.as_of == as_of
    assert ctx.last_close == Decimal("159")
    assert ctx.bars_available == 60
    assert set(ctx.sma) == {"20", "50", "100", "200"}
    # last 20 closes 140..159 -> mean 149.5 ; last 50 closes 110..159 -> 134.5
    assert ctx.sma["20"] == Decimal("149.5")
    assert ctx.sma["50"] == Decimal("134.5")
    assert ctx.sma["100"] is None
    assert ctx.sma["200"] is None


def test_exact_lookback_count_is_available() -> None:
    ctx = build_htf_context(_days(20), as_of=_days(20)[-1].close_time)
    assert ctx is not None
    assert ctx.sma["20"] == Decimal("109.5")
    assert ctx.sma["50"] is None


def test_forming_daily_bar_is_excluded() -> None:
    history = _days(30)
    # Intraday as_of is mid-day 30: the day-30 bar is still forming.
    as_of = DAY0 + timedelta(days=30, hours=13, minutes=59, seconds=59, milliseconds=999)
    forming_final_flag = _daily(30, "9999")  # marked final but close_time > as_of
    forming_intrabar = _daily(30, "8888", is_final=False)
    ctx = build_htf_context([*history, forming_final_flag, forming_intrabar], as_of=as_of)
    assert ctx is not None
    assert ctx.as_of == history[-1].close_time
    assert ctx.last_close == Decimal("129")
    assert ctx.bars_available == 30
    assert ctx == build_htf_context(history, as_of=as_of)


def test_future_bars_do_not_change_context() -> None:
    history = _days(40)
    as_of = history[-1].close_time
    future = [_daily(40 + i, "1") for i in range(30)]
    assert build_htf_context(history + future, as_of=as_of) == build_htf_context(
        history, as_of=as_of
    )


def test_bar_completed_exactly_at_as_of_is_included() -> None:
    history = _days(21)
    ctx = build_htf_context(history, as_of=history[-1].close_time)
    assert ctx is not None
    assert ctx.bars_available == 21
    ctx_before = build_htf_context(
        history, as_of=history[-1].close_time - timedelta(milliseconds=1)
    )
    assert ctx_before is not None
    assert ctx_before.bars_available == 20


def test_none_when_no_completed_bar() -> None:
    assert build_htf_context([], as_of=DAY0) is None
    assert build_htf_context([_daily(0, "100")], as_of=DAY0 + timedelta(hours=12)) is None
    assert (
        build_htf_context([_daily(0, "100", is_final=False)], as_of=DAY0 + timedelta(days=2))
        is None
    )


def test_instrument_filter_and_validation() -> None:
    eth = _days(25)
    btc = [_daily(i, "50000", symbol="BTCUSDT") for i in range(25)]
    as_of = eth[-1].close_time
    ctx = build_htf_context([*eth, *btc], as_of=as_of, instrument=_instrument())
    assert ctx == build_htf_context(eth, as_of=as_of)
    with pytest.raises(ValueError, match="interval"):
        build_htf_context([*eth, _daily(0, "1", interval=Timeframe.H1)], as_of=as_of)
    with pytest.raises(ValueError, match="duplicate"):
        build_htf_context([*eth, eth[3]], as_of=as_of)


def _m15_candles(n: int, start: datetime) -> list[Candle]:
    out: list[Candle] = []
    for i in range(n):
        open_time = start + timedelta(minutes=15 * i)
        c = Decimal(100) + Decimal(i % 5)
        out.append(
            Candle(
                instrument=_instrument(),
                interval=Timeframe.M15,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=15) - timedelta(milliseconds=1),
                open=c,
                high=c + 1,
                low=c - 1,
                close=c,
                volume=Decimal("10"),
                is_final=True,
                source="fixture",
                received_at=open_time + timedelta(minutes=15),
            )
        )
    return out


def test_package_htf_context_uses_snapshot_as_of_and_survives_redaction() -> None:
    daily = _days(60)
    # Trading bars run through mid-day of day 59 (that daily bar is still forming).
    intraday = _m15_candles(40, DAY0 + timedelta(days=59))
    as_of = intraday[-1].close_time
    snap = compute_features(
        intraday, instrument=_instrument(), timeframe=Timeframe.M15, as_of=as_of
    )
    pkg = build_evidence_package(
        snap, [], correlation_id="htf", candles=intraday, htf_candles=daily
    )
    assert pkg.htf_context is not None
    assert pkg.htf_context.as_of == daily[58].close_time
    assert pkg.htf_context.last_close == Decimal("158")
    assert pkg.htf_context.bars_available == 59
    dumped = redact_evidence_package(pkg)
    htf = dumped["htf_context"]
    assert isinstance(htf, dict)
    assert set(htf) == {"timeframe", "as_of", "last_close", "sma", "bars_available"}
    assert "[REDACTED]" not in htf.values()
    assert "[REDACTED]" not in htf["sma"].values()
    assert htf["timeframe"] == "1d"


def test_package_without_htf_candles_has_no_htf_context() -> None:
    intraday = _m15_candles(30, DAY0)
    snap = compute_features(
        intraday,
        instrument=_instrument(),
        timeframe=Timeframe.M15,
        as_of=intraday[-1].close_time,
    )
    pkg = build_evidence_package(snap, [], correlation_id="no-htf", candles=intraday)
    assert pkg.htf_context is None
    assert redact_evidence_package(pkg)["htf_context"] is None
