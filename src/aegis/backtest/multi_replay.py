"""Shared-evidence multi-parameter replay for deterministic rules (tuning speedup).

Why this exists: in ``decision_source="rules"`` mode, everything up to and including
the evidence package (validation, features, analysts, evidence build, Jev) depends
only on the visible candles, the clock and the assumed spread -- never on the rule
parameters or on ledger state. A parameter grid replayed with ``replay_windows``
would recompute that identical work once per parameter set. ``replay_rules_multi``
computes it **once per bar** and then runs the parameter-dependent tail (rule
decision -> Risk -> PaperBroker) for every parameter set, each with its own
isolated broker / ledger / equity tracker / proposal history.

The per-bar steps mirror ``aegis.pipeline.cycle._run_paper_cycle_body`` step by
step (same functions, same arguments, same order for each parameter state). The
equivalence is enforced by ``tests/test_backtest_walk_forward.py``, which replays
the same candles through ``run_paper_cycle`` per parameter set and asserts
identical metrics and counters. If the cycle body changes, that test must be
re-run (and this module updated) -- the rules tail is intentionally not a
re-implementation of Risk: it calls ``evaluate_risk`` and ``PaperBroker`` directly.

Restrictions (fail closed with ``PipelineReplayError``):

* paper / development only; ``trading_mode=live`` is refused;
* ``decision_source`` is fixed to ``rules`` (no LLM spend multiplied by the grid);
* the Jev port is evaluated once per bar and the same result is shared by all
  parameter states (equivalent only for deterministic Jev ports, e.g.
  ``UnavailableJevPort``, the only port used by the tuner).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from aegis.analysts.runner import run_analysts
from aegis.backtest.pipeline_replay import (
    CYCLE_CLOCK_OFFSET,
    PipelineReplayError,
    ReplayWindow,
    WindowOutcome,
    _check_replay_preconditions,
    _final_sorted,
    _synthetic_book,
    as_replayed,
    new_counters,
    outcome_from,
    record_bar,
)
from aegis.backtest.runner import _WindowAccumulator
from aegis.config.settings import Settings
from aegis.evidence.builder import build_evidence_package
from aegis.features.engine import compute_features
from aegis.interfaces.jev import JevPort
from aegis.interfaces.llm import LlmPort, UnavailableLlmPort
from aegis.paper.broker import PaperBroker
from aegis.paper.equity import (
    PaperEquityTracker,
    ProposalHistory,
    compute_paper_equity,
    instrument_mark_key,
)
from aegis.paper.exceptions import PaperBrokerError
from aegis.paper.exits import apply_exits
from aegis.paper.ledger import PaperLedger
from aegis.pipeline.context import build_paper_risk_context
from aegis.pipeline.cycle import PaperCycleDeps, _loss_limits, stale_after_ms
from aegis.pipeline.decide import make_proposal
from aegis.pipeline.defaults import proposal_defaults_from_policy
from aegis.pipeline.intent import IntentBuildError, intent_from_approved
from aegis.risk.engine import evaluate_risk
from aegis.risk.handoff import RiskHandoffDenied, may_submit_to_order_manager
from aegis.risk.policy import RiskPolicy
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef
from aegis.schemas.proposal import ProposalAction
from aegis.schemas.risk import RiskDecisionType
from aegis.strategy.rules import RuleParams
from aegis.validation.stage import validate_candles

RULES_SOURCE = "rules"


def _fixed_clock(value: datetime) -> Callable[[], datetime]:
    def _clock() -> datetime:
        return value

    return _clock


@dataclass
class ParamOutcome:
    """Outcome of one parameter set over one window."""

    params: RuleParams
    window: WindowOutcome
    counters: dict[str, int]
    # First Risk rejection rule_id for rule BUY candidates (diagnostic only).
    candidate_rejects: dict[str, int] = field(default_factory=dict)


@dataclass
class _State:
    params: RuleParams
    deps: PaperCycleDeps
    acc: _WindowAccumulator
    counters: dict[str, int]
    close_times: list[datetime] = field(default_factory=list)
    candidate_rejects: dict[str, int] = field(default_factory=dict)


def _new_state(
    params: RuleParams,
    *,
    settings: Settings,
    risk_policy: RiskPolicy,
    jev_port: JevPort,
    llm_port: LlmPort,
    initial: Decimal,
    strategy_id: str,
    strategy_version: str,
    name: str,
) -> _State:
    broker = PaperBroker(
        settings,
        ledger=PaperLedger(),
        fee_bps=settings.effective_fee_bps,
        initial_quote_balance=initial,
    )
    deps = PaperCycleDeps(
        settings=settings,
        risk_policy=risk_policy,
        jev_port=jev_port,
        llm_port=llm_port,
        supervisor_budgets=None,
        paper_broker=broker,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        equity_tracker=PaperEquityTracker(),
        proposal_history=ProposalHistory(),
        activity=None,
        audit=None,
        llm_call_limiter=None,
        decision_source=RULES_SOURCE,
        rule_params=params,
    )
    return _State(
        params=params,
        deps=deps,
        acc=_WindowAccumulator(name, initial),
        counters=new_counters(),
    )


async def replay_rules_multi(
    candles: Sequence[Candle],
    *,
    settings: Settings,
    risk_policy: RiskPolicy,
    instrument: InstrumentRef,
    timeframe: Timeframe,
    window: ReplayWindow,
    param_sets: Sequence[RuleParams],
    jev_port: JevPort,
    spread_bps: Decimal | None,
    initial_quote_balance: Decimal,
    window_candles: int = 100,
    strategy_id: str = "aegis-default",
    strategy_version: str = "0.1.0",
    analyst_concurrency: int = 5,
) -> list[ParamOutcome]:
    """Replay one window once, evaluating every parameter set in lockstep.

    Returns outcomes in ``param_sets`` order. Bars outside ``window`` are only used
    as trailing context (past data); decisions happen for bars whose
    ``close_time`` lies in ``[window.start, window.end]``.
    """
    if not param_sets:
        raise PipelineReplayError("param_sets must not be empty")
    _check_replay_preconditions(
        settings,
        window_candles=window_candles,
        initial_quote_balance=initial_quote_balance,
        spread_bps=spread_bps,
        decision_source=RULES_SOURCE,
        rule_params=param_sets[0],
    )
    if window.end < window.start:
        raise PipelineReplayError(f"window {window.name} ends before it starts")

    # Rules mode never calls the LLM; an Unavailable port makes that explicit.
    llm_port: LlmPort = UnavailableLlmPort()
    initial = Decimal(initial_quote_balance)
    states = [
        _new_state(
            p,
            settings=settings,
            risk_policy=risk_policy,
            jev_port=jev_port,
            llm_port=llm_port,
            initial=initial,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            name=window.name,
        )
        for p in param_sets
    ]
    finals = [
        c
        for c in _final_sorted(candles)
        if c.instrument.symbol == instrument.symbol
        and c.instrument.market_type == instrument.market_type
        and c.interval == timeframe
    ]
    replayed = [as_replayed(c) for c in finals]
    stale_ms = stale_after_ms(risk_policy)
    loss_limits = _loss_limits(risk_policy)
    slippage = Decimal(str(settings.paper_slippage_bps))

    for idx, bar in enumerate(finals):
        if not (window.start <= bar.close_time <= window.end):
            continue
        lo = max(0, idx + 1 - window_candles)
        visible = replayed[lo : idx + 1]
        if visible[-1].close_time != bar.close_time or any(
            c.close_time > bar.close_time for c in visible
        ):
            raise PipelineReplayError("look-ahead invariant broken")
        clock = bar.close_time + CYCLE_CLOCK_OFFSET
        corr = f"bt-{window.name}-{bar.open_time.isoformat()}"
        book_top = _synthetic_book(instrument, bar, spread_bps)

        marks: list[tuple[int, int]] = []
        for st in states:
            broker = st.deps.paper_broker
            st.acc.total_bars += 1
            st.counters["bars"] += 1
            st.counters["cycles"] += 1
            marks.append((len(broker.ledger.fills), len(broker.ledger.closed_trades)))
            # Replay-loop exit pass (the cycle's own second pass is an idempotent
            # no-op on the same bars, so it is not repeated here).
            apply_exits(broker, instrument, visible, now=clock)

        # --- Parameter-independent stage (computed once per bar) ---------------
        entries_allowed = not settings.kill_switch
        validation = validate_candles(
            visible,
            instrument=instrument,
            timeframe=timeframe,
            now=clock,
            stale_after_ms=stale_ms,
        )
        package = None
        jev_result = None
        if entries_allowed and validation.ok:
            snapshot = compute_features(
                validation.candles,
                instrument=instrument,
                timeframe=timeframe,
                as_of=validation.candles[-1].close_time,
                include_intrabar=False,
                created_at=clock,
            )
            analyst_evidence = await run_analysts(
                snapshot,
                validation.candles,
                evidence_time=clock,
                concurrency=analyst_concurrency,
                timeout_seconds=settings.analyst_timeout_seconds,
                spread=spread_bps,
            )
            package = build_evidence_package(
                snapshot,
                analyst_evidence,
                correlation_id=corr,
                created_at=clock,
                candles=validation.candles,
                spread_bps=spread_bps,
            )
            jev_result = await jev_port.evaluate(package)
        defaults = proposal_defaults_from_policy(
            risk_policy,
            fee_bps=settings.effective_fee_bps,
            slippage_bps=slippage,
            spread_bps=spread_bps,
        )

        # --- Parameter-dependent tail (per state, isolated ledgers) -------------
        for st, (fills_before, closed_before) in zip(states, marks, strict=True):
            deps = st.deps
            broker = deps.paper_broker
            if entries_allowed and not validation.ok:
                st.counters["validation_blocked"] += 1
            if package is not None and jev_result is not None:
                proposal = await make_proposal(
                    package,
                    jev_result,
                    decision_source=RULES_SOURCE,
                    rule_params=st.params,
                    llm=deps.llm_port,
                    budgets=None,
                    timeout_seconds=settings.supervisor_timeout_seconds,
                    strategy_id=deps.strategy_id,
                    strategy_version=deps.strategy_version,
                    clock=_fixed_clock(clock),
                    defaults=defaults,
                    call_limiter=None,
                )
                mark = validation.candles[-1].close
                context = build_paper_risk_context(
                    now=clock,
                    validation=validation,
                    ledger=broker.ledger,
                    instrument=instrument,
                    proposal=proposal,
                    mark_price=mark,
                    equity_tracker=deps.equity_tracker,
                    proposal_history=deps.proposal_history,
                    **loss_limits,
                    spread=spread_bps,
                    best_bid=None if book_top is None else book_top.best_bid,
                    best_ask=None if book_top is None else book_top.best_ask,
                    slippage_model_bps=slippage,
                    fee_estimate=settings.effective_fee_bps,
                )
                decision = evaluate_risk(proposal, context, settings, policy=risk_policy)
                deps.proposal_history.record(proposal.proposal_id)
                if decision.decision == RiskDecisionType.APPROVE:
                    st.counters["approved"] += 1
                elif proposal.action in {ProposalAction.BUY, ProposalAction.SELL}:
                    rule = (
                        str(decision.rejection_reasons[0].get("rule_id", "unknown"))
                        if decision.rejection_reasons
                        else "unknown"
                    )
                    st.candidate_rejects[rule] = st.candidate_rejects.get(rule, 0) + 1
                if may_submit_to_order_manager(decision, now=clock):
                    try:
                        intent = intent_from_approved(decision, proposal, now=clock)
                        broker.submit_from_risk_decision(
                            decision, intent, mid_price=mark, now=clock
                        )
                        deps.equity_tracker.observe(
                            compute_paper_equity(
                                broker.ledger,
                                marks={instrument_mark_key(instrument): mark},
                            ),
                            now=clock,
                            **loss_limits,
                        )
                    except (IntentBuildError, PaperBrokerError, RiskHandoffDenied):
                        st.counters["fill_blocked"] += 1
            record_bar(
                st.acc,
                st.counters,
                broker,
                instrument=instrument,
                timeframe=timeframe,
                bar=bar,
                fills_before=fills_before,
                closed_before=closed_before,
            )
            st.close_times.append(bar.close_time)

    return [
        ParamOutcome(
            params=st.params,
            window=outcome_from(st.acc, st.close_times),
            counters=st.counters,
            candidate_rejects=dict(st.candidate_rejects),
        )
        for st in states
    ]
