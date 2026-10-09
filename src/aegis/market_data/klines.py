"""Normalize Toobit REST kline arrays into Candle models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from aegis.market_data.timeutil import interval_to_milliseconds, ms_to_datetime
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef


class MalformedKlineError(ValueError):
    """Raised when a kline row cannot be parsed safely."""


def parse_rest_kline_row(
    row: list[Any],
    *,
    instrument: InstrumentRef,
    interval: Timeframe,
    received_at: datetime,
    now: datetime,
    source: str = "rest",
) -> Candle:
    if not isinstance(row, list) or len(row) < 6:
        raise MalformedKlineError(f"kline row too short: {row!r}")
    try:
        open_time = ms_to_datetime(row[0])
        open_ = Decimal(str(row[1]))
        high = Decimal(str(row[2]))
        low = Decimal(str(row[3]))
        close = Decimal(str(row[4]))
        volume = Decimal(str(row[5]))
        if len(row) >= 7 and row[6] is not None:
            close_time = ms_to_datetime(row[6])
        else:
            close_time = ms_to_datetime(
                int(row[0]) + interval_to_milliseconds(interval.value) - 1
            )
        quote_volume = Decimal(str(row[7])) if len(row) >= 8 and row[7] is not None else None
        trade_count = int(row[8]) if len(row) >= 9 and row[8] is not None else None
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise MalformedKlineError(f"invalid kline row: {row!r}") from exc

    # Incomplete bars are those whose close_time is still in the future relative to now.
    is_final = close_time < now
    return Candle(
        instrument=instrument,
        interval=interval,
        open_time=open_time,
        close_time=close_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=quote_volume,
        trade_count=trade_count,
        is_final=is_final,
        source=source,
        received_at=received_at,
    )


def parse_ws_kline_payload(
    data_item: dict[str, Any],
    *,
    market_type: MarketType,
    interval: Timeframe,
    received_at: datetime,
    is_final: bool,
    source: str = "ws",
) -> Candle:
    try:
        symbol = str(data_item["s"])
        open_ms = int(data_item["t"])
        open_time = ms_to_datetime(open_ms)
        close_time = ms_to_datetime(open_ms + interval_to_milliseconds(interval.value) - 1)
        return Candle(
            instrument=InstrumentRef(market_type=market_type, symbol=symbol),
            interval=interval,
            open_time=open_time,
            close_time=close_time,
            open=Decimal(str(data_item["o"])),
            high=Decimal(str(data_item["h"])),
            low=Decimal(str(data_item["l"])),
            close=Decimal(str(data_item["c"])),
            volume=Decimal(str(data_item.get("v", "0"))),
            quote_volume=None,
            trade_count=None,
            is_final=is_final,
            source=source,
            received_at=received_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedKlineError(f"invalid ws kline payload: {data_item!r}") from exc
