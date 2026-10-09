"""Evaluation inputs for the Risk Engine — missing critical fields ⇒ REJECT."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass
class RiskContext:
    now: datetime
    market_data_age_ms: int | None = None
    market_integrity_ok: bool | None = None
    account_available: bool | None = None
    balance_reconciled: bool | None = None
    open_positions_count: int | None = None
    pending_orders_count: int | None = None
    aggregate_notional: Decimal | None = None
    instrument_notional: Decimal | None = None
    proposed_notional: Decimal | None = None
    daily_loss: Decimal | None = None
    drawdown: Decimal | None = None
    spread: Decimal | None = None
    liquidity_ok: bool | None = None
    fee_estimate: Decimal | None = None
    funding_ok: bool | None = None
    slippage_model_bps: Decimal | None = None
    symbol_precision_ok: bool | None = None
    min_size_ok: bool | None = None
    reconciliation_ok: bool | None = None
    recent_proposal_ids: list[UUID] | None = None
    duplicate_detected: bool | None = None
    cooldown_active: bool | None = None
    account_state_refs: list[str] = field(default_factory=list)
    market_state_refs: list[str] = field(default_factory=list)
