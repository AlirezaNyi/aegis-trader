"""Backtest helpers: per-bar higher-timeframe (e.g. daily) context and determinism.

``HtfIndex`` gives each replayed trading bar the trailing higher-timeframe candles
that had **closed** by that bar's decision time (``close_time <= as_of``), via a
binary search instead of re-filtering the whole series per bar. Only the last
``HTF_CONTEXT_BARS`` bars are returned: enough for the longest SMA in
``HTF_SMA_LOOKBACKS`` (``build_htf_context`` uses only the last N closes for each
SMA, so a longer tail would not change the result). ``build_htf_context`` filters
``close_time <= as_of`` again, so the slice is defence in depth, not the only guard.

``backtest_settings`` removes CPU-load dependence from historical replays: live /
paper cycles keep the configured per-analyst timeout, but in a backtest a slow
analyst (CPU contention from parallel workers) must not turn into ``ERROR``
evidence on some runs and not others. Backtest paths therefore run analysts with
an effectively unbounded timeout (``BACKTEST_ANALYST_TIMEOUT_SECONDS``).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from datetime import datetime

from aegis.config.settings import Settings
from aegis.schemas.evidence import HTF_SMA_LOOKBACKS
from aegis.schemas.market import Candle, InstrumentRef

# Longest SMA lookback plus a small margin.
HTF_CONTEXT_BARS = max(HTF_SMA_LOOKBACKS) + 10

# One hour per analyst call: never reached by the CPU-only analysts in practice, so
# results do not depend on machine load. Backtest paths only.
BACKTEST_ANALYST_TIMEOUT_SECONDS = 3600.0


class HtfIndex:
    """Sorted final higher-timeframe candles of one instrument, sliced by ``as_of``."""

    def __init__(
        self,
        candles: Sequence[Candle],
        *,
        instrument: InstrumentRef,
        max_bars: int = HTF_CONTEXT_BARS,
    ) -> None:
        if max_bars < 1:
            raise ValueError("max_bars must be >= 1")
        by_open: dict[datetime, Candle] = {}
        for c in candles:
            if not c.is_final:
                continue
            if (
                c.instrument.symbol != instrument.symbol
                or c.instrument.market_type != instrument.market_type
            ):
                continue
            by_open.setdefault(c.open_time, c)
        ordered = [by_open[k] for k in sorted(by_open)]
        intervals = {c.interval for c in ordered}
        if len(intervals) > 1:
            raise ValueError("htf candles must share one interval")
        self._candles = ordered
        self._close_times = [c.close_time for c in ordered]
        self._max_bars = max_bars

    def __len__(self) -> int:
        return len(self._candles)

    def visible(self, as_of: datetime) -> list[Candle]:
        """Last ``max_bars`` candles with ``close_time <= as_of`` (never a later bar)."""
        hi = bisect_right(self._close_times, as_of)
        return self._candles[max(0, hi - self._max_bars) : hi]

    def window_slice(self, start: datetime, end: datetime) -> tuple[Candle, ...]:
        """Candles needed to serve ``visible(t)`` for any ``t`` in ``[start, end]``."""
        lo = max(0, bisect_right(self._close_times, start) - self._max_bars)
        hi = bisect_right(self._close_times, end)
        return tuple(self._candles[lo:hi])


def backtest_settings(settings: Settings) -> Settings:
    """Copy of ``settings`` with the analyst timeout made load-independent (backtest only)."""
    if settings.analyst_timeout_seconds >= BACKTEST_ANALYST_TIMEOUT_SECONDS:
        return settings
    return settings.model_copy(
        update={"analyst_timeout_seconds": BACKTEST_ANALYST_TIMEOUT_SECONDS}
    )
