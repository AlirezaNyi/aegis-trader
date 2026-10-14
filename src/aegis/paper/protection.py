"""Paper protective levels (stop-loss / take-profit) and closed-trade records.

Levels come only from a Risk-APPROVED decision's ``validated_order_params`` and are
recorded when a paper BUY entry fills. They are simulation state for the paper /
backtest ledger only (``ledger_kind=paper``); nothing here talks to an exchange.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from aegis.paper.exceptions import PaperFillError


class ExitReason(StrEnum):
    """Why a paper position was reduced / closed."""

    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    # Risk-approved SELL decision (Supervisor/Risk path), not a protective exit.
    SIGNAL = "signal"


def parse_protective_level(raw: Any, *, name: str) -> Decimal | None:
    """Parse an approved stop/target. Malformed values raise (fail closed, no fill)."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise PaperFillError(f"{name} must be numeric, got bool")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PaperFillError(f"{name} is not numeric ({raw!r})") from exc
    if not value.is_finite() or value <= 0:
        raise PaperFillError(f"{name} must be a positive finite number ({raw!r})")
    return value


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _dec(raw: Any) -> Decimal:
    value = Decimal(str(raw))
    if not value.is_finite():
        raise ValueError(f"non-finite decimal {raw!r}")
    return value


def _opt_dec(raw: Any) -> Decimal | None:
    return None if raw is None else _dec(raw)


def _opt_dt(raw: Any) -> datetime | None:
    return None if raw is None else _aware(datetime.fromisoformat(str(raw)))


@dataclass
class ProtectiveLevels:
    """Open long position's protective levels and entry bookkeeping (paper only).

    ``entry_price`` is the position's average entry after the latest BUY fill and
    ``entry_fees`` the entry fees attributable to ``quantity``. ``opened_at`` is the
    latest entry fill clock; only candles with ``close_time > opened_at`` are ever
    checked for exits. ``last_checked_open_time`` makes exit scanning idempotent.
    """

    stop_loss: Decimal | None
    take_profit: Decimal | None
    entry_price: Decimal
    quantity: Decimal
    entry_fees: Decimal
    opened_at: datetime
    entry_client_order_id: str
    last_checked_open_time: datetime | None = None

    def initial_risk(self, quantity: Decimal | None = None) -> Decimal | None:
        """``(entry - stop) * qty``; None when no stop or the stop is not below entry."""
        qty = self.quantity if quantity is None else quantity
        if self.stop_loss is None or qty <= 0:
            return None
        per_unit = self.entry_price - self.stop_loss
        if per_unit <= 0:
            return None
        return per_unit * qty

    def to_json(self) -> dict[str, Any]:
        return {
            "stop_loss": None if self.stop_loss is None else str(self.stop_loss),
            "take_profit": None if self.take_profit is None else str(self.take_profit),
            "entry_price": str(self.entry_price),
            "quantity": str(self.quantity),
            "entry_fees": str(self.entry_fees),
            "opened_at": _aware(self.opened_at).isoformat(),
            "entry_client_order_id": self.entry_client_order_id,
            "last_checked_open_time": (
                None
                if self.last_checked_open_time is None
                else _aware(self.last_checked_open_time).isoformat()
            ),
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ProtectiveLevels:
        opened = _opt_dt(raw["opened_at"])
        if opened is None:
            raise ValueError("opened_at is required")
        return cls(
            stop_loss=_opt_dec(raw.get("stop_loss")),
            take_profit=_opt_dec(raw.get("take_profit")),
            entry_price=_dec(raw["entry_price"]),
            quantity=_dec(raw["quantity"]),
            entry_fees=_dec(raw.get("entry_fees", "0")),
            opened_at=opened,
            entry_client_order_id=str(raw["entry_client_order_id"]),
            last_checked_open_time=_opt_dt(raw.get("last_checked_open_time")),
        )


@dataclass(frozen=True)
class ClosedTrade:
    """Realized result of reducing / closing a paper long position.

    ``realized_pnl = (exit - entry) * qty - entry_fees - exit_fee`` (net of both
    fee legs; slippage is already inside the fill prices). ``r_multiple`` is
    ``realized_pnl / initial_risk`` with ``initial_risk = (entry - stop) * qty``;
    None when no stop was recorded or the stop was not below entry.
    """

    instrument_key: str
    reason: ExitReason
    close_client_order_id: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    entry_fees: Decimal
    exit_fee: Decimal
    realized_pnl: Decimal
    initial_risk: Decimal | None
    r_multiple: Decimal | None
    opened_at: datetime | None
    closed_at: datetime
