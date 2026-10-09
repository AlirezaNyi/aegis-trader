"""Deterministic backtest runner — candle replay through paper fills."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from aegis.backtest.costs import CostModel
from aegis.backtest.metrics import compute_window_metrics
from aegis.backtest.replay import CandleReplay
from aegis.config.settings import Settings, TradingMode
from aegis.paper.broker import PaperBroker
from aegis.paper.ledger import PaperLedger
from aegis.risk.handoff import may_submit_to_order_manager
from aegis.schemas.backtest import BacktestRunReport, SplitWindows, WindowMetrics
from aegis.schemas.common import LedgerKind
from aegis.schemas.market import Candle
from aegis.schemas.orders import OrderIntent
from aegis.schemas.risk import RiskDecision, RiskDecisionType

SOFTWARE_VERSION = "aegis-0.1.0-phase6"

# Strategy sees replay state and current candle; returns optional intent.
StrategyHook = Callable[[CandleReplay, Candle], OrderIntent | None]

# Optional risk gate: return APPROVE decision or None / REJECT to skip fill.
RiskGate = Callable[[OrderIntent, Candle], RiskDecision | None]


@dataclass
class _WindowAccumulator:
    name: str
    initial_equity: Decimal
    equity_curve: list[Decimal] = field(default_factory=list)
    trade_pnls: list[Decimal] = field(default_factory=list)
    costs: list[Decimal] = field(default_factory=list)
    notionals: list[Decimal] = field(default_factory=list)
    exposure_bars: int = 0
    total_bars: int = 0
    by_instrument: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_timeframe: dict[str, dict[str, Any]] = field(default_factory=dict)
    open_entry: Decimal | None = None
    open_qty: Decimal = Decimal("0")


class BacktestRunner:
    """Replay candles with a strategy hook; fills go through PaperBroker (paper only).

    Train / validation / OOS metrics are accumulated separately and never mixed.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        strategy_id: str,
        strategy_version: str,
        dataset_version: str,
        splits: SplitWindows,
        seed: int = 0,
        cost_model: CostModel | None = None,
        initial_quote_balance: Decimal = Decimal("100000"),
        software_version: str = SOFTWARE_VERSION,
    ) -> None:
        if settings.trading_mode == TradingMode.LIVE:
            raise RuntimeError("BacktestRunner refuses trading_mode=live")
        self._settings = settings
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.dataset_version = dataset_version
        self.splits = splits
        self.seed = seed
        self.cost_model = cost_model or CostModel(
            fee_bps=settings.paper_fee_bps,
            slippage_bps=settings.paper_slippage_bps,
        )
        self.initial_quote_balance = Decimal(initial_quote_balance)
        self.software_version = software_version

    def run(
        self,
        candles: Sequence[Candle],
        strategy: StrategyHook,
        *,
        risk_gate: RiskGate | None = None,
        skip_risk: bool = False,
        run_id: UUID | None = None,
        now: datetime | None = None,
    ) -> BacktestRunReport:
        """Execute a deterministic backtest.

        * ``skip_risk=True`` — explicit research-only auto-APPROVE (paper-only).
        * ``risk_gate`` — when set (and not skip_risk), only APPROVE decisions fill.
        * Without ``skip_risk`` and without ``risk_gate``: raise (no silent APPROVE).
        """
        if not skip_risk and risk_gate is None:
            raise ValueError(
                "BacktestRunner requires risk_gate=... or skip_risk=True "
                "(research-only auto-APPROVE must be explicit)"
            )
        created = now if now is not None else datetime.now(tz=UTC)
        replay = CandleReplay(candles, seed=self.seed)

        windows = {
            "train": _WindowAccumulator("train", self.initial_quote_balance),
            "validation": _WindowAccumulator("validation", self.initial_quote_balance),
            "oos": _WindowAccumulator("oos", self.initial_quote_balance),
        }
        # Separate ledgers per window so train/val/OOS metrics never mix
        window_brokers: dict[str, PaperBroker] = {}
        for name in windows:
            window_brokers[name] = PaperBroker(
                self._settings,
                ledger=PaperLedger(),
                fee_bps=self.cost_model.fee_bps,
                slippage_bps=self.cost_model.slippage_bps,
                initial_quote_balance=self.initial_quote_balance,
            )

        for candle in replay.all_candles:
            replay.set_as_of(candle.close_time)
            visible = replay.visible_candles()
            if not visible or visible[-1].close_time != candle.close_time:
                raise RuntimeError("replay invariant broken")

            window_name = self._window_for(candle.close_time)
            if window_name is None:
                continue

            acc = windows[window_name]
            w_broker = window_brokers[window_name]
            acc.total_bars += 1

            intent = strategy(replay, candle)
            if intent is not None:
                if intent.ledger_kind != LedgerKind.PAPER:
                    raise RuntimeError("backtest intents must use ledger_kind=paper")
                decision = self._resolve_decision(
                    intent,
                    candle,
                    risk_gate=risk_gate,
                    skip_risk=skip_risk,
                    now=created,
                )
                if decision is not None and may_submit_to_order_manager(
                    decision, now=created
                ):
                    prior_pos_qty = Decimal("0")
                    prior_entry: Decimal | None = None
                    pos = w_broker.ledger.position_for(
                        intent.instrument.exchange,
                        intent.instrument.market_type.value,
                        intent.instrument.symbol,
                    )
                    if pos:
                        prior_pos_qty = pos.quantity
                        prior_entry = pos.entry_price

                    w_broker.submit_from_risk_decision(
                        decision,
                        intent,
                        mid_price=candle.close,
                        now=candle.close_time,
                    )
                    fill = w_broker.ledger.fills[-1]
                    fee = fill.fee or Decimal("0")
                    notional = fill.quantity * fill.price
                    acc.costs.append(fee)
                    acc.notionals.append(notional)
                    self._record_trade_pnl(
                        acc,
                        side=intent.side,
                        qty=fill.quantity,
                        price=fill.price,
                        fee=fee,
                        prior_qty=prior_pos_qty,
                        prior_entry=prior_entry,
                    )
                    _bump_breakdown(
                        acc.by_instrument,
                        intent.instrument.symbol,
                        fee=fee,
                        notional=notional,
                    )
                    _bump_breakdown(
                        acc.by_timeframe,
                        candle.interval.value,
                        fee=fee,
                        notional=notional,
                    )

            equity = _mark_equity(w_broker.ledger, candle.close)
            acc.equity_curve.append(equity)
            pos_now = w_broker.ledger.position_for(
                candle.instrument.exchange,
                candle.instrument.market_type.value,
                candle.instrument.symbol,
            )
            if pos_now and pos_now.quantity != 0:
                acc.exposure_bars += 1

        return BacktestRunReport(
            run_id=run_id or uuid4(),
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            dataset_version=self.dataset_version,
            seed=self.seed,
            software_version=self.software_version,
            splits=self.splits,
            assumptions={
                **self.cost_model.to_assumptions(),
                "fill_model": "immediate_full_fill_at_mid_pm_slippage",
                "seed": str(self.seed),
            },
            train_metrics=_finalize(windows["train"]),
            validation_metrics=_finalize(windows["validation"]),
            oos_metrics=_finalize(windows["oos"]),
            uncertainty_notes=(
                "Metrics are descriptive for the configured sample only. "
                "Small trade counts imply high uncertainty; OOS must not be used for "
                "parameter fitting. No claim of profitability or future performance."
            ),
            created_at=created,
        )

    def _window_for(self, ts: datetime) -> str | None:
        s = self.splits
        if s.train_start <= ts <= s.train_end:
            return "train"
        if s.validation_start <= ts <= s.validation_end:
            return "validation"
        if s.oos_start <= ts <= s.oos_end:
            return "oos"
        return None

    def _resolve_decision(
        self,
        intent: OrderIntent,
        candle: Candle,
        *,
        risk_gate: RiskGate | None,
        skip_risk: bool,
        now: datetime,
    ) -> RiskDecision | None:
        if skip_risk:
            return _auto_approve(intent, now, policy="backtest-skip-risk")
        assert risk_gate is not None
        return risk_gate(intent, candle)

    def _record_trade_pnl(
        self,
        acc: _WindowAccumulator,
        *,
        side: str,
        qty: Decimal,
        price: Decimal,
        fee: Decimal,
        prior_qty: Decimal,
        prior_entry: Decimal | None,
    ) -> None:
        side_u = side.strip().upper()
        if side_u == "BUY":
            if prior_entry is None or prior_qty <= 0:
                acc.open_entry = price
                acc.open_qty = qty
            else:
                new_qty = prior_qty + qty
                acc.open_entry = ((prior_entry * prior_qty) + (price * qty)) / new_qty
                acc.open_qty = new_qty
            return
        if side_u == "SELL" and prior_qty > 0 and prior_entry is not None:
            close_qty = min(qty, prior_qty)
            gross = (price - prior_entry) * close_qty
            acc.trade_pnls.append(gross - fee)
            acc.open_qty = prior_qty - close_qty
            if acc.open_qty == 0:
                acc.open_entry = None


