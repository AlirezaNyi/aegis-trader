"""Replay stored final candles through the full paper decision pipeline.

Each final bar (sorted by ``open_time``) triggers one ``run_paper_cycle`` call on the
trailing window of final candles whose ``close_time <= bar.close_time`` (no
look-ahead). ``received_at`` is rewritten to ``close_time`` so validation freshness
is evaluated as if the bar had just closed, and the cycle clock is
``bar.close_time + 1ms``.

Paper only: refuses ``trading_mode=live``; fills go through a per-window
``PaperBroker`` (``ledger_kind=paper``). No exchange client is constructed here.

Honest defaults:

* ``spread_bps`` is required as an explicit argument. Historical order-book spread is
  not stored, so any value is an assumption recorded in the report. ``None`` means
  "unobserved" and Risk rejects every trade (RP-MAX-SPREAD / RP-MIN-LIQUIDITY).
* Jev / LLM ports are caller-provided. ``UnavailableJevPort`` / ``UnavailableLlmPort``
  yield NO_TRADE on every bar (zero trades). A real LLM port must be passed
  explicitly and should be wrapped with ``CappedLlmPort``.

Protective exits: before each cycle, ``aegis.paper.exits.apply_exits`` scans the
visible final bars after entry for the Risk-approved stop-loss / take-profit
(stop first when both are inside one bar). It is idempotent, so a cycle that also
calls it on the same bars is a no-op. Trade PnL comes from the ledger's closed-trade
log (net of entry and exit fees) and R-multiples use ``(entry - stop) * qty``.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from aegis.backtest.metrics import RTrade
from aegis.backtest.runner import (
    _bump_breakdown,
    _finalize,
    _mark_equity,
    _WindowAccumulator,
)
from aegis.config.settings import Settings, TradingMode
from aegis.interfaces.jev import JevPort
from aegis.interfaces.llm import LlmPort
from aegis.paper.broker import PaperBroker
from aegis.paper.equity import PaperEquityTracker, ProposalHistory
from aegis.paper.exits import apply_exits
from aegis.paper.ledger import PaperLedger
from aegis.paper.protection import ExitReason
from aegis.pipeline.cycle import PaperCycleDeps, PaperCycleResult, run_paper_cycle
from aegis.pipeline.decide import DECISION_SOURCES
from aegis.risk.policy import RiskPolicy
from aegis.schemas.backtest import BacktestRunReport, SplitWindows, WindowMetrics
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef, OrderBookTop
from aegis.schemas.risk import RiskDecisionType
from aegis.strategy.rules import RuleParams
from aegis.supervisor.budgets import BudgetConfig, DailyCallLimiter

PIPELINE_REPLAY_VERSION = "aegis-0.1.0-pipeline-replay"
EXIT_ORDER_PREFIX = "paper-exit-"
CYCLE_CLOCK_OFFSET = timedelta(milliseconds=1)
WINDOW_NAMES: tuple[str, ...] = ("train", "validation", "oos")


class CycleFn(Protocol):
    def __call__(
        self,
        candles: Sequence[Candle],
        *,
        deps: PaperCycleDeps,
        instrument: InstrumentRef,
        timeframe: Timeframe,
        now: datetime | None = None,
        correlation_id: str | None = None,
        spread_bps: Decimal | None = None,
        book_top: OrderBookTop | None = None,
    ) -> Awaitable[PaperCycleResult]: ...


class PipelineReplayError(RuntimeError):
    """Raised when replay preconditions fail (live mode, bad window, etc.)."""


class LlmCallCapExceeded(RuntimeError):
    """Raised by ``CappedLlmPort`` once the total backtest call cap is reached."""


class CappedLlmPort:
    """Total (whole-run) LLM call cap for backtests.

    ``DailyCallLimiter`` resets per *historical* UTC day of the cycle clock, so a
    multi-day replay could otherwise spend far more than intended. Once the cap is
    reached this port raises, which the Supervisor treats as NO_TRADE (fail closed).
    """

    def __init__(self, inner: LlmPort, *, max_calls: int) -> None:
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls <= 0:
            raise ValueError("CappedLlmPort max_calls must be a positive int")
        self._inner = inner
        self._max_calls = max_calls
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        return self._used

    @property
    def max_calls(self) -> int:
        return self._max_calls

    async def complete_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        with self._lock:
            if self._used >= self._max_calls:
                raise LlmCallCapExceeded(
                    f"backtest LLM call cap reached ({self._max_calls})"
                )
            self._used += 1
        return await self._inner.complete_structured(
            prompt=prompt,
            schema_name=schema_name,
            timeout_seconds=timeout_seconds,
        )


def window_for(splits: SplitWindows, ts: datetime) -> str | None:
    if splits.train_start <= ts <= splits.train_end:
        return "train"
    if splits.validation_start <= ts <= splits.validation_end:
        return "validation"
    if splits.oos_start <= ts <= splits.oos_end:
        return "oos"
    return None


def as_replayed(candle: Candle) -> Candle:
    """Copy with ``received_at = close_time`` (bar observed exactly at close)."""
    return candle.model_copy(update={"received_at": candle.close_time})


def _final_sorted(candles: Sequence[Candle]) -> list[Candle]:
    by_open: dict[datetime, Candle] = {}
    for c in candles:
        if not c.is_final:
            continue
        by_open.setdefault(c.open_time, c)
    return [by_open[k] for k in sorted(by_open)]


def _synthetic_book(
    instrument: InstrumentRef, bar: Candle, spread_bps: Decimal | None
) -> OrderBookTop | None:
    """Backtest-only book: bid/ask straddle the bar close by the ASSUMED spread.

    Historical order books are not stored. The same caller-supplied spread
    assumption already recorded in the report is used; None => no book => Risk
    rejects every LIMIT trade (honest).
    """
    if spread_bps is None:
        return None
    half = bar.close * Decimal(spread_bps) / Decimal("20000")
    return OrderBookTop(
        instrument=instrument,
        best_bid=bar.close - half,
        best_bid_qty=Decimal("0"),
        best_ask=bar.close + half,
        best_ask_qty=Decimal("0"),
        exchange_time=bar.close_time,
        received_at=bar.close_time,
    )


@dataclass(frozen=True)
class ReplayWindow:
    """Named inclusive ``[start, end]`` window on bar ``close_time``."""

    name: str
    start: datetime
    end: datetime


@dataclass
class WindowOutcome:
    """Metrics plus the raw series needed to stitch windows (walk-forward)."""

    metrics: WindowMetrics
    r_trades: list[RTrade]
    trade_pnls: list[Decimal]
    equity_curve: list[Decimal]
    initial_equity: Decimal
    bar_close_times: list[datetime]


@dataclass
class ReplayOutcome:
    windows: dict[str, WindowOutcome]
    counters: dict[str, int]


def new_counters() -> dict[str, int]:
    return {
        "bars": 0,
        "cycles": 0,
        "validation_blocked": 0,
        "approved": 0,
        "filled": 0,
        "fill_blocked": 0,
        "exit_fills": 0,
        "closed_trades": 0,
        **{f"closed_{reason.value}": 0 for reason in ExitReason},
    }


def record_bar(
    acc: _WindowAccumulator,
    counters: dict[str, int],
    broker: PaperBroker,
    *,
    instrument: InstrumentRef,
    timeframe: Timeframe,
    bar: Candle,
    fills_before: int,
    closed_before: int,
) -> None:
    """Account this bar's new fills / closed trades and mark equity at bar close."""
    new_fills = broker.ledger.fills[fills_before:]
    if new_fills:
        client_ids = {o.order_id: o.client_order_id for o in broker.ledger.orders.values()}
        for fill in new_fills:
            if client_ids.get(fill.order_id, "").startswith(EXIT_ORDER_PREFIX):
                counters["exit_fills"] += 1
            else:
                counters["filled"] += 1
            fee = fill.fee or Decimal("0")
            notional = fill.quantity * fill.price
            acc.costs.append(fee)
            acc.notionals.append(notional)
            _bump_breakdown(acc.by_instrument, instrument.symbol, fee=fee, notional=notional)
            _bump_breakdown(acc.by_timeframe, timeframe.value, fee=fee, notional=notional)

    for trade in broker.ledger.closed_trades[closed_before:]:
        counters["closed_trades"] += 1
        counters[f"closed_{trade.reason.value}"] += 1
        acc.trade_pnls.append(trade.realized_pnl)
        if trade.initial_risk is not None and trade.initial_risk > 0:
            acc.r_trades.append(RTrade(pnl=trade.realized_pnl, initial_risk=trade.initial_risk))

    acc.equity_curve.append(_mark_equity(broker.ledger, bar.close))
    pos_now = broker.ledger.position_for(
        instrument.exchange, instrument.market_type.value, instrument.symbol
    )
    if pos_now and pos_now.quantity != 0:
        acc.exposure_bars += 1


