"""Postgres persistence for paper ledger rows (ledger_kind='paper' only)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis.db.models import FillRow, OrderRow, PaperBalanceRow, PositionRow
from aegis.paper.exceptions import PaperLedgerKindError
from aegis.paper.ledger import PaperLedger, _instrument_key
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import Fill, Order, OrderStatus, Position


def _dec_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def persist_paper_ledger(session: Session, ledger: PaperLedger) -> None:
    """Upsert all paper orders/fills/positions/balances. Refuses any LIVE rows."""
    for order in ledger.orders.values():
        if order.ledger_kind != LedgerKind.PAPER:
            raise PaperLedgerKindError("refuse persisting non-paper order")
        _upsert_order(session, order)
    for fill in ledger.fills:
        if fill.ledger_kind != LedgerKind.PAPER:
            raise PaperLedgerKindError("refuse persisting non-paper fill")
        _upsert_fill(session, fill)
    for position in ledger.positions.values():
        if position.ledger_kind != LedgerKind.PAPER:
            raise PaperLedgerKindError("refuse persisting non-paper position")
        _upsert_position(session, position)
    for asset, balance in ledger.balances.items():
        _upsert_balance(session, asset, balance)


def load_paper_ledger(session: Session, *, quote_asset: str = "USDT") -> PaperLedger:
    """Load paper rows into an in-memory ledger."""
    ledger = PaperLedger(quote_asset=quote_asset)

    for bal_row in session.scalars(
        select(PaperBalanceRow).where(PaperBalanceRow.ledger_kind == LedgerKind.PAPER.value)
    ):
        ledger.balances[bal_row.asset] = Decimal(bal_row.balance)

    for order_row in session.scalars(
        select(OrderRow).where(OrderRow.ledger_kind == LedgerKind.PAPER.value)
    ):
        order = _order_from_row(order_row)
        ledger.orders[order.client_order_id] = order
        ledger.intent_ids[order.client_order_id] = order.intent_id

    for fill_row in session.scalars(
        select(FillRow).where(FillRow.ledger_kind == LedgerKind.PAPER.value)
    ):
        ledger.fills.append(_fill_from_row(fill_row))

    for pos_row in session.scalars(
        select(PositionRow).where(PositionRow.ledger_kind == LedgerKind.PAPER.value)
    ):
        pos = _position_from_row(pos_row)
        key = _instrument_key(
            pos.instrument.exchange,
            pos.instrument.market_type.value,
            pos.instrument.symbol,
        )
        ledger.positions[key] = pos

    return ledger


def _upsert_order(session: Session, order: Order) -> None:
    row = session.scalars(
        select(OrderRow).where(
            OrderRow.client_order_id == order.client_order_id,
            OrderRow.ledger_kind == LedgerKind.PAPER.value,
        )
    ).one_or_none()
    if row is None:
        row = OrderRow(id=order.order_id)
        session.add(row)
    row.intent_id = order.intent_id
    row.client_order_id = order.client_order_id
    row.exchange_order_id = order.exchange_order_id
    row.ledger_kind = LedgerKind.PAPER.value
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


def _upsert_fill(session: Session, fill: Fill) -> None:
    existing = session.scalars(select(FillRow).where(FillRow.id == fill.fill_id)).one_or_none()
    if existing is not None:
        return
    session.add(
        FillRow(
            id=fill.fill_id,
            order_id=fill.order_id,
            ledger_kind=LedgerKind.PAPER.value,
            exchange=fill.instrument.exchange,
            market_type=fill.instrument.market_type.value,
            symbol=fill.instrument.symbol,
            quantity=str(fill.quantity),
            price=str(fill.price),
            fee=_dec_str(fill.fee),
            fee_asset=fill.fee_asset,
            exchanged_at=fill.exchanged_at,
            received_at=fill.received_at,
        )
    )


def _upsert_position(session: Session, position: Position) -> None:
    row = session.scalars(
        select(PositionRow).where(
            PositionRow.ledger_kind == LedgerKind.PAPER.value,
            PositionRow.exchange == position.instrument.exchange,
            PositionRow.market_type == position.instrument.market_type.value,
            PositionRow.symbol == position.instrument.symbol,
        )
    ).one_or_none()
    if row is None:
        row = PositionRow(id=position.position_id)
        session.add(row)
    row.ledger_kind = LedgerKind.PAPER.value
    row.exchange = position.instrument.exchange
    row.market_type = position.instrument.market_type.value
    row.symbol = position.instrument.symbol
    row.quantity = str(position.quantity)
    row.entry_price = _dec_str(position.entry_price)
    row.unrealized_pnl = _dec_str(position.unrealized_pnl)
    row.updated_at = position.updated_at


def _upsert_balance(session: Session, asset: str, balance: Decimal) -> None:
    row = session.scalars(
        select(PaperBalanceRow).where(
            PaperBalanceRow.ledger_kind == LedgerKind.PAPER.value,
            PaperBalanceRow.asset == asset,
        )
    ).one_or_none()
    if row is None:
        row = PaperBalanceRow(id=uuid4())
        session.add(row)
    row.ledger_kind = LedgerKind.PAPER.value
    row.asset = asset
    row.balance = str(balance)
    row.updated_at = datetime.now(tz=UTC)


def _order_from_row(row: OrderRow) -> Order:
    return Order(
        order_id=row.id if isinstance(row.id, UUID) else UUID(str(row.id)),
        intent_id=row.intent_id if isinstance(row.intent_id, UUID) else UUID(str(row.intent_id)),
        client_order_id=row.client_order_id,
        exchange_order_id=row.exchange_order_id,
        ledger_kind=LedgerKind.PAPER,
        instrument=InstrumentRef(
            exchange=row.exchange,
            market_type=MarketType(row.market_type),
            symbol=row.symbol,
        ),
        status=OrderStatus(row.status),
        side=row.side,
        order_type=row.order_type,
        quantity=Decimal(row.quantity),
        filled_quantity=Decimal(row.filled_quantity),
        price=Decimal(row.price) if row.price is not None else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _fill_from_row(row: FillRow) -> Fill:
    return Fill(
        fill_id=row.id if isinstance(row.id, UUID) else UUID(str(row.id)),
        order_id=row.order_id if isinstance(row.order_id, UUID) else UUID(str(row.order_id)),
        ledger_kind=LedgerKind.PAPER,
        instrument=InstrumentRef(
            exchange=row.exchange,
            market_type=MarketType(row.market_type),
            symbol=row.symbol,
        ),
        quantity=Decimal(row.quantity),
        price=Decimal(row.price),
        fee=Decimal(row.fee) if row.fee is not None else None,
        fee_asset=row.fee_asset,
        exchanged_at=row.exchanged_at,
        received_at=row.received_at,
    )


def _position_from_row(row: PositionRow) -> Position:
    return Position(
        position_id=row.id if isinstance(row.id, UUID) else UUID(str(row.id)),
        ledger_kind=LedgerKind.PAPER,
        instrument=InstrumentRef(
            exchange=row.exchange,
            market_type=MarketType(row.market_type),
            symbol=row.symbol,
        ),
        market_type=MarketType(row.market_type),
        quantity=Decimal(row.quantity),
        entry_price=Decimal(row.entry_price) if row.entry_price is not None else None,
        unrealized_pnl=(
            Decimal(row.unrealized_pnl) if row.unrealized_pnl is not None else None
        ),
        updated_at=row.updated_at,
    )