def _auto_approve(intent: OrderIntent, now: datetime, *, policy: str) -> RiskDecision:
    return RiskDecision(
        decision=RiskDecisionType.APPROVE,
        policy_version=policy,
        rules_evaluated=[policy.upper()],
        validated_order_params={"action": intent.side},
        decided_at=now,
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        correlation_id=intent.risk_decision_correlation_id,
        proposal_id=intent.proposal_id,
    )


def _finalize(acc: _WindowAccumulator) -> WindowMetrics:
    return compute_window_metrics(
        window=acc.name,
        equity_curve=acc.equity_curve,
        initial_equity=acc.initial_equity,
        trade_pnls=acc.trade_pnls,
        costs=acc.costs,
        notionals=acc.notionals,
        exposure_bars=acc.exposure_bars,
        total_bars=acc.total_bars,
        by_instrument=acc.by_instrument,
        by_timeframe=acc.by_timeframe,
    )


def _mark_equity(ledger: PaperLedger, mark: Decimal) -> Decimal:
    equity = ledger.get_balance()
    for pos in ledger.positions.values():
        if pos.quantity != 0:
            equity += pos.quantity * mark
    return equity


def _bump_breakdown(
    bucket: dict[str, dict[str, Any]],
    key: str,
    *,
    fee: Decimal,
    notional: Decimal,
) -> None:
    entry = bucket.setdefault(key, {"trade_count": 0, "cost": "0", "turnover": "0"})
    entry["trade_count"] = int(entry["trade_count"]) + 1
    entry["cost"] = str(Decimal(str(entry["cost"])) + fee)
    entry["turnover"] = str(Decimal(str(entry["turnover"])) + notional)


