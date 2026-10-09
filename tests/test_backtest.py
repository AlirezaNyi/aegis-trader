"""Phase 6 backtest harness tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from aegis.backtest import (
    BacktestRunner,
    CandleReplay,
    CostModel,
    LookAheadError,
    always_flat,
    buy_hold_one_bar_factory,
)
from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.market_data.mock import build_candle_series
from aegis.risk.handoff import may_submit_to_order_manager
from aegis.schemas.backtest import SplitWindows
from aegis.schemas.common import LedgerKind, MarketType, Timeframe
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import OrderIntent
from aegis.schemas.risk import RiskDecision, RiskDecisionType


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
        "paper_fee_bps": Decimal("10"),
        "paper_slippage_bps": Decimal("5"),
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _candles(count: int = 12):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    return build_candle_series(
        instrument,
        Timeframe.M5,
        start=start,
        count=count,
        finalize_all_but_last=False,
    )


def _splits_for(candles) -> SplitWindows:
    """Partition into train / validation / OOS by close_time thirds."""
    times = [c.close_time for c in candles]
    n = len(times)
    t1 = times[n // 3]
    t2 = times[(2 * n) // 3]
    return SplitWindows(
        train_start=times[0],
        train_end=t1,
        validation_start=t1 + timedelta(microseconds=1),
        validation_end=t2,
        oos_start=t2 + timedelta(microseconds=1),
        oos_end=times[-1],
    )


def _train_only_splits(candles) -> SplitWindows:
    """Put all candles in train; empty validation/OOS windows after last bar."""
    times = [c.close_time for c in candles]
    after = times[-1] + timedelta(days=1)
    return SplitWindows(
        train_start=times[0],
        train_end=times[-1],
        validation_start=after,
        validation_end=after + timedelta(hours=1),
        oos_start=after + timedelta(hours=2),
        oos_end=after + timedelta(hours=3),
    )


def test_reproducible_metrics_same_seed() -> None:
    candles = _candles(10)
    splits = _splits_for(candles)
    settings = _settings()

    def _run(seed: int):
        runner = BacktestRunner(
            settings,
            strategy_id="flat",
            strategy_version="1",
            dataset_version="fixture-v1",
            splits=splits,
            seed=seed,
            cost_model=CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5")),
        )
        return runner.run(
            candles,
            always_flat,
            skip_risk=True,
            now=datetime(2026, 10, 9, tzinfo=UTC),
        )

    a = _run(42)
    b = _run(42)
    assert a.train_metrics.model_dump() == b.train_metrics.model_dump()
    assert a.validation_metrics.model_dump() == b.validation_metrics.model_dump()
    assert a.oos_metrics.model_dump() == b.oos_metrics.model_dump()
    assert a.seed == 42


def test_lookahead_cannot_see_future_bars() -> None:
    candles = _candles(5)
    replay = CandleReplay(candles, seed=1)
    replay.set_as_of(candles[2].close_time)
    visible = replay.visible_candles()
    assert len(visible) == 3
    assert all(c.close_time <= candles[2].close_time for c in visible)
    with pytest.raises(LookAheadError):
        replay.require_no_future(candles[3])


def test_oos_metrics_separate_from_train() -> None:
    candles = _candles(12)
    splits = _splits_for(candles)
    # Trade only inside train window so train metrics differ from empty OOS trade counts
    train_end = splits.train_end
    state = {"done": False}

    def train_only_buy(replay: CandleReplay, candle) -> OrderIntent | None:
        _ = replay.visible_candles()
        if state["done"] or candle.close_time > train_end:
            return None
        state["done"] = True
        return OrderIntent(
            intent_id=uuid4(),
            proposal_id=uuid4(),
            risk_decision_correlation_id="train-buy",
            client_order_id=f"train-buy-{candle.close_time.isoformat()}",
            ledger_kind=LedgerKind.PAPER,
            instrument=candle.instrument,
            side="BUY",
            order_type="MARKET",
            quantity=Decimal("1"),
            price=None,
            created_at=candle.close_time,
        )

    runner = BacktestRunner(
        _settings(),
        strategy_id="train-buy",
        strategy_version="1",
        dataset_version="fixture-v1",
        splits=splits,
        seed=7,
        initial_quote_balance=Decimal("100000"),
    )
    report = runner.run(
        candles,
        train_only_buy,
        skip_risk=True,
        now=datetime(2026, 10, 9, tzinfo=UTC),
    )
    assert report.train_metrics.window == "train"
    assert report.oos_metrics.window == "oos"
    assert report.train_metrics is not report.oos_metrics
    assert report.train_metrics.cost_contribution > 0
    assert report.oos_metrics.cost_contribution == 0
    assert "profitability" not in report.disclaimer.lower() or "not" in report.disclaimer.lower()
    assert report.disclaimer


def test_costs_reduce_net_return_vs_zero_cost() -> None:
    candles = _candles(6)
    splits = _train_only_splits(candles)
    settings = _settings()
    strategy = buy_hold_one_bar_factory(quantity=Decimal("1"))

    costly = BacktestRunner(
        settings,
        strategy_id="bh",
        strategy_version="1",
        dataset_version="fixture-v1",
        splits=splits,
        seed=3,
        cost_model=CostModel(fee_bps=Decimal("50"), slippage_bps=Decimal("0")),
        initial_quote_balance=Decimal("100000"),
    ).run(candles, strategy, skip_risk=True, now=datetime(2026, 10, 9, tzinfo=UTC))

    # Fresh strategy state
    strategy_zero = buy_hold_one_bar_factory(quantity=Decimal("1"))
    zero = BacktestRunner(
        settings,
        strategy_id="bh",
        strategy_version="1",
        dataset_version="fixture-v1",
        splits=splits,
        seed=3,
        cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        initial_quote_balance=Decimal("100000"),
    ).run(candles, strategy_zero, skip_risk=True, now=datetime(2026, 10, 9, tzinfo=UTC))

    assert costly.train_metrics.cost_contribution > zero.train_metrics.cost_contribution
    assert (
        costly.train_metrics.net_return_after_costs
        < zero.train_metrics.net_return_after_costs
    )


def test_reject_risk_gate_never_fills() -> None:
    candles = _candles(4)
    splits = _train_only_splits(candles)

    def reject_all(intent: OrderIntent, _candle) -> RiskDecision:
        now = datetime(2026, 10, 9, tzinfo=UTC)
        return RiskDecision(
            decision=RiskDecisionType.REJECT,
            policy_version="test",
            rules_evaluated=["REJECT"],
            rejection_reasons=[{"rule_id": "REJECT", "detail": "no"}],
            decided_at=now,
            expires_at=now + timedelta(hours=1),
            correlation_id=intent.risk_decision_correlation_id,
            proposal_id=intent.proposal_id,
        )

    report = BacktestRunner(
        _settings(),
        strategy_id="bh",
        strategy_version="1",
        dataset_version="fixture-v1",
        splits=splits,
        seed=1,
    ).run(
        candles,
        buy_hold_one_bar_factory(),
        risk_gate=reject_all,
        skip_risk=False,
        now=datetime(2026, 10, 9, tzinfo=UTC),
    )
    assert report.train_metrics.trade_count == 0
    assert report.train_metrics.cost_contribution == 0


def test_live_mode_refused_and_no_exchange_calls() -> None:
    with pytest.raises(ValueError):
        # Settings validator: live without armed fails
        Settings(trading_mode=TradingMode.LIVE, live_armed=False, require_database=False)

    # Even constructing runner with a hypothetically live settings object is blocked
    # if somehow constructed — use model_construct to bypass validator for gate test.
    live_settings = Settings.model_construct(
        trading_mode=TradingMode.LIVE,
        live_armed=True,
        kill_switch=False,
        require_database=False,
        paper_fee_bps=Decimal("10"),
        paper_slippage_bps=Decimal("5"),
    )
    candles = _candles(3)
    with pytest.raises(RuntimeError, match="live"):
        BacktestRunner(
            live_settings,
            strategy_id="x",
            strategy_version="1",
            dataset_version="v1",
            splits=_train_only_splits(candles),
            seed=0,
        )

    # Paper path does not touch exchange live submit; flag means path exists (Phase 7)
    import aegis.exchange as exchange

    assert exchange.LIVE_SUBMIT_SUPPORTED is True



def test_may_submit_false_for_reject() -> None:
    decision = RiskDecision(
        decision=RiskDecisionType.REJECT,
        policy_version="t",
        decided_at=datetime(2026, 10, 9, tzinfo=UTC),
        expires_at=datetime(2026, 10, 10, tzinfo=UTC),
        correlation_id="c",
        proposal_id=uuid4(),
    )
    assert may_submit_to_order_manager(decision) is False