def outcome_from(acc: _WindowAccumulator, close_times: list[datetime]) -> WindowOutcome:
    return WindowOutcome(
        metrics=_finalize(acc),
        r_trades=list(acc.r_trades),
        trade_pnls=list(acc.trade_pnls),
        equity_curve=list(acc.equity_curve),
        initial_equity=acc.initial_equity,
        bar_close_times=list(close_times),
    )


def _check_replay_preconditions(
    settings: Settings,
    *,
    window_candles: int,
    initial_quote_balance: Decimal,
    spread_bps: Decimal | None,
    decision_source: str,
    rule_params: RuleParams | None,
) -> None:
    if settings.trading_mode == TradingMode.LIVE:
        raise PipelineReplayError("pipeline replay refuses trading_mode=live")
    if settings.trading_mode not in {TradingMode.PAPER, TradingMode.DEVELOPMENT}:
        raise PipelineReplayError(
            f"pipeline replay requires paper/development, got {settings.trading_mode.value}"
        )
    if window_candles < 1:
        raise PipelineReplayError("window_candles must be >= 1")
    if Decimal(initial_quote_balance) <= 0:
        raise PipelineReplayError("initial_quote_balance must be positive")
    if spread_bps is not None and Decimal(spread_bps) < 0:
        raise PipelineReplayError("spread_bps must be >= 0 when provided")
    if decision_source not in DECISION_SOURCES:
        raise PipelineReplayError(f"unknown decision_source {decision_source!r}")
    if decision_source != "llm" and rule_params is None:
        raise PipelineReplayError(f"decision_source={decision_source} requires rule_params")


