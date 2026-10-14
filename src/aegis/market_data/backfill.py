"""Historical kline backfill helpers (public REST only, no credentials).

Window math is pure so it can be tested without network or database. Each window
covers at most `KLINES_MAX_LIMIT` bars and is requested as
`startTime=window_start`, `endTime=window_start + n*interval - 1ms`.

Observed live 2026-10-13 (not stated in docs): Toobit treats startTime as inclusive,
respects endTime, returns rows oldest->newest, and serves only roughly the most
recent ~3,500 bars per interval; older windows return an empty list.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from aegis.market_data.candle_store import CandleStore
from aegis.market_data.constants import KLINES_MAX_LIMIT
from aegis.market_data.rest_client import ToobitRestMarketDataClient
from aegis.market_data.timeutil import interval_to_milliseconds
from aegis.schemas.common import Timeframe
from aegis.schemas.market import InstrumentRef

_ONE_MS = timedelta(milliseconds=1)


@dataclass(frozen=True)
class KlineWindow:
    start: datetime  # first open_time requested (inclusive)
    end: datetime  # endTime sent to Toobit (inclusive, = last open + interval - 1ms)
    bars: int  # number of bars the window spans (limit sent)


def floor_to_interval(value: datetime, interval: Timeframe) -> datetime:
    step_ms = interval_to_milliseconds(interval.value)
    epoch_ms = int(value.timestamp() * 1000)
    floored = epoch_ms - (epoch_ms % step_ms)
    return datetime.fromtimestamp(floored / 1000.0, tz=value.tzinfo)


def backfill_windows(
    start: datetime,
    end: datetime,
    interval: Timeframe,
    *,
    max_bars: int = KLINES_MAX_LIMIT,
) -> list[KlineWindow]:
    """Non-overlapping windows covering open times in `[start, end)`, oldest first.

    `start` is floored to the interval boundary; `end` is floored too so the
    in-progress bar (open_time == floor(now)) is excluded.
    """
    if max_bars < 1 or max_bars > KLINES_MAX_LIMIT:
        raise ValueError(f"max_bars must be 1..{KLINES_MAX_LIMIT}")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    step = timedelta(milliseconds=interval_to_milliseconds(interval.value))
    cursor = floor_to_interval(start, interval)
    stop = floor_to_interval(end, interval)
    windows: list[KlineWindow] = []
    while cursor < stop:
        remaining = int((stop - cursor) / step)
        bars = min(max_bars, remaining)
        window_end_open = cursor + step * bars
        windows.append(KlineWindow(start=cursor, end=window_end_open - _ONE_MS, bars=bars))
        cursor = window_end_open
    return windows


@dataclass
class BackfillResult:
    symbol: str
    interval: str
    windows: int = 0
    empty_windows: int = 0
    fetched: int = 0
    final: int = 0
    inserted: int = 0
    errors: list[str] = field(default_factory=list)


async def backfill_instrument(
    client: ToobitRestMarketDataClient,
    store: CandleStore,
    instrument: InstrumentRef,
    interval: Timeframe,
    *,
    start: datetime,
    end: datetime,
    delay_seconds: float = 0.25,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> BackfillResult:
    """Fetch windows oldest->newest and upsert finalized bars.

    HTTP/transport errors and malformed payloads (`ValueError`, incl.
    `MalformedKlineError` and JSON decode errors) are counted per window, not
    raised. Other exceptions are bugs and propagate. No retries.
    """
    result = BackfillResult(symbol=instrument.symbol, interval=interval.value)
    windows: Sequence[KlineWindow] = backfill_windows(start, end, interval)
    for index, window in enumerate(windows):
        if index > 0 and delay_seconds > 0:
            await sleep(delay_seconds)
        result.windows += 1
        try:
            candles = await client.get_klines(
                instrument,
                interval,
                limit=window.bars,
                start_time=window.start,
                end_time=window.end,
            )
        except (httpx.HTTPError, ValueError) as exc:
            # Keep going; report class name only.
            result.errors.append(f"{window.start.isoformat()}:{type(exc).__name__}")
            continue
        if not candles:
            result.empty_windows += 1
            continue
        result.fetched += len(candles)
        final = [c for c in candles if c.is_final]
        result.final += len(final)
        result.inserted += store.upsert_final(final)
    return result
