"""In-memory paper ledger — ledger_kind=paper only."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from aegis.paper.exceptions import PaperBrokerError, PaperLedgerKindError
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import Fill, Order, Position


def _instrument_key(exchange: str, market_type: str, symbol: str) -> str:
    return f"{exchange}:{market_type}:{symbol}"


@dataclass
class PaperLedger:
    """Simulated orders, fills, positions, and cash balances (paper only)."""

    quote_asset: str = "USDT"
    balances: dict[str, Decimal] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    positions: dict[str, Position] = field(default_factory=dict)
    intent_ids: dict[str, UUID] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quote_asset not in self.balances:
            self.balances[self.quote_asset] = Decimal("0")

    def ensure_paper(self, ledger_kind: LedgerKind) -> None:
        if ledger_kind != LedgerKind.PAPER:
            raise PaperLedgerKindError(
                f"paper ledger refuses ledger_kind={ledger_kind.value!r}"
            )

    def get_balance(self, asset: str | None = None) -> Decimal:
        key = asset or self.quote_asset
        return Decimal(self.balances.get(key, Decimal("0")))

    def set_balance(self, amount: Decimal, asset: str | None = None) -> None:
        key = asset or self.quote_asset
        self.balances[key] = Decimal(amount)

    def position_for(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
    ) -> Position | None:
        return self.positions.get(_instrument_key(exchange, market_type, symbol))

    def upsert_position(self, position: Position) -> None:
        self.ensure_paper(position.ledger_kind)
        key = _instrument_key(
            position.instrument.exchange,
            position.instrument.market_type.value,
            position.instrument.symbol,
        )
        self.positions[key] = position

    def record_order(self, order: Order) -> None:
        self.ensure_paper(order.ledger_kind)
        self.orders[order.client_order_id] = order
        self.intent_ids[order.client_order_id] = order.intent_id

    def record_fill(self, fill: Fill) -> None:
        self.ensure_paper(fill.ledger_kind)
        self.fills.append(fill)

    def checksum(self) -> str:
        """Deterministic checksum for restart reconcile (balances + positions)."""
        bal_parts = [
            f"{asset}:{self.balances[asset]}"
            for asset in sorted(self.balances)
        ]
        pos_parts: list[str] = []
        for key in sorted(self.positions):
            pos = self.positions[key]
            entry = pos.entry_price if pos.entry_price is not None else ""
            pos_parts.append(f"{key}:{pos.quantity}:{entry}")
        return "|".join(bal_parts + pos_parts)

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "quote_asset": self.quote_asset,
            "balances": {k: str(v) for k, v in self.balances.items()},
            "orders": {k: v.model_dump(mode="json") for k, v in self.orders.items()},
            "fills": [f.model_dump(mode="json") for f in self.fills],
            "positions": {k: v.model_dump(mode="json") for k, v in self.positions.items()},
            "intent_ids": {k: str(v) for k, v in self.intent_ids.items()},
            "checksum": self.checksum(),
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> PaperLedger:
        ledger = cls(quote_asset=str(snapshot.get("quote_asset", "USDT")))
        balances_raw = snapshot.get("balances", {})
        ledger.balances = {str(k): Decimal(str(v)) for k, v in balances_raw.items()}
        for cid, raw in snapshot.get("orders", {}).items():
            order = Order.model_validate(raw)
            ledger.ensure_paper(order.ledger_kind)
            ledger.orders[str(cid)] = order
        for raw in snapshot.get("fills", []):
            fill = Fill.model_validate(raw)
            ledger.ensure_paper(fill.ledger_kind)
            ledger.fills.append(fill)
        for key, raw in snapshot.get("positions", {}).items():
            pos = Position.model_validate(raw)
            ledger.ensure_paper(pos.ledger_kind)
            ledger.positions[str(key)] = pos
        for cid, iid in snapshot.get("intent_ids", {}).items():
            ledger.intent_ids[str(cid)] = UUID(str(iid))
        return ledger

    def clone(self) -> PaperLedger:
        return PaperLedger.from_snapshot(deepcopy(self.to_snapshot()))


def apply_cash_and_position(
    ledger: PaperLedger,
    *,
    side: str,
    quantity: Decimal,
    price: Decimal,
    fee: Decimal,
    instrument_exchange: str,
    instrument_market_type: str,
    instrument_symbol: str,
    market_type_enum: MarketType,
    instrument_ref: InstrumentRef,
    now: datetime,
    position_id: UUID,
) -> Position:
    """Update quote cash and spot-style position after a full fill."""
    side_u = side.strip().upper()
    notional = quantity * price
    quote = ledger.quote_asset
    cash = ledger.get_balance(quote)

    key = _instrument_key(instrument_exchange, instrument_market_type, instrument_symbol)
    existing = ledger.positions.get(key)
    prev_qty = existing.quantity if existing else Decimal("0")
    prev_entry = existing.entry_price if existing and existing.entry_price is not None else None

    if side_u == "BUY":
        cash = cash - notional - fee
        new_qty = prev_qty + quantity
        if prev_qty <= 0 or prev_entry is None:
            new_entry: Decimal | None = price
        else:
            new_entry = ((prev_entry * prev_qty) + (price * quantity)) / new_qty
    elif side_u == "SELL":
        cash = cash + notional - fee
        new_qty = prev_qty - quantity
        if new_qty == 0:
            new_entry = None
        else:
            new_entry = prev_entry
    else:
        raise PaperBrokerError(f"unsupported side={side!r}")

    ledger.set_balance(cash, quote)
    position = Position(
        position_id=existing.position_id if existing else position_id,
        ledger_kind=LedgerKind.PAPER,
        instrument=instrument_ref,
        market_type=market_type_enum,
        quantity=new_qty,
        entry_price=new_entry,
        unrealized_pnl=None,
        updated_at=now,
    )
    ledger.upsert_position(position)
    return position
