"""Validate candle sequences before the decision pipeline consumes them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from aegis.market_data.quality import DataQualityIssue, DataQualityIssueKind
from aegis.market_data.timeutil import interval_to_milliseconds
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating a candle window for one instrument/timeframe."""

    ok: bool
    candles: tuple[Candle, ...]
    issues: tuple[DataQualityIssue, ...]
    market_data_age_ms: int | None
    market_integrity_ok: bool
    block_reasons: tuple[str, ...]


def validate_candles(
    candles: Sequence[Candle],
    *,
    instrument: InstrumentRef,
    timeframe: Timeframe,
    now: datetime,
    stale_after_ms: int = 5000,
) -> ValidationResult:
    """Fail closed unless finalized candles are fresh, ordered, and gap-free.

    Only ``is_final`` candles for the requested instrument/timeframe are kept.
    Duplicates, out-of-order bars, gaps, emptiness, or staleness block the cycle.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    issues: list[DataQualityIssue] = []
    block_reasons: list[str] = []

    filtered = [
        c
        for c in candles
        if c.instrument.exchange == instrument.exchange
        and c.instrument.market_type == instrument.market_type
        and c.instrument.symbol == instrument.symbol
        and c.interval == timeframe
        and c.is_final
    ]

    if not filtered:
        issue = DataQualityIssue(
            kind=DataQualityIssueKind.MALFORMED,
            detail="no finalized candles for instrument/timeframe",
            symbol=instrument.symbol,
            interval=timeframe.value,
        )
        issues.append(issue)
        block_reasons.append("no_finalized_candles")
        return ValidationResult(
            ok=False,
            candles=(),
            issues=tuple(issues),
            market_data_age_ms=None,
            market_integrity_ok=False,
            block_reasons=tuple(block_reasons),
        )

    ordered = sorted(filtered, key=lambda c: c.open_time)
    step_ms = interval_to_milliseconds(timeframe.value)
    prev_open_ms: int | None = None
    for candle in ordered:
        open_ms = int(candle.open_time.timestamp() * 1000)
        if prev_open_ms is not None:
            if open_ms == prev_open_ms:
                issues.append(
                    DataQualityIssue(
                        kind=DataQualityIssueKind.DUPLICATE,
                        detail=f"duplicate open_time={open_ms}",
                        symbol=instrument.symbol,
                        interval=timeframe.value,
                    )
                )
                block_reasons.append("duplicate_candle")
            elif open_ms < prev_open_ms:
                issues.append(
                    DataQualityIssue(
                        kind=DataQualityIssueKind.OUT_OF_ORDER,
                        detail=f"open_time={open_ms} before previous={prev_open_ms}",
                        symbol=instrument.symbol,
                        interval=timeframe.value,
                    )
                )
                block_reasons.append("out_of_order_candle")
            else:
                delta = open_ms - prev_open_ms
                if delta > step_ms:
                    missing = (delta // step_ms) - 1
                    issues.append(
                        DataQualityIssue(
                            kind=DataQualityIssueKind.GAP,
                            detail=(
                                f"gap of ~{missing} bars between "
                                f"{prev_open_ms} and {open_ms}"
                            ),
                            symbol=instrument.symbol,
                            interval=timeframe.value,
                        )
                    )
                    block_reasons.append("candle_gap")
        prev_open_ms = open_ms

    last = ordered[-1]
    last_received = last.received_at
    if last_received.tzinfo is None:
        last_received = last_received.replace(tzinfo=UTC)
    age_ms = int((now - last_received).total_seconds() * 1000)
    if age_ms < 0:
        age_ms = 0
    if age_ms > stale_after_ms:
        issues.append(
            DataQualityIssue(
                kind=DataQualityIssueKind.STALE,
                detail=(
                    f"last received {age_ms}ms ago "
                    f"(threshold {stale_after_ms}ms)"
                ),
                symbol=instrument.symbol,
                interval=timeframe.value,
            )
        )
        block_reasons.append("stale_market_data")

    structural_bad = any(
        i.kind
        in {
            DataQualityIssueKind.DUPLICATE,
            DataQualityIssueKind.OUT_OF_ORDER,
            DataQualityIssueKind.GAP,
            DataQualityIssueKind.MALFORMED,
        }
        for i in issues
    )
    integrity_ok = not structural_bad
    stale = "stale_market_data" in block_reasons
    ok = integrity_ok and not stale

    # Deduplicate block reasons while preserving order.
    seen: set[str] = set()
    unique_reasons: list[str] = []
    for reason in block_reasons:
        if reason not in seen:
            seen.add(reason)
            unique_reasons.append(reason)

    return ValidationResult(
        ok=ok,
        candles=tuple(ordered),
        issues=tuple(issues),
        market_data_age_ms=age_ms,
        market_integrity_ok=integrity_ok,
        block_reasons=tuple(unique_reasons),
    )
