"""Postgres store for finalized public candles (fail-open, idempotent).

Writes and reads never raise into callers: when no database URL is configured or
Postgres is unreachable the store reports `enabled=False` / returns empty results
and records `last_error` (exception class name only, never the URL).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from aegis.db.models import MarketCandleRow
from aegis.db.session import create_engine_from_url
from aegis.logging import get_logger
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef

logger = get_logger("aegis.market_data.candle_store")

UNIQUE_CONSTRAINT_NAME = "uq_market_candles_key"


def candle_to_row_values(candle: Candle) -> dict[str, Any]:
    """Column values for `market_candles` (Decimals stay Decimal).

    `id` is generated explicitly: the table has no server-side default and
    multi-row Core inserts should not rely on ORM-side defaults.
    """
    return {
        "id": uuid4(),
        "exchange": candle.instrument.exchange,
        "market_type": candle.instrument.market_type.value,
        "symbol": candle.instrument.symbol,
        "interval": candle.interval.value,
        "open_time": candle.open_time,
        "close_time": candle.close_time,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "quote_volume": candle.quote_volume,
        "trade_count": candle.trade_count,
        "source": candle.source,
        "received_at": candle.received_at,
    }


def final_row_values(candles: Iterable[Candle]) -> list[dict[str, Any]]:
    """Keep only finalized bars, de-duplicated on the natural key (first wins)."""
    seen: set[tuple[str, str, str, str, datetime]] = set()
    out: list[dict[str, Any]] = []
    for candle in candles:
        if not candle.is_final:
            continue
        values = candle_to_row_values(candle)
        key = (
            values["exchange"],
            values["market_type"],
            values["symbol"],
            values["interval"],
            values["open_time"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(values)
    return out


def build_upsert_statement(rows: list[dict[str, Any]]) -> PgInsert:
    """INSERT ... ON CONFLICT (natural key) DO NOTHING — never overwrites history."""
    return (
        pg_insert(MarketCandleRow)
        .values(rows)
        .on_conflict_do_nothing(constraint=UNIQUE_CONSTRAINT_NAME)
    )


def row_to_candle(row: MarketCandleRow) -> Candle:
    return Candle(
        instrument=InstrumentRef(
            exchange=row.exchange,
            market_type=MarketType(row.market_type),
            symbol=row.symbol,
        ),
        interval=Timeframe(row.interval),
        open_time=row.open_time,
        close_time=row.close_time,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        quote_volume=row.quote_volume,
        trade_count=row.trade_count,
        is_final=True,
        source=row.source,
        received_at=row.received_at,
    )


class CandleStore:
    """Persist finalized candles to Postgres. Never aborts the caller."""

    def __init__(self, database_url: str | None) -> None:
        self._lock = Lock()
        self.last_error: str | None = None
        self._session_factory: sessionmaker[Session] | None = None
        # The URL may contain a password; it is not retained after engine bind.
        if database_url:
            try:
                engine = create_engine_from_url(database_url)
                self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            except Exception as exc:  # noqa: BLE001 — fail open
                self._record_error("engine_init", exc)
                self._session_factory = None

    @property
    def enabled(self) -> bool:
        return self._session_factory is not None

    def _record_error(self, op: str, exc: BaseException) -> None:
        self.last_error = f"{op}:{type(exc).__name__}"
        logger.warning(
            "candle_store_%s_failed err=%s",
            op,
            type(exc).__name__,
            extra={"event": f"candle_store_{op}_failed", "component": "candle_store"},
        )

    def upsert_final(self, candles: Iterable[Candle]) -> int:
        """Insert finalized bars; existing keys are left untouched.

        Returns the number of newly inserted rows (0 when disabled or on error).
        """
        rows = final_row_values(candles)
        if not rows or self._session_factory is None:
            return 0
        try:
            with self._lock, self._session_factory() as session:
                # RETURNING counts only rows actually inserted; rowcount is
                # unreliable for multi-row ON CONFLICT DO NOTHING inserts.
                result = session.execute(
                    build_upsert_statement(rows).returning(MarketCandleRow.id)
                )
                inserted = len(result.fetchall())
                session.commit()
                return inserted
        except Exception as exc:  # noqa: BLE001 — fail open
            self._record_error("upsert", exc)
            return 0

    def load_range(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """Finalized candles with `start <= open_time < end`, ordered by open_time."""
        if self._session_factory is None or end <= start:
            return []
        stmt = (
            select(MarketCandleRow)
            .where(
                MarketCandleRow.exchange == instrument.exchange,
                MarketCandleRow.market_type == instrument.market_type.value,
                MarketCandleRow.symbol == instrument.symbol,
                MarketCandleRow.interval == interval.value,
                MarketCandleRow.open_time >= start,
                MarketCandleRow.open_time < end,
            )
            .order_by(MarketCandleRow.open_time.asc())
        )
        try:
            with self._session_factory() as session:
                rows = list(session.scalars(stmt))
            return [row_to_candle(row) for row in rows]
        except Exception as exc:  # noqa: BLE001 — fail open
            self._record_error("load", exc)
            return []
