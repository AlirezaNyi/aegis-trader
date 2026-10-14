"""Optional Postgres persistence for live orders (ledger_kind=live only)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis.db.models import OrderRow
from aegis.logging import get_logger
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import Order, OrderSide, OrderStatus

logger = get_logger("aegis.orders.persist")


class LiveLedgerKindError(RuntimeError):
    """Raised when a non-live order is offered to live persistence."""


class LiveOrderLoadError(RuntimeError):
    """One or more persisted live order rows could not be parsed (e.g. a legacy /
    unparseable ``side``). Never silently dropped: pass this to
    ``recover_open_intents(load_error=...)`` so new submits stay blocked.

    ``loaded`` holds the rows that did parse; ``failed_client_order_ids`` the rest
    (row id is used when the client_order_id itself is unavailable).
    """

    def __init__(
        self,
        message: str,
        *,
        loaded: dict[str, Order],
        failed_client_order_ids: list[str],
    ) -> None:
        super().__init__(message)
        self.loaded = loaded
        self.failed_client_order_ids = failed_client_order_ids


def _dec_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


class TestOrderPersistError(RuntimeError):
    """Raised when a ``TEST_ACCEPTED`` (Spot orderTest) order is offered to the live
    ledger. orderTest never creates an exchange order, so it must never appear in
    live persistence (it would look like a real live order after restart)."""

    __test__ = False  # not a pytest test class


def upsert_live_order(session: Session, order: Order) -> None:
    if order.ledger_kind != LedgerKind.LIVE:
        raise LiveLedgerKindError(
            f"live persist refuses ledger_kind={order.ledger_kind.value!r}"
        )
    if order.status == OrderStatus.TEST_ACCEPTED:
        raise TestOrderPersistError(
            "live persist refuses status='test_accepted' "
            f"client_order_id={order.client_order_id!r} (orderTest is not a live order)"
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
    row.side = order.side.value
    row.order_type = order.order_type
    row.quantity = str(order.quantity)
    row.filled_quantity = str(order.filled_quantity)
    row.price = _dec_str(order.price)
    row.created_at = order.created_at
    row.updated_at = order.updated_at


def load_live_orders(session: Session) -> dict[str, Order]:
    """Load every live order row. Raises :class:`LiveOrderLoadError` (after logging
    each bad row by id and error class only) if any row cannot be parsed."""
    rows = session.scalars(
        select(OrderRow).where(OrderRow.ledger_kind == LedgerKind.LIVE.value)
    ).all()
    out: dict[str, Order] = {}
    failed: list[str] = []
    for row in rows:
        try:
            order = _row_to_order(row)
        except (ValueError, TypeError, InvalidOperation) as exc:
            ident = str(getattr(row, "client_order_id", None) or getattr(row, "id", "?"))
            failed.append(ident)
            logger.error(
                "live_order_row_unparseable client_order_id=%s err=%s",
                ident,
                type(exc).__name__,
                extra={"event": "live_order_row_unparseable", "component": "orders_persist"},
            )
            continue
        out[order.client_order_id] = order
    if failed:
        raise LiveOrderLoadError(
            f"{len(failed)} persisted live order row(s) unparseable; "
            "new live submits must stay blocked",
            loaded=out,
            failed_client_order_ids=failed,
        )
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
        side=OrderSide.parse(row.side),
        order_type=row.order_type,
        quantity=Decimal(row.quantity),
        filled_quantity=Decimal(row.filled_quantity or "0"),
        price=Decimal(row.price) if row.price is not None else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
