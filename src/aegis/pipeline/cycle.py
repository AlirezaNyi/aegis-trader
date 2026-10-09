"""Paper-only decision cycle — explicit orchestration on finalized candles."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from aegis.analysts.runner import run_analysts
from aegis.config.settings import Settings, TradingMode
from aegis.evidence.builder import build_evidence_package, empty_package_shell
from aegis.features.engine import compute_features
from aegis.interfaces.jev import JevPort
from aegis.interfaces.llm import LlmPort
from aegis.paper.broker import PaperBroker
from aegis.paper.equity import (
    PaperEquityTracker,
    ProposalHistory,
    compute_paper_equity,
    instrument_mark_key,
)
from aegis.paper.exceptions import PaperBrokerError
from aegis.pipeline.activity import ActivityStage, ActivityState
from aegis.pipeline.context import build_paper_risk_context
from aegis.pipeline.intent import IntentBuildError, intent_from_approved
from aegis.risk.engine import evaluate_risk
from aegis.risk.handoff import RiskHandoffDenied, may_submit_to_order_manager
from aegis.risk.policy import ParamStatus, RiskPolicy
from aegis.schemas.common import EvidenceStatus, Timeframe
from aegis.schemas.evidence import JevResult
from aegis.schemas.market import Candle, InstrumentRef
from aegis.schemas.orders import Order
from aegis.schemas.proposal import TradeProposal
from aegis.schemas.risk import RiskDecision, RiskDecisionType
from aegis.supervisor.budgets import BudgetConfig
from aegis.supervisor.supervise import no_trade_proposal, supervise
from aegis.validation.stage import ValidationResult, validate_candles


@dataclass(frozen=True)
class PaperCycleDeps:
    """Ports and state for one paper cycle. Never includes Toobit credentials."""

    settings: Settings
    risk_policy: RiskPolicy
    jev_port: JevPort
    llm_port: LlmPort
    supervisor_budgets: BudgetConfig | None
    paper_broker: PaperBroker
    analyst_concurrency: int = 5
    strategy_id: str = "aegis-default"
    strategy_version: str = "0.1.0"
    equity_tracker: PaperEquityTracker = field(default_factory=PaperEquityTracker)
    proposal_history: ProposalHistory = field(default_factory=ProposalHistory)
    activity: ActivityState | None = None


def _notify_activity(
    deps: PaperCycleDeps,
    stage: ActivityStage,
    *,
    instrument: InstrumentRef | None = None,
    timeframe: Timeframe | None = None,
    final_open_time: datetime | None = None,
    correlation_id: str | None = None,
    detail: str | None = None,
) -> None:
    if deps.activity is None:
        return
    deps.activity.set(
        stage,
        symbol=None if instrument is None else instrument.symbol,
        timeframe=None if timeframe is None else timeframe.value,
        final_open_time=final_open_time,
        correlation_id=correlation_id,
        detail=detail,
    )


@dataclass(frozen=True)
class PaperCycleResult:
    correlation_id: str
    validation: ValidationResult
    proposal: TradeProposal | None
    decision: RiskDecision | None
    order: Order | None
    blocked_reason: str | None = None


def _stale_after_ms(policy: RiskPolicy) -> int:
    param = policy.get("RP-DATA-FRESHNESS-MS")
    if param is not None and param.status == ParamStatus.APPROVED and param.value is not None:
        return int(param.value)
    return 5000


def _daily_loss_limit(policy: RiskPolicy) -> Decimal | None:
    param = policy.get("RP-DAILY-LOSS-LIMIT")
    if param is not None and param.status == ParamStatus.APPROVED and param.value is not None:
        return Decimal(str(param.value))
    return None


def _reject_for_validation(
    *,
    correlation_id: str,
    instrument: InstrumentRef,
    timeframe: Timeframe,
    now: datetime,
    validation: ValidationResult,
    strategy_id: str,
    strategy_version: str,
) -> tuple[TradeProposal, RiskDecision]:
    """Fail closed without LLM spend when market data is not usable."""
    package = empty_package_shell(
        instrument=instrument,
        market_type=instrument.market_type,
        timeframe=timeframe,
        as_of=now,
        correlation_id=correlation_id,
    )
    jev = JevResult(status=EvidenceStatus.UNAVAILABLE)
    reasons = ["validation_blocked", *validation.block_reasons]
    proposal = no_trade_proposal(
        package,
        jev,
        reasons=reasons,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        clock=lambda: now,
    )
    rejection_reasons: list[dict[str, Any]] = []
    if "stale_market_data" in validation.block_reasons:
        rejection_reasons.append(
            {
                "rule_id": "RP-DATA-FRESHNESS-MS",
                "detail": (
                    f"market_data_age_ms={validation.market_data_age_ms} "
                    "blocked by validation"
                ),
            }
        )
    if not validation.market_integrity_ok or validation.block_reasons:
        rejection_reasons.append(
            {
                "rule_id": "RP-DATA-INTEGRITY",
                "detail": (
                    f"validation.ok={validation.ok}; "
                    f"reasons={list(validation.block_reasons)}"
                ),
            }
        )
    if not rejection_reasons:
        rejection_reasons.append(
            {
                "rule_id": "RP-DATA-INTEGRITY",
                "detail": "validation blocked cycle",
            }
        )
    decision = RiskDecision(
        decision=RiskDecisionType.REJECT,
        policy_version="validation",
        rules_evaluated=[r["rule_id"] for r in rejection_reasons],
        rejection_reasons=rejection_reasons,
        validated_order_params=None,
        account_state_refs=["acct:paper"],
        market_state_refs=[f"md:{instrument.symbol}"],
        decided_at=now,
        expires_at=proposal.expires_at,
        correlation_id=correlation_id,
        proposal_id=proposal.proposal_id,
    )
    return proposal, decision


async def run_paper_cycle(
    candles: Sequence[Candle],
    *,
    deps: PaperCycleDeps,
    instrument: InstrumentRef,
    timeframe: Timeframe,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> PaperCycleResult:
    """Run one decision cycle on finalized candles. Paper/development only.

    Never constructs a live Toobit submit client. Never calls OrderManager.
    Unavailable Jev/LLM fail closed to NO_TRADE / REJECT with zero fills.
    """
    clock = now if now is not None else datetime.now(tz=UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    corr = correlation_id or f"paper-cycle-{uuid4().hex[:12]}"
    finals = [c for c in candles if c.is_final]
    bar_open = finals[-1].open_time if finals else None
    _notify_activity(
        deps,
        ActivityStage.VALIDATING,
        instrument=instrument,
        timeframe=timeframe,
        final_open_time=bar_open,
        correlation_id=corr,
    )

    if deps.settings.trading_mode == TradingMode.LIVE:
        empty = ValidationResult(
            ok=False,
            candles=(),
            issues=(),
            market_data_age_ms=None,
            market_integrity_ok=False,
            block_reasons=("live_mode_refused",),
        )
        return PaperCycleResult(
            correlation_id=corr,
            validation=empty,
            proposal=None,
            decision=None,
            order=None,
            blocked_reason="trading_mode_live",
        )

    if deps.settings.kill_switch:
        empty = ValidationResult(
            ok=False,
            candles=(),
            issues=(),
            market_data_age_ms=None,
            market_integrity_ok=False,
            block_reasons=("kill_switch",),
        )
        return PaperCycleResult(
            correlation_id=corr,
            validation=empty,
            proposal=None,
            decision=None,
            order=None,
            blocked_reason="kill_switch",
        )

    if deps.settings.trading_mode not in {
        TradingMode.PAPER,
        TradingMode.DEVELOPMENT,
    }:
        empty = ValidationResult(
            ok=False,
            candles=(),
            issues=(),
            market_data_age_ms=None,
            market_integrity_ok=False,
            block_reasons=("unsupported_trading_mode",),
        )
        return PaperCycleResult(
            correlation_id=corr,
            validation=empty,
            proposal=None,
            decision=None,
            order=None,
            blocked_reason=f"unsupported_mode:{deps.settings.trading_mode.value}",
        )

    validation = validate_candles(
        candles,
        instrument=instrument,
        timeframe=timeframe,
        now=clock,
        stale_after_ms=_stale_after_ms(deps.risk_policy),
    )

    if not validation.ok:
        proposal, decision = _reject_for_validation(
            correlation_id=corr,
            instrument=instrument,
            timeframe=timeframe,
            now=clock,
            validation=validation,
            strategy_id=deps.strategy_id,
            strategy_version=deps.strategy_version,
        )
        return PaperCycleResult(
            correlation_id=corr,
            validation=validation,
            proposal=proposal,
            decision=decision,
            order=None,
        )

    as_of = validation.candles[-1].close_time
    _notify_activity(
        deps,
        ActivityStage.FEATURES,
        instrument=instrument,
        timeframe=timeframe,
        final_open_time=validation.candles[-1].open_time,
        correlation_id=corr,
    )
    snapshot = compute_features(
        validation.candles,
        instrument=instrument,
        timeframe=timeframe,
        as_of=as_of,
        include_intrabar=False,
        created_at=clock,
    )
    _notify_activity(
        deps,
        ActivityStage.ANALYSTS,
        instrument=instrument,
        timeframe=timeframe,
        correlation_id=corr,
    )
    analyst_evidence = await run_analysts(
        snapshot,
        validation.candles,
        evidence_time=clock,
        concurrency=deps.analyst_concurrency,
        timeout_seconds=2.0,
    )
    _notify_activity(
        deps,
        ActivityStage.EVIDENCE,
        instrument=instrument,
        timeframe=timeframe,
        correlation_id=corr,
    )
    package = build_evidence_package(
        snapshot,
        analyst_evidence,
        correlation_id=corr,
        created_at=clock,
    )
    _notify_activity(
        deps,
        ActivityStage.JEV,
        instrument=instrument,
        timeframe=timeframe,
        correlation_id=corr,
    )
    jev_result = deps.jev_port.evaluate(package)
    _notify_activity(
        deps,
        ActivityStage.SUPERVISOR,
        instrument=instrument,
        timeframe=timeframe,
        correlation_id=corr,
    )
    proposal = supervise(
        package,
        jev_result,
        deps.llm_port,
        budgets=deps.supervisor_budgets,
        strategy_id=deps.strategy_id,
        strategy_version=deps.strategy_version,
        clock=lambda: clock,
    )
    mark = validation.candles[-1].close
    _notify_activity(
        deps,
        ActivityStage.RISK,
        instrument=instrument,
        timeframe=timeframe,
        correlation_id=corr,
        detail=proposal.action.value,
    )
    context = build_paper_risk_context(
        now=clock,
        validation=validation,
        ledger=deps.paper_broker.ledger,
        instrument=instrument,
        proposal=proposal,
        mark_price=mark,
        equity_tracker=deps.equity_tracker,
        proposal_history=deps.proposal_history,
        daily_loss_limit=_daily_loss_limit(deps.risk_policy),
        slippage_model_bps=Decimal(str(deps.settings.paper_slippage_bps)),
        fee_estimate=Decimal(str(deps.settings.paper_fee_bps)),
    )
    decision = evaluate_risk(
        proposal,
        context,
        deps.settings,
        policy=deps.risk_policy,
    )
    deps.proposal_history.record(proposal.proposal_id)

    order: Order | None = None
    fill_blocked: str | None = None
    if may_submit_to_order_manager(decision, now=clock):
        _notify_activity(
            deps,
            ActivityStage.PAPER,
            instrument=instrument,
            timeframe=timeframe,
            correlation_id=corr,
        )
        try:
            intent = intent_from_approved(decision, proposal, now=clock)
            order = deps.paper_broker.submit_from_risk_decision(
                decision,
                intent,
                mid_price=mark,
                now=clock,
            )
            # Refresh equity after fill so peak/day start stay current.
            deps.equity_tracker.observe(
                compute_paper_equity(
                    deps.paper_broker.ledger,
                    marks={instrument_mark_key(instrument): mark},
                ),
                now=clock,
                daily_loss_limit=_daily_loss_limit(deps.risk_policy),
            )
        except (IntentBuildError, PaperBrokerError, RiskHandoffDenied) as exc:
            # Fail closed: no paper fill. Decision remains for audit.
            order = None
            fill_blocked = f"paper_fill_blocked:{type(exc).__name__}"

    return PaperCycleResult(
        correlation_id=corr,
        validation=validation,
        proposal=proposal,
        decision=decision,
        order=order,
        blocked_reason=fill_blocked,
    )
