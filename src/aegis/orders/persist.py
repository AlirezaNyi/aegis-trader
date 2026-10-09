"""Optional Postgres persistence for live orders (ledger_kind=live only)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis.db.models import OrderRow
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import Order, OrderStatus


class LiveLedgerKindError(RuntimeError):
    """Raised when a non-live order is offered to live persistence."""


def _dec_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def upsert_live_order(session: Session, order: Order) -> None:
    if order.ledger_kind != LedgerKind.LIVE:
        raise LiveLedgerKindError(
            f"live persist refuses ledger_kind={order.ledger_kind.value!r}"
        )
    row = session.scalars(
        select(OrderRow).where(
            OrderRow.client_order_id == order.client_order_id,
            OrderRow.ledger_kind == LedgerKind.LIVE.value,
        )
    ).one_or_none()
    if row is None:
        row = OrderRow(id=order.order_id)
        session.add(row)
    row.intent_id = order.intent_id
    row.client_order_id = order.client_order_id
    row.exchange_order_id = order.exchange_order_id
    row.ledger_kind = LedgerKind.LIVE.value
    row.exchange = order.instrument.exchange
    row.market_type = order.instrument.market_type.value
    row.symbol = order.instrument.symbol
    row.status = order.status.value
    row.side = order.side
    row.order_type = order.order_type
    row.quantity = str(order.quantity)
    row.filled_quantity = str(order.filled_quantity)
    row.price = _dec_str(order.price)
    row.created_at = order.created_at
    row.updated_at = order.updated_at


def load_live_orders(session: Session) -> dict[str, Order]:
    rows = session.scalars(
        select(OrderRow).where(OrderRow.ledger_kind == LedgerKind.LIVE.value)
    ).all()
    out: dict[str, Order] = {}
    for row in rows:
        order = _row_to_order(row)
        out[order.client_order_id] = order
    return out


def _row_to_order(row: OrderRow) -> Order:
    return Order(
        order_id=row.id if isinstance(row.id, UUID) else UUID(str(row.id)),
        intent_id=(
            row.intent_id if isinstance(row.intent_id, UUID) else UUID(str(row.intent_id))
        ),
        client_order_id=row.client_order_id,
        exchange_order_id=row.exchange_order_id,
        ledger_kind=LedgerKind.LIVE,
        instrument=InstrumentRef(
            exchange=row.exchange,
            market_type=MarketType(row.market_type),
            symbol=row.symbol,
        ),
        status=OrderStatus(row.status),
        side=row.side,
        order_type=row.order_type,
        quantity=Decimal(row.quantity),
        filled_quantity=Decimal(row.filled_quantity or "0"),
        price=Decimal(row.price) if row.price is not None else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
