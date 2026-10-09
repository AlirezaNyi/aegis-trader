"""Paper cycle review journal — memory + optional Postgres (fail-open)."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from aegis.db.models import PaperCycleReviewRow
from aegis.db.session import create_engine_from_url
from aegis.logging import get_logger
from aegis.pipeline.signal_marks import (
    SignalMark,
    SignalMarkStatus,
    aggregate_signal_returns,
    direction_from_action,
    is_trade_signal_action,
    mark_signal_on_candles,
)
from aegis.schemas.market import Candle

logger = get_logger("aegis.pipeline.journal")

_SECRET_KEYS = frozenset({"api_key", "authorization", "secret", "password", "token"})
_MEMORY_CAP = 500


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                continue
            out[str(key)] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


@dataclass
class ReviewRecord:
    review_id: UUID
    correlation_id: str
    symbol: str
    timeframe: str
    final_open_time: datetime | None
    proposal_action: str | None
    proposal_direction: str | None
    risk_decision: str | None
    entry_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    mark_status: str | None
    return_pct: Decimal | None
    exit_price: Decimal | None
    paper_order_id: str | None
    summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    db_persisted: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "review_id": str(self.review_id),
            "correlation_id": self.correlation_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "final_open_time": (
                None if self.final_open_time is None else self.final_open_time.isoformat()
            ),
            "proposal_action": self.proposal_action,
            "proposal_direction": self.proposal_direction,
            "risk_decision": self.risk_decision,
            "entry_price": None if self.entry_price is None else str(self.entry_price),
            "stop_loss": None if self.stop_loss is None else str(self.stop_loss),
            "take_profit": None if self.take_profit is None else str(self.take_profit),
            "mark_status": self.mark_status,
            "return_pct": None if self.return_pct is None else str(self.return_pct),
            "exit_price": None if self.exit_price is None else str(self.exit_price),
            "paper_order_id": self.paper_order_id,
            "summary": self.summary,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "db_persisted": self.db_persisted,
            "is_trade_signal": is_trade_signal_action(self.proposal_action),
        }


@dataclass
class PaperCycleJournal:
    """Append-only review store. Postgres writes never abort the paper cycle."""

    database_url: str | None = None
    memory_cap: int = _MEMORY_CAP
    _memory: deque[ReviewRecord] = field(default_factory=deque, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)
    last_persist_error: str | None = field(default=None, init=False)
    _session_factory: sessionmaker[Session] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        url = self.database_url
        # Drop URL (may contain password) once the engine is bound.
        self.database_url = None
        if url:
            try:
                engine = create_engine_from_url(url)
                self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            except Exception as exc:  # noqa: BLE001 — fail open
                self.last_persist_error = f"engine_init:{type(exc).__name__}"
                logger.warning(
                    "paper_journal_engine_failed err=%s",
                    type(exc).__name__,
                    extra={"event": "paper_journal_engine_failed", "component": "journal"},
                )
                self._session_factory = None

    def append(self, record: ReviewRecord) -> ReviewRecord:
        with self._lock:
            self._memory.append(record)
            while len(self._memory) > self.memory_cap:
                self._memory.popleft()
        persisted = self._try_insert(record)
        record.db_persisted = persisted
        return record

    def update_mark(self, review_id: UUID, mark: SignalMark) -> ReviewRecord | None:
        with self._lock:
            target: ReviewRecord | None = None
            for row in self._memory:
                if row.review_id == review_id:
                    target = row
                    break
            if target is None:
                return None
            if target.mark_status in {
                SignalMarkStatus.STOPPED.value,
                SignalMarkStatus.TARGET.value,
            }:
                return target
            now = datetime.now(tz=UTC)
            target.mark_status = mark.status.value
            target.return_pct = mark.return_pct
            target.exit_price = mark.exit_price
            target.updated_at = now
        self._try_update_mark(review_id, mark, now=now)
        return target

    def open_trade_signals(self, *, symbol: str | None = None) -> list[ReviewRecord]:
        with self._lock:
            rows = [
                r
                for r in self._memory
                if is_trade_signal_action(r.proposal_action)
                and r.mark_status == SignalMarkStatus.OPEN.value
                and r.entry_price is not None
                and (symbol is None or r.symbol == symbol)
            ]
            return list(rows)

    def list_reviews(self, *, limit: int = 50) -> list[ReviewRecord]:
        with self._lock:
            items = list(self._memory)
        items.reverse()
        return items[: max(0, limit)]

    def trade_signal_rows(self) -> list[ReviewRecord]:
        with self._lock:
            return [
                r
                for r in self._memory
                if is_trade_signal_action(r.proposal_action) and r.return_pct is not None
            ]

    def signal_aggregate(self) -> dict[str, Any]:
        rows = self.trade_signal_rows()
        if not rows:
            return {
                "count": 0,
                "wins": 0,
                "losses": 0,
                "open": 0,
                "mean_return_pct": "0",
                "sum_return_pct": "0",
                "disclaimer": (
                    "جمع درصدها بازدهٔ یک سبد نیست؛ سایز سرمایه ساخته نمی‌شود. "
                    "Sum of percents is not a portfolio return."
                ),
            }
        agg = aggregate_signal_returns(
            [r.return_pct for r in rows if r.return_pct is not None],
            [r.mark_status or SignalMarkStatus.OPEN.value for r in rows],
        )
        return {
            "count": int(agg["count"]),
            "wins": int(agg["wins"]),
            "losses": int(agg["losses"]),
            "open": int(agg["open"]),
            "mean_return_pct": str(agg["mean_return_pct"]),
            "sum_return_pct": str(agg["sum_return_pct"]),
            "disclaimer": (
                "جمع درصدها بازدهٔ یک سبد نیست؛ سایز سرمایه ساخته نمی‌شود. "
                "Sum of percents is not a portfolio return."
            ),
        }

    def mark_open_with_candles(
        self,
        *,
        symbol: str,
        candles: Sequence[Candle],
    ) -> int:
        """Refresh open BUY/SELL marks using fetched candles. Returns updates applied."""
        updated = 0
        finals = sorted(
            [c for c in candles if c.is_final],
            key=lambda c: c.open_time,
        )
        for record in self.open_trade_signals(symbol=symbol):
            if record.entry_price is None or record.final_open_time is None:
                continue
            direction = direction_from_action(record.proposal_action or "")
            if direction is None:
                continue
            after = [c for c in finals if c.open_time > record.final_open_time]
            if not after:
                continue
            mark = mark_signal_on_candles(
                entry_price=record.entry_price,
                direction=direction,
                stop_loss=record.stop_loss,
                take_profit=record.take_profit,
                candles_after_entry=after,
            )
            if self.update_mark(record.review_id, mark) is not None:
                updated += 1
        return updated

    def _try_insert(self, record: ReviewRecord) -> bool:
        factory = self._session_factory
        if factory is None:
            return False
        try:
            with factory() as session:
                session.add(
                    PaperCycleReviewRow(
                        id=record.review_id,
                        correlation_id=record.correlation_id,
                        symbol=record.symbol,
                        timeframe=record.timeframe,
                        final_open_time=record.final_open_time,
                        proposal_action=record.proposal_action,
                        proposal_direction=record.proposal_direction,
                        risk_decision=record.risk_decision,
                        entry_price=(
                            None if record.entry_price is None else str(record.entry_price)
                        ),
                        stop_loss=(
                            None if record.stop_loss is None else str(record.stop_loss)
                        ),
                        take_profit=(
                            None if record.take_profit is None else str(record.take_profit)
                        ),
                        mark_status=record.mark_status,
                        return_pct=(
                            None if record.return_pct is None else str(record.return_pct)
                        ),
                        exit_price=(
                            None if record.exit_price is None else str(record.exit_price)
                        ),
                        paper_order_id=record.paper_order_id,
                        summary=record.summary,
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                    )
                )
                session.commit()
            self.last_persist_error = None
            return True
        except Exception as exc:  # noqa: BLE001 — fail open
            self.last_persist_error = f"insert:{type(exc).__name__}"
            logger.warning(
                "paper_journal_insert_failed err=%s",
                type(exc).__name__,
                extra={"event": "paper_journal_insert_failed", "component": "journal"},
            )
            return False

    def _try_update_mark(
        self,
        review_id: UUID,
        mark: SignalMark,
        *,
        now: datetime,
    ) -> bool:
        factory = self._session_factory
        if factory is None:
            return False
        try:
            with factory() as session:
                row = session.get(PaperCycleReviewRow, review_id)
                if row is None:
                    return False
                if row.mark_status in {
                    SignalMarkStatus.STOPPED.value,
                    SignalMarkStatus.TARGET.value,
                }:
                    return True
                row.mark_status = mark.status.value
                row.return_pct = str(mark.return_pct)
                row.exit_price = str(mark.exit_price)
                row.updated_at = now
                session.commit()
            self.last_persist_error = None
            return True
        except Exception as exc:  # noqa: BLE001 — fail open
            self.last_persist_error = f"update:{type(exc).__name__}"
            logger.warning(
                "paper_journal_update_failed err=%s",
                type(exc).__name__,
                extra={"event": "paper_journal_update_failed", "component": "journal"},
            )
            return False


def build_review_record(
    *,
    correlation_id: str,
    symbol: str,
    timeframe: str,
    final_open_time: datetime | None,
    summary: dict[str, Any],
    proposal_action: str | None,
    proposal_direction: str | None,
    risk_decision: str | None,
    entry_price: Decimal | None,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    paper_order_id: str | None,
) -> ReviewRecord:
    now = datetime.now(tz=UTC)
    mark_status: str | None = None
    return_pct: Decimal | None = None
    exit_price: Decimal | None = None
    if is_trade_signal_action(proposal_action) and entry_price is not None:
        mark_status = SignalMarkStatus.OPEN.value
        return_pct = Decimal("0")
        exit_price = entry_price
    return ReviewRecord(
        review_id=uuid4(),
        correlation_id=correlation_id,
        symbol=symbol,
        timeframe=timeframe,
        final_open_time=final_open_time,
        proposal_action=proposal_action,
        proposal_direction=proposal_direction,
        risk_decision=risk_decision,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        mark_status=mark_status,
        return_pct=return_pct,
        exit_price=exit_price,
        paper_order_id=paper_order_id,
        summary=redact_secrets(summary),
        created_at=now,
        updated_at=now,
    )