async def replay_windows(
    candles: Sequence[Candle],
    *,
    settings: Settings,
    risk_policy: RiskPolicy,
    instrument: InstrumentRef,
    timeframe: Timeframe,
    windows: Sequence[ReplayWindow],
    jev_port: JevPort,
    llm_port: LlmPort,
    supervisor_budgets: BudgetConfig | None,
    spread_bps: Decimal | None,
    initial_quote_balance: Decimal,
    window_candles: int = 100,
    call_limiter: DailyCallLimiter | None = None,
    strategy_id: str = "aegis-default",
    strategy_version: str = "0.1.0",
    decision_source: str = "llm",
    rule_params: RuleParams | None = None,
    cycle_fn: CycleFn | None = None,
    on_cycle: Callable[[str, Candle, PaperCycleResult], None] | None = None,
) -> ReplayOutcome:
    """Replay final bars inside non-overlapping ``windows`` through the paper cycle.

    Each window gets its own ``PaperBroker`` / ledger / equity tracker / proposal
    history (no state crosses windows). Bars before a window start may appear in the
    trailing ``window_candles`` context (they are in the past: not look-ahead), but
    no decision is taken on them for that window.
    """
    _check_replay_preconditions(
        settings,
        window_candles=window_candles,
        initial_quote_balance=initial_quote_balance,
        spread_bps=spread_bps,
        decision_source=decision_source,
        rule_params=rule_params,
    )
    names = [w.name for w in windows]
    if len(set(names)) != len(names):
        raise PipelineReplayError("window names must be unique")
    ordered = sorted(windows, key=lambda w: w.start)
    for w in ordered:
        if w.end < w.start:
            raise PipelineReplayError(f"window {w.name} ends before it starts")
    for a, b in zip(ordered, ordered[1:], strict=False):
        if b.start <= a.end:
            raise PipelineReplayError(f"windows {a.name} and {b.name} overlap")

    initial = Decimal(initial_quote_balance)
    cycle: CycleFn = cycle_fn if cycle_fn is not None else run_paper_cycle
    finals = [
        c
        for c in _final_sorted(candles)
        if c.instrument.symbol == instrument.symbol
        and c.instrument.market_type == instrument.market_type
        and c.interval == timeframe
    ]
    # One replayed copy per bar (identical to copying inside the loop, far cheaper).
    replayed = [as_replayed(c) for c in finals]

    accs = {name: _WindowAccumulator(name, initial) for name in names}
    close_times: dict[str, list[datetime]] = {name: [] for name in names}
    deps_by_window: dict[str, PaperCycleDeps] = {}
    for name in names:
        broker = PaperBroker(
            settings,
            ledger=PaperLedger(),
            # Same fee the Risk cost model uses (owner fee when set, else paper default)
            # so realized R is comparable with Risk's ex-ante reward:risk.
            fee_bps=settings.effective_fee_bps,
            initial_quote_balance=initial,
        )
        deps_by_window[name] = PaperCycleDeps(
            settings=settings,
            risk_policy=risk_policy,
            jev_port=jev_port,
            llm_port=llm_port,
            supervisor_budgets=supervisor_budgets,
            paper_broker=broker,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            equity_tracker=PaperEquityTracker(),
            proposal_history=ProposalHistory(),
            activity=None,
            audit=None,
            llm_call_limiter=call_limiter,
            decision_source=decision_source,
            rule_params=rule_params,
        )

    counters = new_counters()

    def _window_name(ts: datetime) -> str | None:
        for w in ordered:
            if w.start <= ts <= w.end:
                return w.name
        return None

    for idx, bar in enumerate(finals):
        window_name = _window_name(bar.close_time)
        if window_name is None:
            continue
        acc = accs[window_name]
        deps = deps_by_window[window_name]
        broker = deps.paper_broker
        acc.total_bars += 1
        counters["bars"] += 1

        lo = max(0, idx + 1 - window_candles)
        visible = replayed[lo : idx + 1]
        if visible[-1].close_time != bar.close_time or any(
            c.close_time > bar.close_time for c in visible
        ):
            raise PipelineReplayError("look-ahead invariant broken")

        clock = bar.close_time + CYCLE_CLOCK_OFFSET
        fills_before = len(broker.ledger.fills)
        closed_before = len(broker.ledger.closed_trades)

        # Resting protective stop / target first (reduce-only, no Risk round-trip).
        apply_exits(broker, instrument, visible, now=clock)

        result = await cycle(
            visible,
            deps=deps,
            instrument=instrument,
            timeframe=timeframe,
            now=clock,
            correlation_id=f"bt-{window_name}-{bar.open_time.isoformat()}",
            spread_bps=spread_bps,
            book_top=_synthetic_book(instrument, bar, spread_bps),
        )
        counters["cycles"] += 1
        if not result.validation.ok:
            counters["validation_blocked"] += 1
        if result.decision is not None and result.decision.decision == RiskDecisionType.APPROVE:
            counters["approved"] += 1
        if result.blocked_reason and result.blocked_reason.startswith("paper_fill_blocked"):
            counters["fill_blocked"] += 1

        record_bar(
            acc,
            counters,
            broker,
            instrument=instrument,
            timeframe=timeframe,
            bar=bar,
            fills_before=fills_before,
            closed_before=closed_before,
        )
        close_times[window_name].append(bar.close_time)
        if on_cycle is not None:
            on_cycle(window_name, bar, result)

    outcomes = {name: outcome_from(accs[name], close_times[name]) for name in names}
    return ReplayOutcome(windows=outcomes, counters=counters)


