"""Populate RiskContext from validation + paper ledger for the paper cycle."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from aegis.paper.equity import (
    PaperEquityTracker,
    ProposalHistory,
    compute_paper_equity,
    instrument_mark_key,
)
from aegis.paper.ledger import PaperLedger
from aegis.risk.context import RiskContext
from aegis.schemas.market import InstrumentRef
from aegis.schemas.proposal import TradeProposal
from aegis.validation.stage import ValidationResult


def build_paper_risk_context(
    *,
    now: datetime,
    validation: ValidationResult,
    ledger: PaperLedger,
    instrument: InstrumentRef,
    proposal: TradeProposal | None,
    proposed_notional: Decimal | None = None,
    mark_price: Decimal | None = None,
    equity_tracker: PaperEquityTracker | None = None,
    proposal_history: ProposalHistory | None = None,
    daily_loss_limit: Decimal | None = None,
    spread: Decimal = Decimal("0"),
    slippage_model_bps: Decimal = Decimal("5"),
    fee_estimate: Decimal = Decimal("5"),
) -> RiskContext:
    """Honest paper RiskContext — missing critical fields stay None ⇒ Risk REJECT.

    Equity / daily_loss / drawdown come from ``PaperEquityTracker`` when provided.
    Reconcile OK flags are paper-local simulation assumptions, not exchange inventory.
    """
    open_positions = len([p for p in ledger.positions.values() if p.quantity != 0])
    aggregate = Decimal("0")
    instrument_notional = Decimal("0")
    for pos in ledger.positions.values():
        entry = pos.entry_price or Decimal("0")
        pos_notional = abs(pos.quantity * entry)
        aggregate += pos_notional
        if (
            pos.instrument.exchange == instrument.exchange
            and pos.instrument.market_type == instrument.market_type
            and pos.instrument.symbol == instrument.symbol
        ):
            instrument_notional = pos_notional

    proposed: Decimal | None = proposed_notional
    if proposed is None and proposal is not None:
        raw = proposal.sizing.get("notional")
        if raw is not None:
            proposed = Decimal(str(raw))

    marks: dict[str, Decimal] = {}
    if mark_price is not None:
        marks[instrument_mark_key(instrument)] = mark_price
    equity = compute_paper_equity(ledger, marks=marks or None)

    tracker = equity_tracker or PaperEquityTracker()
    snap = tracker.observe(equity, now=now, daily_loss_limit=daily_loss_limit)

    history = proposal_history or ProposalHistory()
    recent: list[UUID] = history.recent_ids
    duplicate = False
    if proposal is not None:
        duplicate = history.is_duplicate(proposal.proposal_id)

    return RiskContext(
        now=now,
        market_data_age_ms=validation.market_data_age_ms,
        market_integrity_ok=validation.market_integrity_ok and validation.ok,
        account_available=True,
        balance_reconciled=True,
        open_positions_count=open_positions,
        pending_orders_count=0,
        aggregate_notional=aggregate,
        instrument_notional=instrument_notional,
        proposed_notional=proposed,
        daily_loss=snap.daily_loss,
        drawdown=snap.drawdown,
        spread=spread,
        liquidity_ok=True,
        fee_estimate=fee_estimate,
        funding_ok=True,
        slippage_model_bps=slippage_model_bps,
        symbol_precision_ok=True,
        min_size_ok=True,
        reconciliation_ok=True,
        recent_proposal_ids=recent,
        duplicate_detected=duplicate,
        cooldown_active=snap.cooldown_active,
        account_state_refs=["acct:paper"],
        market_state_refs=[
            f"md:{instrument.symbol}:{validation.market_data_age_ms}"
        ],
    )
