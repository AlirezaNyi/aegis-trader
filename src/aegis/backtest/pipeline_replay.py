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
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from aegis.backtest.runner import (
    _bump_breakdown,
    _finalize,
    _mark_equity,
    _WindowAccumulator,
    record_trade_pnl,
)
from aegis.config.settings import Settings, TradingMode
from aegis.interfaces.jev import JevPort
from aegis.interfaces.llm import LlmPort
from aegis.paper.broker import PaperBroker
from aegis.paper.equity import PaperEquityTracker, ProposalHistory
from aegis.paper.ledger import PaperLedger
from aegis.pipeline.cycle import PaperCycleDeps, PaperCycleResult, run_paper_cycle
from aegis.risk.policy import RiskPolicy
from aegis.schemas.backtest import BacktestRunReport, SplitWindows
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef, OrderBookTop
from aegis.schemas.risk import RiskDecisionType
from aegis.supervisor.budgets import BudgetConfig, DailyCallLimiter

PIPELINE_REPLAY_VERSION = "aegis-0.1.0-pipeline-replay"
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

    def complete_structured(
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
        return self._inner.complete_structured(
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
) -> BacktestRunReport:
    """Replay finalized candles bar-by-bar through ``run_paper_cycle``.

    ``cycle_fn`` exists for tests (spy wrapping ``run_paper_cycle``); production
    callers leave it ``None``.
    """
    if settings.trading_mode == TradingMode.LIVE:
        raise PipelineReplayError("pipeline replay refuses trading_mode=live")
    if settings.trading_mode not in {TradingMode.PAPER, TradingMode.DEVELOPMENT}:
        raise PipelineReplayError(
            f"pipeline replay requires paper/development, got {settings.trading_mode.value}"
        )
    if window_candles < 1:
        raise PipelineReplayError("window_candles must be >= 1")
    initial = Decimal(initial_quote_balance)
    if initial <= 0:
        raise PipelineReplayError("initial_quote_balance must be positive")
    if spread_bps is not None and Decimal(spread_bps) < 0:
        raise PipelineReplayError("spread_bps must be >= 0 when provided")

    cycle: CycleFn = cycle_fn if cycle_fn is not None else run_paper_cycle
    finals = [
        c
        for c in _final_sorted(candles)
        if c.instrument.symbol == instrument.symbol
        and c.instrument.market_type == instrument.market_type
        and c.interval == timeframe
    ]

    accs = {name: _WindowAccumulator(name, initial) for name in WINDOW_NAMES}
    deps_by_window: dict[str, PaperCycleDeps] = {}
    for name in WINDOW_NAMES:
        broker = PaperBroker(
            settings,
            ledger=PaperLedger(),
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
        )

    counters = {
        "bars": 0,
        "cycles": 0,
        "validation_blocked": 0,
        "approved": 0,
        "filled": 0,
        "fill_blocked": 0,
    }

    for idx, bar in enumerate(finals):
        window_name = window_for(splits, bar.close_time)
        if window_name is None:
            continue
        acc = accs[window_name]
        deps = deps_by_window[window_name]
        broker = deps.paper_broker
        acc.total_bars += 1
        counters["bars"] += 1

        lo = max(0, idx + 1 - window_candles)
        visible = [as_replayed(c) for c in finals[lo : idx + 1]]
        if visible[-1].close_time != bar.close_time or any(
            c.close_time > bar.close_time for c in visible
        ):
            raise PipelineReplayError("look-ahead invariant broken")

        clock = bar.close_time + CYCLE_CLOCK_OFFSET
        fills_before = len(broker.ledger.fills)
        pos = broker.ledger.position_for(
            instrument.exchange, instrument.market_type.value, instrument.symbol
        )
        prior_qty = pos.quantity if pos else Decimal("0")
        prior_entry = pos.entry_price if pos else None

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

        for fill in broker.ledger.fills[fills_before:]:
            counters["filled"] += 1
            fee = fill.fee or Decimal("0")
            notional = fill.quantity * fill.price
            side = result.order.side if result.order is not None else "BUY"
            acc.costs.append(fee)
            acc.notionals.append(notional)
            record_trade_pnl(
                acc,
                side=side,
                qty=fill.quantity,
                price=fill.price,
                fee=fee,
                prior_qty=prior_qty,
                prior_entry=prior_entry,
            )
            _bump_breakdown(acc.by_instrument, instrument.symbol, fee=fee, notional=notional)
            _bump_breakdown(acc.by_timeframe, timeframe.value, fee=fee, notional=notional)

        acc.equity_curve.append(_mark_equity(broker.ledger, bar.close))
        pos_now = broker.ledger.position_for(
            instrument.exchange, instrument.market_type.value, instrument.symbol
        )
        if pos_now and pos_now.quantity != 0:
            acc.exposure_bars += 1
        if on_cycle is not None:
            on_cycle(window_name, bar, result)

    spread_note = (
        "spread_bps=None (unobserved): Risk rejects every trade by design."
        if spread_bps is None
        else (
            f"spread_bps={spread_bps} is a constant caller ASSUMPTION; historical "
            "order-book spread is not stored and was not observed."
        )
    )
    return BacktestRunReport(
        run_id=run_id or uuid4(),
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        dataset_version=dataset_version,
        seed=0,
        software_version=PIPELINE_REPLAY_VERSION,
        splits=splits,
        assumptions={
            "mode": "full_pipeline_replay",
            "instrument": f"{instrument.market_type.value}:{instrument.symbol}",
            "timeframe": timeframe.value,
            "window_candles": str(window_candles),
            "cycle_clock": "bar.close_time + 1ms; received_at rewritten to close_time",
            "spread_bps": "none" if spread_bps is None else str(spread_bps),
            "fee_bps": str(settings.paper_fee_bps),
            "slippage_bps": str(settings.paper_slippage_bps),
            "fill_model": "paper_broker_immediate_fill_at_bar_close_pm_slippage",
            "jev_port": type(jev_port).__name__,
            "llm_port": type(llm_port).__name__,
            "risk_policy_version": risk_policy.policy_version,
            "counters": {k: str(v) for k, v in counters.items()},
        },
        train_metrics=_finalize(accs["train"]),
        validation_metrics=_finalize(accs["validation"]),
        oos_metrics=_finalize(accs["oos"]),
        uncertainty_notes=(
            f"{spread_note} Fills occur at the decision bar's close (no queue, no "
            "partial fills, no latency). Daily LLM call limiter (if any) resets per "
            "historical UTC day. LLM outputs are non-deterministic unless scripted. "
            "Metrics are descriptive for this sample only; small trade counts imply "
            "high uncertainty; OOS must not be used for parameter fitting. No claim "
            "of profitability or future performance."
        ),
        created_at=created_at if created_at is not None else datetime.now(tz=UTC),
    )