def replay_assumptions(
    *,
    settings: Settings,
    risk_policy: RiskPolicy,
    instrument: InstrumentRef,
    timeframe: Timeframe,
    window_candles: int,
    spread_bps: Decimal | None,
    jev_port: JevPort,
    llm_port: LlmPort,
    decision_source: str,
    rule_params: RuleParams | None,
    data_source: str | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "mode": "full_pipeline_replay",
        "instrument": f"{instrument.market_type.value}:{instrument.symbol}",
        "timeframe": timeframe.value,
        "window_candles": str(window_candles),
        "cycle_clock": "bar.close_time + 1ms; received_at rewritten to close_time",
        "spread_bps": "none" if spread_bps is None else str(spread_bps),
        "fee_bps": str(settings.effective_fee_bps),
        "slippage_bps": str(settings.paper_slippage_bps),
        "fill_model": "paper_broker_immediate_fill_at_bar_close_pm_slippage",
        "exit_model": (
            "protective stop/target on final bars after entry: open<=stop fills at "
            "open (gap), low<=stop fills at stop, high>=target fills at target; "
            "stop first when both are in one bar; stop fills get slippage_bps, "
            "target fills none; full position closed"
        ),
        "trade_pnl": "net of entry and exit fees; R = pnl / ((entry - stop) * qty)",
        "jev_port": type(jev_port).__name__,
        "llm_port": type(llm_port).__name__,
        "risk_policy_version": risk_policy.policy_version,
        "decision_source": decision_source,
    }
    if rule_params is not None:
        out["rule_params"] = rule_params.to_dict()
        out["rule_params_hash"] = rule_params.param_hash()
    if data_source is not None:
        out["data_source"] = data_source
        out["data_exchange_label"] = instrument.exchange
    return out


