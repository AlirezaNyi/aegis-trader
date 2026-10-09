"""Hypothetical BUY/SELL percent marks — SL-first same-bar rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aegis.pipeline.signal_marks import (
    SignalMarkStatus,
    aggregate_signal_returns,
    direction_from_action,
    is_trade_signal_action,
    mark_signal_on_candles,
)
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef
from aegis.schemas.proposal import ProposalAction, TradeDirection


def _instrument() -> InstrumentRef:
    return InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT")


def _candle(
    open_time: datetime,
    *,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        instrument=_instrument(),
        interval=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        is_final=True,
        source="fixture",
        received_at=open_time + timedelta(seconds=1),
    )


def test_direction_and_action_filters() -> None:
    assert direction_from_action(ProposalAction.BUY) == TradeDirection.LONG
    assert direction_from_action(ProposalAction.SELL) == TradeDirection.SHORT
    assert direction_from_action(ProposalAction.HOLD) is None
    assert direction_from_action(ProposalAction.NO_TRADE) is None
    assert is_trade_signal_action("BUY")
    assert not is_trade_signal_action("HOLD")
    assert not is_trade_signal_action(None)


def test_long_stop_wins_same_bar() -> None:
    t0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    mark = mark_signal_on_candles(
        entry_price=Decimal("100"),
        direction=TradeDirection.LONG,
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        candles_after_entry=[
            _candle(t0, high="111", low="94", close="105"),
        ],
    )
    assert mark.status == SignalMarkStatus.STOPPED
    assert mark.exit_price == Decimal("95")
    assert mark.return_pct == Decimal("-5.0000")


def test_long_target_hit() -> None:
    t0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    mark = mark_signal_on_candles(
        entry_price=Decimal("100"),
        direction=TradeDirection.LONG,
        stop_loss=Decimal("90"),
        take_profit=Decimal("110"),
        candles_after_entry=[
            _candle(t0, high="112", low="99", close="111"),
        ],
    )
    assert mark.status == SignalMarkStatus.TARGET
    assert mark.return_pct == Decimal("10.0000")


def test_short_open_mtm() -> None:
    t0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    mark = mark_signal_on_candles(
        entry_price=Decimal("100"),
        direction=TradeDirection.SHORT,
        stop_loss=None,
        take_profit=None,
        candles_after_entry=[
            _candle(t0, high="101", low="96", close="97"),
        ],
    )
    assert mark.status == SignalMarkStatus.OPEN
    assert mark.return_pct == Decimal("3.0000")


def test_short_stop_and_target() -> None:
    t0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    stopped = mark_signal_on_candles(
        entry_price=Decimal("100"),
        direction=TradeDirection.SHORT,
        stop_loss=Decimal("105"),
        take_profit=Decimal("90"),
        candles_after_entry=[
            _candle(t0, high="106", low="89", close="100"),
        ],
    )
    assert stopped.status == SignalMarkStatus.STOPPED
    assert stopped.return_pct == Decimal("-5.0000")

    t1 = datetime(2026, 10, 9, 12, 1, tzinfo=UTC)
    target = mark_signal_on_candles(
        entry_price=Decimal("100"),
        direction=TradeDirection.SHORT,
        stop_loss=Decimal("110"),
        take_profit=Decimal("90"),
        candles_after_entry=[
            _candle(t1, high="101", low="89", close="91"),
        ],
    )
    assert target.status == SignalMarkStatus.TARGET
    assert target.return_pct == Decimal("10.0000")


def test_aggregate_excludes_hold_via_caller_list() -> None:
    agg = aggregate_signal_returns(
        [Decimal("2"), Decimal("-1"), Decimal("0.5")],
        [SignalMarkStatus.TARGET, SignalMarkStatus.STOPPED, SignalMarkStatus.OPEN],
    )
    assert agg["count"] == 3
    assert agg["wins"] == 1
    assert agg["losses"] == 1
    assert agg["open"] == 1
    assert agg["sum_return_pct"] == Decimal("1.5000")
