"""Cross-venue close comparison (research only; e.g. Toobit vs Binance archive).

Pure functions over already-loaded candles. Statistics are descriptive floats
(basis-point diagnostics), not prices used for trading decisions.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from aegis.market_data.timeutil import interval_to_milliseconds
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle

_BPS = Decimal(10_000)


@dataclass(frozen=True)
class CloseBasisReport:
    primary_bars: int
    reference_bars: int
    matched: int
    first_open: datetime | None
    last_open: datetime | None
    mean_abs_bps: float | None
    median_abs_bps: float | None
    p95_abs_bps: float | None
    max_abs_bps: float | None
    mean_signed_bps: float | None  # (primary - reference) / reference
    return_pairs: int
    return_correlation: float | None  # Pearson, simple 1-bar close-to-close returns


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (`q` in [0, 1]) of pre-sorted values."""
    if not sorted_values:
        raise ValueError("percentile of empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def compare_closes(
    primary: Sequence[Candle],
    reference: Sequence[Candle],
    interval: Timeframe,
) -> CloseBasisReport:
    """Match bars on `open_time` and summarise close differences in basis points.

    Return correlation uses only consecutive bar pairs present on both venues.
    Bars with a non-positive reference close are ignored.
    """
    primary_close = {c.open_time: c.close for c in primary if c.interval == interval}
    reference_close = {
        c.open_time: c.close for c in reference if c.interval == interval and c.close > 0
    }
    matched_times = sorted(set(primary_close) & set(reference_close))
    signed: list[float] = []
    for t in matched_times:
        diff = (primary_close[t] - reference_close[t]) / reference_close[t] * _BPS
        signed.append(float(diff))
    absolute = sorted(abs(v) for v in signed)

    step = timedelta(milliseconds=interval_to_milliseconds(interval.value))
    matched_set = set(matched_times)
    primary_returns: list[float] = []
    reference_returns: list[float] = []
    for t in matched_times:
        prev = t - step
        if prev not in matched_set or primary_close[prev] <= 0:
            continue
        primary_returns.append(float(primary_close[t] / primary_close[prev] - 1))
        reference_returns.append(float(reference_close[t] / reference_close[prev] - 1))

    correlation: float | None = None
    if len(primary_returns) >= 2:
        try:
            correlation = statistics.correlation(primary_returns, reference_returns)
        except statistics.StatisticsError:
            correlation = None  # constant series

    return CloseBasisReport(
        primary_bars=len(primary_close),
        reference_bars=len(reference_close),
        matched=len(matched_times),
        first_open=matched_times[0] if matched_times else None,
        last_open=matched_times[-1] if matched_times else None,
        mean_abs_bps=statistics.fmean(absolute) if absolute else None,
        median_abs_bps=statistics.median(absolute) if absolute else None,
        p95_abs_bps=percentile(absolute, 0.95) if absolute else None,
        max_abs_bps=absolute[-1] if absolute else None,
        mean_signed_bps=statistics.fmean(signed) if signed else None,
        return_pairs=len(primary_returns),
        return_correlation=correlation,
    )