def spread_uncertainty_note(spread_bps: Decimal | None) -> str:
    if spread_bps is None:
        return "spread_bps=None (unobserved): Risk rejects every trade by design."
    return (
        f"spread_bps={spread_bps} is a constant caller ASSUMPTION; historical "
        "order-book spread is not stored and was not observed."
    )


REPLAY_UNCERTAINTY = (
    "Fills occur at the decision bar's close (no queue, no partial fills, no "
    "latency). Protective exits are bar-resolution: the intrabar path is unknown, so "
    "a bar touching both stop and target counts as a stop; exits pay no spread. Daily "
    "LLM call limiter (if any) resets per historical UTC day. LLM outputs are "
    "non-deterministic unless scripted. Metrics are descriptive for this sample only; "
    "small trade counts imply high uncertainty; OOS must not be used for parameter "
    "fitting. No claim of profitability or future performance."
)


async def replay_pipeline(
    candles: Sequence[Candle],
    *,
    settings: Settings,
    risk_policy: RiskPolicy,
    instrument: InstrumentRef,
    timeframe: Timeframe,
    splits: SplitWindows,
    jev_port: JevPort,
    llm_port: LlmPort,
    supervisor_budgets: BudgetConfig | None,
    spread_bps: Decimal | None,
    initial_quote_balance: Decimal,
    window_candles: int = 100,
    call_limiter: DailyCallLimiter | None = None,
    dataset_version: str = "candle-store",
    strategy_id: str = "aegis-default",
    strategy_version: str = "0.1.0",
    run_id: UUID | None = None,
    created_at: datetime | None = None,
    cycle_fn: CycleFn | None = None,
    on_cycle: Callable[[str, Candle, PaperCycleResult], None] | None = None,
    decision_source: str = "llm",
    rule_params: RuleParams | None = None,
    data_source: str | None = None,
) -> BacktestRunReport:
    """Replay finalized candles bar-by-bar through ``run_paper_cycle``.

    ``decision_source`` / ``rule_params`` are passed through to ``PaperCycleDeps``
    (``rules`` needs no LLM/Jev). ``data_source`` (e.g. ``binance-archive``) is
    recorded in the assumptions when candles come from a research archive rather
    than the trading venue. ``cycle_fn`` exists for tests and the cached
    walk-forward cycle; production callers leave it ``None``.
    """
    outcome = await replay_windows(
        candles,
        settings=settings,
        risk_policy=risk_policy,
        instrument=instrument,
        timeframe=timeframe,
        windows=[
            ReplayWindow("train", splits.train_start, splits.train_end),
            ReplayWindow("validation", splits.validation_start, splits.validation_end),
            ReplayWindow("oos", splits.oos_start, splits.oos_end),
        ],
        jev_port=jev_port,
        llm_port=llm_port,
        supervisor_budgets=supervisor_budgets,
        spread_bps=spread_bps,
        initial_quote_balance=initial_quote_balance,
        window_candles=window_candles,
        call_limiter=call_limiter,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        decision_source=decision_source,
        rule_params=rule_params,
        cycle_fn=cycle_fn,
        on_cycle=on_cycle,
    )
    assumptions = replay_assumptions(
        settings=settings,
        risk_policy=risk_policy,
        instrument=instrument,
        timeframe=timeframe,
        window_candles=window_candles,
        spread_bps=spread_bps,
        jev_port=jev_port,
        llm_port=llm_port,
        decision_source=decision_source,
        rule_params=rule_params,
        data_source=data_source,
    )
    assumptions["counters"] = {k: str(v) for k, v in outcome.counters.items()}
    return BacktestRunReport(
        run_id=run_id or uuid4(),
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        dataset_version=dataset_version,
        seed=0,
        software_version=PIPELINE_REPLAY_VERSION,
        splits=splits,
        assumptions=assumptions,
        train_metrics=outcome.windows["train"].metrics,
        validation_metrics=outcome.windows["validation"].metrics,
        oos_metrics=outcome.windows["oos"].metrics,
        uncertainty_notes=f"{spread_uncertainty_note(spread_bps)} {REPLAY_UNCERTAINTY}",
        created_at=created_at if created_at is not None else datetime.now(tz=UTC),
    )
