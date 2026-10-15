"""Paper time stop (rules-v2 ``max_hold_bars``): count, priority, costs, persistence."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from aegis.paper import (
    ExitReason,
    PaperBroker,
    PaperFillError,
    PaperLedger,
    apply_exits,
)
from aegis.paper.protection import ProtectiveLevels, parse_max_hold_bars
from test_paper_exits import (  # type: ignore[import-not-found]
    INST,
    KEY,
    STEP,
    _approve,
    _bar,
    _broker,
    _entry_clock,
)


def _params(hold: Any, *, stop: str | None = "95", target: str | None = "110") -> dict[str, Any]:
    return {
        "action": "BUY",
        "stop_loss": stop,
        "take_profit": target,
        "entry_conditions": {"order_type": "LIMIT", "max_hold_bars": hold},
    }


def _open(broker: PaperBroker, hold: Any, **kw: Any) -> None:
    decision, intent = _approve(params=_params(hold, **kw))
    broker.submit_from_risk_decision(decision, intent, now=_entry_clock())


def _quiet(i: int, close: str = "101") -> Any:
    # Never touches stop 95 or target 110.
    return _bar(i, "100", "102", "99", close)


def _now(i: int) -> datetime:
    return _bar(i, "1", "1", "1", "1").close_time + timedelta(milliseconds=1)


def test_buy_records_max_hold_bars_from_entry_conditions() -> None:
    broker = _broker()
    _open(broker, 3)
    prot = broker.ledger.protections[KEY]
    assert prot.max_hold_bars == 3
    assert prot.bars_held == 0


def test_no_entry_conditions_means_no_time_stop() -> None:
    broker = _broker()
    decision, intent = _approve(params={"action": "BUY", "stop_loss": "95", "take_profit": "110"})
    broker.submit_from_risk_decision(decision, intent, now=_entry_clock())
    assert broker.ledger.protections[KEY].max_hold_bars is None
    bars = [_quiet(i) for i in range(1, 30)]
    assert apply_exits(broker, INST, bars, now=_now(29)) == []


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "x", [3]])
def test_malformed_max_hold_bars_fails_closed_without_fill(bad: Any) -> None:
    broker = _broker()
    decision, intent = _approve(params=_params(bad))
    with pytest.raises(PaperFillError):
        broker.submit_from_risk_decision(decision, intent, now=_entry_clock())
    assert broker.ledger.fills == []
    assert broker.ledger.get_balance() == Decimal("1000")


def test_non_mapping_entry_conditions_fails_closed() -> None:
    broker = _broker()
    params = {"action": "BUY", "stop_loss": "95", "entry_conditions": "nope"}
    decision, intent = _approve(params=params)
    with pytest.raises(PaperFillError):
        broker.submit_from_risk_decision(decision, intent, now=_entry_clock())
    assert broker.ledger.fills == []


def test_parse_max_hold_bars_accepts_int_and_int_string() -> None:
    assert parse_max_hold_bars(None) is None
    assert parse_max_hold_bars(12) == 12
    assert parse_max_hold_bars("36") == 36


def test_time_stop_closes_at_nth_bar_close_with_slippage_and_fee() -> None:
    broker = _broker(slippage_bps=Decimal("10"))
    _open(broker, 3)
    entry = broker.ledger.protections[KEY]
    entry_price = entry.entry_price
    entry_fees = entry.entry_fees
    # Entry bar 0 is never counted; bars 1 and 2 count but do not reach the stop.
    assert apply_exits(broker, INST, [_bar(0, "100", "120", "80", "100"), _quiet(1)],
                       now=_now(1)) == []
    assert apply_exits(broker, INST, [_quiet(2)], now=_now(2)) == []
    assert broker.ledger.protections[KEY].bars_held == 2
    exits = apply_exits(broker, INST, [_quiet(3, close="104")], now=_now(3))
    assert len(exits) == 1
    ex = exits[0]
    assert ex.exit_reason == ExitReason.TIME_STOP
    assert ex.trigger_price == Decimal("104")
    assert ex.gap is False
    assert ex.candle_open_time == _bar(3, "1", "1", "1", "1").open_time
    # 104 - 10 bps slippage against the seller.
    assert ex.fill.price == Decimal("103.896")
    assert ex.order.order_type == "MARKET"
    assert ex.order.client_order_id.startswith("paper-exit-time_stop-")
    qty = Decimal("2")
    exit_fee = qty * Decimal("103.896") * Decimal("10") / Decimal("10000")
    assert ex.fill.fee == exit_fee
    assert ex.realized_pnl == (Decimal("103.896") - entry_price) * qty - entry_fees - exit_fee
    assert ex.trade.reason == ExitReason.TIME_STOP
    assert ex.r_multiple is not None
    assert KEY not in broker.ledger.protections
    assert broker.ledger.positions[KEY].quantity == 0


def test_time_stop_in_one_scan_over_many_bars() -> None:
    broker = _broker()
    _open(broker, 2)
    exits = apply_exits(broker, INST, [_quiet(i, close=f"10{i}") for i in range(1, 6)],
                        now=_now(5))
    assert len(exits) == 1
    assert exits[0].exit_reason == ExitReason.TIME_STOP
    assert exits[0].trigger_price == Decimal("102")  # bar 2's close


def test_stop_loss_keeps_priority_on_time_stop_bar() -> None:
    broker = _broker()
    _open(broker, 2)
    exits = apply_exits(broker, INST, [_quiet(1), _bar(2, "99", "100", "94", "96")], now=_now(2))
    assert exits[0].exit_reason == ExitReason.STOP_LOSS
    assert exits[0].trigger_price == Decimal("95")


def test_take_profit_keeps_priority_on_time_stop_bar() -> None:
    broker = _broker()
    _open(broker, 1)
    exits = apply_exits(broker, INST, [_bar(1, "100", "111", "99", "100")], now=_now(1))
    assert exits[0].exit_reason == ExitReason.TAKE_PROFIT


def test_time_stop_without_price_levels_still_exits() -> None:
    broker = _broker()
    _open(broker, 1, stop=None, target=None)
    exits = apply_exits(broker, INST, [_quiet(1)], now=_now(1))
    assert exits and exits[0].exit_reason == ExitReason.TIME_STOP
    assert exits[0].r_multiple is None  # no stop -> R undefined


def test_bars_count_once_and_ignore_future_or_non_final() -> None:
    broker = _broker()
    _open(broker, 3)
    apply_exits(broker, INST, [_quiet(1)], now=_now(1))
    apply_exits(broker, INST, [_quiet(1)], now=_now(1))  # same bar again: no recount
    future = _quiet(5)
    partial = _bar(2, "100", "102", "99", "101", final=False)
    apply_exits(broker, INST, [future, partial], now=_now(1))
    assert broker.ledger.protections[KEY].bars_held == 1


def test_add_on_buy_restarts_count_and_keeps_prior_hold_when_missing() -> None:
    broker = _broker()
    _open(broker, 3)
    apply_exits(broker, INST, [_quiet(1), _quiet(2)], now=_now(2))
    assert broker.ledger.protections[KEY].bars_held == 2
    decision, intent = _approve(params={"action": "BUY", "stop_loss": "95"})
    broker.submit_from_risk_decision(decision, intent, now=_now(2))
    prot = broker.ledger.protections[KEY]
    assert prot.max_hold_bars == 3
    assert prot.bars_held == 0


def test_time_stop_state_survives_snapshot_and_json() -> None:
    broker = _broker()
    _open(broker, 4)
    apply_exits(broker, INST, [_quiet(1), _quiet(2)], now=_now(2))
    restored = PaperLedger.from_snapshot(broker.ledger.to_snapshot())
    assert restored.protections[KEY] == broker.ledger.protections[KEY]
    prot = broker.ledger.protections[KEY]
    again = ProtectiveLevels.from_json(prot.to_json())
    assert again.max_hold_bars == 4 and again.bars_held == 2
    # Older payloads without the new keys still load (no time stop).
    raw = prot.to_json()
    raw.pop("max_hold_bars")
    raw.pop("bars_held")
    legacy = ProtectiveLevels.from_json(raw)
    assert legacy.max_hold_bars is None and legacy.bars_held == 0
    bad = prot.to_json()
    bad["bars_held"] = -1
    with pytest.raises(ValueError):
        ProtectiveLevels.from_json(bad)


def test_restored_broker_continues_count() -> None:
    broker = _broker()
    _open(broker, 3)
    apply_exits(broker, INST, [_quiet(1), _quiet(2)], now=_now(2))
    ledger = PaperLedger.from_snapshot(broker.ledger.to_snapshot())
    restored = PaperBroker(broker._settings, ledger=ledger)
    exits = apply_exits(restored, INST, [_quiet(3)], now=_now(3))
    assert exits and exits[0].exit_reason == ExitReason.TIME_STOP
    assert exits[0].candle_open_time == _bar(3, "1", "1", "1", "1").open_time
    _ = STEP
