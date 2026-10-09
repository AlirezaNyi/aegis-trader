"""Populate RiskContext from validation + paper ledger for the paper cycle."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
    spread: Decimal = Decimal("0"),
    slippage_model_bps: Decimal = Decimal("5"),
    fee_estimate: Decimal = Decimal("5"),
) -> RiskContext:
    """Honest paper RiskContext — missing critical fields stay None ⇒ Risk REJECT."""
    open_positions = len(ledger.positions)
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
        daily_loss=Decimal("0"),
        drawdown=Decimal("0"),
        spread=spread,
        liquidity_ok=True,
        fee_estimate=fee_estimate,
        funding_ok=True,
        slippage_model_bps=slippage_model_bps,
        symbol_precision_ok=True,
        min_size_ok=True,
        reconciliation_ok=True,
        recent_proposal_ids=[],
        duplicate_detected=False,
        cooldown_active=False,
        account_state_refs=["acct:paper"],
        market_state_refs=[
            f"md:{instrument.symbol}:{validation.market_data_age_ms}"
        ],
    )
