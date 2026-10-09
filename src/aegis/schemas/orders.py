"""Order, fill, and position contracts. Paper and live ledgers are separate."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from aegis.schemas.common import ApiModel, LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef


class OrderStatus(StrEnum):
    CREATED = "created"
    SUBMIT_ATTEMPTED = "submit_attempted"
    PENDING_NEW = "pending_new"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    PENDING_CANCEL = "pending_cancel"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    MISSING = "missing"
    FAILED_TERMINAL = "failed_terminal"


class OrderIntent(ApiModel):
    intent_id: UUID
    proposal_id: UUID
    risk_decision_correlation_id: str
    client_order_id: str
    ledger_kind: LedgerKind
    instrument: InstrumentRef
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    time_in_force: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class Order(ApiModel):
    order_id: UUID
    intent_id: UUID
    client_order_id: str
    exchange_order_id: str | None = None
    ledger_kind: LedgerKind
    instrument: InstrumentRef
    status: OrderStatus
    side: str
    order_type: str
    quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    price: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class Fill(ApiModel):
    fill_id: UUID
    order_id: UUID
    ledger_kind: LedgerKind
    instrument: InstrumentRef
    quantity: Decimal
    price: Decimal
    fee: Decimal | None = None
    fee_asset: str | None = None
    exchanged_at: datetime
    received_at: datetime


class Position(ApiModel):
    position_id: UUID
    ledger_kind: LedgerKind
    instrument: InstrumentRef
    market_type: MarketType
    quantity: Decimal
    entry_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    updated_at: datetime
