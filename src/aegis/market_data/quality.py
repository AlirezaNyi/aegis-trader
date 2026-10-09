"""Data-quality checks: duplicates, gaps, out-of-order, stale."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from aegis.market_data.timeutil import interval_to_milliseconds
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle


class DataQualityIssueKind(StrEnum):
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    GAP = "gap"
    STALE = "stale"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class DataQualityIssue:
    kind: DataQualityIssueKind
    detail: str
    symbol: str | None = None
    interval: str | None = None


@dataclass
class CandleStreamTracker:
    """Tracks open_time progression per symbol/interval for quality detection."""

    stale_after_ms: int = 120_000
    _last_open_ms: dict[tuple[str, str], int] = field(default_factory=dict)
    _last_received_at: dict[tuple[str, str], datetime] = field(default_factory=dict)
    issues: list[DataQualityIssue] = field(default_factory=list)

    def observe(self, candle: Candle) -> list[DataQualityIssue]:
        key = (candle.instrument.symbol, candle.interval.value)
        open_ms = int(candle.open_time.timestamp() * 1000)
        found: list[DataQualityIssue] = []
        prev = self._last_open_ms.get(key)
        if prev is not None:
            if open_ms == prev:
                issue = DataQualityIssue(
                    kind=DataQualityIssueKind.DUPLICATE,
                    detail=f"duplicate open_time={open_ms}",
                    symbol=candle.instrument.symbol,
                    interval=candle.interval.value,
                )
                found.append(issue)
            elif open_ms < prev:
                issue = DataQualityIssue(
                    kind=DataQualityIssueKind.OUT_OF_ORDER,
                    detail=f"open_time={open_ms} before previous={prev}",
                    symbol=candle.instrument.symbol,
                    interval=candle.interval.value,
                )
                found.append(issue)
            else:
                expected_step = interval_to_milliseconds(candle.interval.value)
                delta = open_ms - prev
                if delta > expected_step:
                    missing = (delta // expected_step) - 1
                    issue = DataQualityIssue(
                        kind=DataQualityIssueKind.GAP,
                        detail=f"gap of ~{missing} bars between {prev} and {open_ms}",
                        symbol=candle.instrument.symbol,
                        interval=candle.interval.value,
                    )
                    found.append(issue)
            if open_ms > prev:
                self._last_open_ms[key] = open_ms
        else:
            self._last_open_ms[key] = open_ms

        self._last_received_at[key] = candle.received_at
        self.issues.extend(found)
        return found

    def check_stale(
        self,
        *,
        now: datetime,
        symbol: str,
        interval: Timeframe,
    ) -> DataQualityIssue | None:
        key = (symbol, interval.value)
        last = self._last_received_at.get(key)
        if last is None:
            return DataQualityIssue(
                kind=DataQualityIssueKind.STALE,
                detail="no market data received yet",
                symbol=symbol,
                interval=interval.value,
            )
        age_ms = int((now - last).total_seconds() * 1000)
        if age_ms > self.stale_after_ms:
            issue = DataQualityIssue(
                kind=DataQualityIssueKind.STALE,
                detail=f"last received {age_ms}ms ago (threshold {self.stale_after_ms}ms)",
                symbol=symbol,
                interval=interval.value,
            )
            self.issues.append(issue)
            return issue
        return None
