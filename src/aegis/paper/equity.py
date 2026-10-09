"""Paper equity / loss / drawdown tracking for honest RiskContext population."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from aegis.paper.ledger import PaperLedger
from aegis.schemas.market import InstrumentRef


def instrument_mark_key(instrument: InstrumentRef) -> str:
    return (
        f"{instrument.exchange}:{instrument.market_type.value}:{instrument.symbol}"
    )


def compute_paper_equity(
    ledger: PaperLedger,
    *,
    marks: dict[str, Decimal] | None = None,
) -> Decimal:
    """Cash plus marked positions. Missing marks fall back to entry_price then 0."""
    equity = ledger.get_balance()
    mark_map = marks or {}
    for key, pos in ledger.positions.items():
        if pos.quantity == 0:
            continue
        mark = mark_map.get(key)
        if mark is None:
            mark = pos.entry_price if pos.entry_price is not None else Decimal("0")
        equity += pos.quantity * mark
    return equity


@dataclass
class PaperEquitySnapshot:
    equity: Decimal
    daily_loss: Decimal
    drawdown: Decimal
    cooldown_active: bool


@dataclass
class PaperEquityTracker:
    """Tracks peak equity and UTC-day start equity for paper soak RiskContext."""

    initial_equity: Decimal | None = None
    peak_equity: Decimal = Decimal("0")
    day_start_equity: Decimal | None = None
    day_utc: date | None = None
    cooldown_active: bool = False

    def observe(
        self,
        equity: Decimal,
        *,
        now: datetime,
        daily_loss_limit: Decimal | None = None,
    ) -> PaperEquitySnapshot:
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        today = now.astimezone(UTC).date()

        if self.initial_equity is None:
            self.initial_equity = equity
            self.peak_equity = equity
            self.day_start_equity = equity
            self.day_utc = today
        elif self.day_utc != today:
            self.day_utc = today
            self.day_start_equity = equity
            # New UTC day clears cooldown from prior session loss.
            self.cooldown_active = False

        if equity > self.peak_equity:
            self.peak_equity = equity

        day_start = self.day_start_equity if self.day_start_equity is not None else equity
        daily_loss = day_start - equity
        if daily_loss < 0:
            daily_loss = Decimal("0")
        drawdown = self.peak_equity - equity
        if drawdown < 0:
            drawdown = Decimal("0")

        if daily_loss_limit is not None and daily_loss > daily_loss_limit:
            self.cooldown_active = True

        return PaperEquitySnapshot(
            equity=equity,
            daily_loss=daily_loss,
            drawdown=drawdown,
            cooldown_active=self.cooldown_active,
        )


@dataclass
class ProposalHistory:
    """Short in-memory proposal id window for duplicate detection."""

    max_len: int = 64
    _ids: deque[UUID] = field(default_factory=deque)

    @property
    def recent_ids(self) -> list[UUID]:
        return list(self._ids)

    def is_duplicate(self, proposal_id: UUID) -> bool:
        return proposal_id in self._ids

    def record(self, proposal_id: UUID) -> None:
        if proposal_id in self._ids:
            return
        self._ids.append(proposal_id)
        while len(self._ids) > self.max_len:
            self._ids.popleft()
