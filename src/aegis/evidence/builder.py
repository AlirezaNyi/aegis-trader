"""Evidence package builder — aggregates features + analyst evidence (no Jev)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.evidence import AnalystEvidence, EvidencePackage, MarketContext
from aegis.schemas.features import FeatureSnapshot, FeatureValueStatus
from aegis.schemas.market import Candle, InstrumentRef

_ATR_PCT_QUANT = Decimal("0.0001")


def build_market_context(
    candles: Sequence[Candle],
    snapshot: FeatureSnapshot,
    *,
    lookback: int = 20,
    spread_bps: Decimal | None = None,
) -> MarketContext | None:
    """Descriptive price levels from final candles at or before ``snapshot.as_of``.

    Only ``is_final`` candles matching the snapshot instrument and timeframe with
    ``close_time <= snapshot.as_of`` are used (no look-ahead, no intrabar).
    ``atr_14`` is taken from the snapshot only when its status is ``ok``.
    Returns ``None`` when no eligible candle exists.
    """
    if lookback < 1:
        msg = "lookback must be >= 1"
        raise ValueError(msg)
    eligible = [
        c
        for c in candles
        if c.is_final
        and c.close_time <= snapshot.as_of
        and c.interval == snapshot.timeframe
        and c.instrument.symbol == snapshot.instrument.symbol
        and c.instrument.market_type == snapshot.instrument.market_type
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda c: c.open_time)
    window = eligible[-lookback:]
    last = window[-1]

    atr: Decimal | None = None
    atr_feature = snapshot.features.get("atr_14")
    if (
        atr_feature is not None
        and atr_feature.status == FeatureValueStatus.OK
        and atr_feature.value is not None
    ):
        atr = atr_feature.value

    atr_pct: Decimal | None = None
    if atr is not None and last.close > 0:
        atr_pct = (atr / last.close * Decimal(100)).quantize(_ATR_PCT_QUANT)

    return MarketContext(
        last_close=last.close,
        recent_high=max(c.high for c in window),
        recent_low=min(c.low for c in window),
        lookback_candles=len(window),
        atr_14=atr,
        atr_pct_of_close=atr_pct,
        spread_bps=spread_bps,
        spread_status="ok" if spread_bps is not None else "unavailable",
        as_of=last.close_time,
    )


def build_evidence_package(
    snapshot: FeatureSnapshot,
    analyst_evidence: list[AnalystEvidence],
    *,
    correlation_id: str,
    package_id: UUID | None = None,
    created_at: datetime | None = None,
    candles: Sequence[Candle] | None = None,
    spread_bps: Decimal | None = None,
) -> EvidencePackage:
    """Build a versioned EvidencePackage. Jev is not invoked in Phase 3.

    When ``candles`` is given, ``market_context`` is populated via
    :func:`build_market_context` (``None`` if no eligible final candle).
    """
    market_context = (
        build_market_context(candles, snapshot, spread_bps=spread_bps)
        if candles is not None
        else None
    )
    return EvidencePackage(
        schema_version="1",
        package_id=package_id or uuid4(),
        correlation_id=correlation_id,
        created_at=created_at or datetime.now(UTC),
        instrument=snapshot.instrument,
        market_type=snapshot.market_type,
        timeframe=snapshot.timeframe,
        as_of=snapshot.as_of,
        feature_refs=[str(snapshot.snapshot_id)],
        analyst_evidence=list(analyst_evidence),
        market_context=market_context,
    )


def empty_package_shell(
    *,
    instrument: InstrumentRef,
    market_type: MarketType,
    timeframe: Timeframe,
    as_of: datetime,
    correlation_id: str,
) -> EvidencePackage:
    """Minimal package used in tests when no snapshot is available."""
    return EvidencePackage(
        schema_version="1",
        package_id=uuid4(),
        correlation_id=correlation_id,
        created_at=datetime.now(UTC),
        instrument=instrument,
        market_type=market_type,
        timeframe=timeframe,
        as_of=as_of,
        feature_refs=[],
        analyst_evidence=[],
    )