def always_flat(_replay: CandleReplay, _candle: Candle) -> OrderIntent | None:
    """Strategy hook that never trades."""
    return None


def buy_hold_one_bar_factory(*, quantity: Decimal = Decimal("1")) -> StrategyHook:
    """Buy once then sell on the next bar (test hook; not a trading edge)."""
    state: dict[str, bool] = {"long": False}

    def _hook(replay: CandleReplay, candle: Candle) -> OrderIntent | None:
        _ = replay.visible_candles()  # force look-ahead-safe access pattern
        now = candle.close_time
        if state["long"]:
            state["long"] = False
            return OrderIntent(
                intent_id=uuid4(),
                proposal_id=uuid4(),
                risk_decision_correlation_id="bt-sell",
                client_order_id=f"bt-sell-{now.isoformat()}",
                ledger_kind=LedgerKind.PAPER,
                instrument=candle.instrument,
                side="SELL",
                order_type="MARKET",
                quantity=quantity,
                price=None,
                created_at=now,
            )
        state["long"] = True
        return OrderIntent(
            intent_id=uuid4(),
            proposal_id=uuid4(),
            risk_decision_correlation_id="bt-buy",
            client_order_id=f"bt-buy-{now.isoformat()}",
            ledger_kind=LedgerKind.PAPER,
            instrument=candle.instrument,
            side="BUY",
            order_type="MARKET",
            quantity=quantity,
            price=None,
            created_at=now,
        )

    return _hook
