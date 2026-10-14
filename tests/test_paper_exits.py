"""Simulated paper protective exits (stop-loss / take-profit) and R-multiples."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.paper import (
    ExitReason,
    PaperBroker,
    PaperFillError,
    PaperLedger,
    PaperTradingBlocked,
    apply_exits,
    instrument_key_for,
)
from aegis.schemas.common import LedgerKind, MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef
from aegis.schemas.orders import OrderIntent
from aegis.schemas.risk import RiskDecision, RiskDecisionType

START = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)
INST = InstrumentRef(market_type=MarketType.SPOT, symbol="BTCUSDT")
KEY = instrument_key_for(INST)


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base: dict[str, object] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
        "paper_fee_bps": Decimal("10"),
        "paper_slippage_bps": Decimal("0"),
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _bar(i: int, o: str, h: str, low: str, c: str, *, final: bool = True) -> Candle:
    open_time = START + STEP * i
    return Candle(
        instrument=INST,
        interval=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + STEP - timedelta(milliseconds=1),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal("10"),
        is_final=final,
        source="fixture",
        received_at=open_time + STEP,
    )


def _entry_clock() -> datetime:
    """Replay-style fill clock: entry bar (index 0) close + 1ms == bar 1 open."""
    return _bar(0, "100", "100", "100", "100").close_time + timedelta(milliseconds=1)


def _approve(
    side: str = "BUY",
    *,
    quantity: str = "2",
    price: str = "100",
    params: dict[str, Any] | None = None,
) -> tuple[RiskDecision, OrderIntent]:
    pid = uuid4()
    now = _entry_clock()
    decision = RiskDecision(
        decision=RiskDecisionType.APPROVE,
        policy_version="test",
        rules_evaluated=["TEST"],
        validated_order_params=params if params is not None else {"action": side},
        decided_at=now,
        expires_at=now + timedelta(hours=1),
        correlation_id="corr-exit",
        proposal_id=pid,
    )
    intent = OrderIntent(
        intent_id=uuid4(),
        proposal_id=pid,
        risk_decision_correlation_id="corr-exit",
        client_order_id=f"paper-{uuid4().hex[:16]}",
        ledger_kind=LedgerKind.PAPER,
        instrument=INST,
        side=side,
        order_type="LIMIT",
        quantity=Decimal(quantity),
        price=Decimal(price),
        created_at=now,
    )
    return decision, intent


def _open_long(
    broker: PaperBroker,
    *,
    stop: str | None = "95",
    target: str | None = "110",
    quantity: str = "2",
    price: str = "100",
    now: datetime | None = None,
) -> None:
    decision, intent = _approve(
        quantity=quantity,
        price=price,
        params={"action": "BUY", "stop_loss": stop, "take_profit": target},
    )
    broker.submit_from_risk_decision(decision, intent, now=now or _entry_clock())


def _broker(**kwargs: object) -> PaperBroker:
    slippage = kwargs.pop("slippage_bps", Decimal("0"))
    return PaperBroker(
        _settings(**kwargs),
        ledger=PaperLedger(),
        slippage_bps=Decimal(str(slippage)),
        initial_quote_balance=Decimal("1000"),
    )


def test_buy_records_protective_levels_from_decision() -> None:
    broker = _broker()
    _open_long(broker)
    prot = broker.ledger.protections[KEY]
    assert prot.stop_loss == Decimal("95")
    assert prot.take_profit == Decimal("110")
    assert prot.entry_price == Decimal("100")
    assert prot.quantity == Decimal("2")
    assert prot.entry_fees == Decimal("0.2")  # 200 * 10bps
    assert prot.opened_at == _entry_clock()
    assert prot.initial_risk() == Decimal("10")


def test_protections_survive_snapshot_round_trip() -> None:
    broker = _broker()
    _open_long(broker)
    apply_exits(broker, INST, [_bar(1, "101", "102", "99", "101")], now=_entry_clock() + STEP)
    restored = PaperLedger.from_snapshot(broker.ledger.to_snapshot())
    assert restored.protections[KEY] == broker.ledger.protections[KEY]
    assert restored.protections[KEY].last_checked_open_time == START + STEP


def test_malformed_stop_in_decision_fails_closed_without_fill() -> None:
    broker = _broker()
    decision, intent = _approve(params={"action": "BUY", "stop_loss": "abc"})
    with pytest.raises(PaperFillError):
        broker.submit_from_risk_decision(decision, intent, now=_entry_clock())
    assert broker.ledger.fills == []
    assert broker.ledger.get_balance() == Decimal("1000")


def test_no_exit_on_entry_bar() -> None:
    broker = _broker()
    _open_long(broker)
    entry_bar = _bar(0, "100", "120", "80", "100")  # touches both levels pre-entry
    assert apply_exits(broker, INST, [entry_bar], now=_entry_clock() + STEP) == []
    assert broker.ledger.positions[KEY].quantity == Decimal("2")


def test_stop_fills_at_stop_on_next_bar() -> None:
    broker = _broker()
    _open_long(broker)
    exits = apply_exits(
        broker,
        INST,
        [_bar(0, "100", "100", "100", "100"), _bar(1, "99", "100", "94", "96")],
        now=_entry_clock() + STEP,
    )
    assert len(exits) == 1
    ex = exits[0]
    assert ex.exit_reason == ExitReason.STOP_LOSS
    assert ex.gap is False
    assert ex.fill.price == Decimal("95")
    assert ex.candle_open_time == START + STEP
    assert ex.order.side == "SELL"
    assert ex.order.ledger_kind == LedgerKind.PAPER
    assert ex.order.client_order_id.startswith("paper-exit-stop_loss-")
    assert broker.ledger.positions[KEY].quantity == Decimal("0")
    assert KEY not in broker.ledger.protections


def test_gap_below_stop_fills_at_open() -> None:
    broker = _broker()
    _open_long(broker)
    exits = apply_exits(broker, INST, [_bar(1, "90", "92", "88", "91")], now=_entry_clock() + STEP)
    assert exits[0].exit_reason == ExitReason.STOP_LOSS
    assert exits[0].gap is True
    assert exits[0].trigger_price == Decimal("90")
    assert exits[0].fill.price == Decimal("90")


def test_take_profit_fills_at_target_without_slippage() -> None:
    broker = _broker(slippage_bps="50")
    _open_long(broker)
    exits = apply_exits(
        broker, INST, [_bar(1, "105", "111", "104", "110")], now=_entry_clock() + STEP
    )
    ex = exits[0]
    assert ex.exit_reason == ExitReason.TAKE_PROFIT
    assert ex.fill.price == Decimal("110")
    # pnl = (110-100)*2 - entry fee 0.2 - exit fee 0.22
    assert ex.realized_pnl == Decimal("19.58")
    assert ex.r_multiple == Decimal("1.958")


def test_both_levels_in_one_bar_takes_stop_first() -> None:
    broker = _broker()
    _open_long(broker)
    exits = apply_exits(
        broker, INST, [_bar(1, "100", "115", "90", "112")], now=_entry_clock() + STEP
    )
    assert exits[0].exit_reason == ExitReason.STOP_LOSS
    assert exits[0].fill.price == Decimal("95")


def test_stop_fee_slippage_and_r_multiple_math() -> None:
    broker = _broker(slippage_bps="50")
    _open_long(broker)
    exits = apply_exits(broker, INST, [_bar(1, "99", "99", "94", "95")], now=_entry_clock() + STEP)
    ex = exits[0]
    # stop 95 with 50bps slippage against the seller: 95 * (1 - 0.005)
    assert ex.fill.price == Decimal("94.525")
    assert ex.fill.fee == Decimal("0.18905")  # 2 * 94.525 * 10bps
    # (94.525 - 100) * 2 - 0.2 - 0.18905
    assert ex.realized_pnl == Decimal("-11.33905")
    assert ex.trade.initial_risk == Decimal("10")
    assert ex.r_multiple == Decimal("-1.133905")
    assert broker.ledger.get_balance() == Decimal("1000") + ex.realized_pnl
    assert broker.ledger.closed_trades[-1] == ex.trade


def test_exits_are_idempotent_and_never_rescan_checked_bars() -> None:
    broker = _broker()
    _open_long(broker)
    calm = _bar(1, "100", "101", "99", "100")
    assert apply_exits(broker, INST, [calm], now=_entry_clock() + STEP) == []
    # A revised copy of an already-checked bar is not re-evaluated.
    revised = _bar(1, "100", "101", "50", "100")
    later = _bar(2, "100", "101", "99", "100")
    assert apply_exits(broker, INST, [revised, later], now=_entry_clock() + 2 * STEP) == []
    hit = _bar(3, "99", "99", "94", "95")
    first = apply_exits(broker, INST, [later, hit], now=_entry_clock() + 3 * STEP)
    second = apply_exits(broker, INST, [later, hit], now=_entry_clock() + 3 * STEP)
    assert len(first) == 1 and second == []
    assert sum(1 for o in broker.ledger.orders.values() if o.side == "SELL") == 1


def test_future_and_non_final_bars_are_ignored() -> None:
    broker = _broker()
    _open_long(broker)
    future = _bar(1, "99", "99", "80", "85")
    # now is before bar 1 closes => look-ahead refused.
    assert apply_exits(broker, INST, [future], now=_entry_clock() + timedelta(seconds=30)) == []
    partial = _bar(1, "99", "99", "80", "85", final=False)
    assert apply_exits(broker, INST, [partial], now=_entry_clock() + STEP) == []
    assert broker.ledger.positions[KEY].quantity == Decimal("2")


def test_other_instrument_candles_are_ignored() -> None:
    broker = _broker()
    _open_long(broker)
    other = _bar(1, "10", "10", "1", "5").model_copy(
        update={"instrument": InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")}
    )
    assert apply_exits(broker, INST, [other], now=_entry_clock() + STEP) == []


def test_position_without_levels_never_exits() -> None:
    broker = _broker()
    _open_long(broker, stop=None, target=None)
    crash = _bar(1, "50", "50", "1", "2")
    assert apply_exits(broker, INST, [crash], now=_entry_clock() + STEP) == []
    assert broker.ledger.positions[KEY].quantity == Decimal("2")


def test_live_mode_refuses_exits() -> None:
    broker = _broker()
    _open_long(broker)
    broker._settings = Settings.model_construct(
        trading_mode=TradingMode.LIVE, live_armed=True, kill_switch=False
    )
    with pytest.raises(PaperTradingBlocked):
        apply_exits(broker, INST, [_bar(1, "90", "90", "80", "85")], now=_entry_clock() + STEP)
    assert broker.ledger.positions[KEY].quantity == Decimal("2")


def test_kill_switch_still_allows_reduce_only_exit() -> None:
    broker = _broker()
    _open_long(broker)
    broker._settings = _settings(kill_switch=True)
    exits = apply_exits(broker, INST, [_bar(1, "99", "99", "94", "95")], now=_entry_clock() + STEP)
    assert exits and exits[0].exit_reason == ExitReason.STOP_LOSS


def test_signal_sell_logs_closed_trade_and_clears_levels() -> None:
    broker = _broker()
    _open_long(broker)
    decision, intent = _approve("SELL", price="104")
    broker.submit_from_risk_decision(decision, intent, now=_entry_clock() + STEP)
    trade = broker.ledger.closed_trades[-1]
    assert trade.reason == ExitReason.SIGNAL
    # (104-100)*2 - 0.2 entry fee - 0.208 exit fee
    assert trade.realized_pnl == Decimal("7.592")
    assert trade.r_multiple == Decimal("0.7592")
    assert KEY not in broker.ledger.protections


def test_partial_signal_sell_scales_protection() -> None:
    broker = _broker()
    _open_long(broker)
    decision, intent = _approve("SELL", quantity="1", price="100")
    broker.submit_from_risk_decision(decision, intent, now=_entry_clock() + STEP)
    prot = broker.ledger.protections[KEY]
    assert prot.quantity == Decimal("1")
    assert prot.entry_fees == Decimal("0.1")
    assert broker.ledger.closed_trades[-1].entry_fees == Decimal("0.1")


def test_add_on_buy_replaces_levels_and_keeps_stop_when_missing() -> None:
    broker = _broker()
    _open_long(broker, quantity="1", price="100")
    later = _entry_clock() + 5 * STEP
    decision, intent = _approve(
        quantity="1",
        price="102",
        params={"action": "BUY", "stop_loss": None, "take_profit": "120"},
    )
    broker.submit_from_risk_decision(decision, intent, now=later)
    prot = broker.ledger.protections[KEY]
    assert prot.stop_loss == Decimal("95")  # never silently removed
    assert prot.take_profit == Decimal("120")
    assert prot.entry_price == Decimal("101")
    assert prot.quantity == Decimal("2")
    assert prot.opened_at == later
    assert prot.last_checked_open_time is None
