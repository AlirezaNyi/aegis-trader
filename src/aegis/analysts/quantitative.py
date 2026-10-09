"""Quantitative Analyst — sample stats only; not profitability claims."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import AnalystEvidence, AnalystType
from aegis.schemas.features import FeatureSnapshot, FeatureValueStatus
from aegis.schemas.market import Candle

# Minimum return samples for reporting stats (not a risk or sizing limit).
MIN_RETURN_SAMPLES = 30


def _simple_returns(closes: Sequence[Decimal]) -> list[Decimal]:
    out: list[Decimal] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev == 0:
            continue
        out.append((closes[i] - prev) / prev)
    return out


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _sample_stdev(values: Sequence[Decimal]) -> Decimal:
    n = len(values)
    mean = _mean(values)
    variance = sum((v - mean) ** 2 for v in values) / Decimal(n - 1)
    return variance.sqrt()


def analyze_quantitative(
    snapshot: FeatureSnapshot,
    candles: Sequence[Candle],
    *,
    evidence_time: datetime,
) -> AnalystEvidence:
    closes = [c.close for c in candles if c.is_final and c.close_time <= snapshot.as_of]
    returns = _simple_returns(closes)
    if len(returns) < MIN_RETURN_SAMPLES:
        return AnalystEvidence(
            analyst_type=AnalystType.QUANTITATIVE,
            status=EvidenceStatus.UNAVAILABLE,
            payload={
                "reason": "insufficient_sample",
                "return_count": len(returns),
                "min_required": MIN_RETURN_SAMPLES,
                "note": "30 is a reporting minimum, not a risk or position-size limit.",
            },
            evidence_time=evidence_time,
            sources=[{"kind": "feature_snapshot", "snapshot_id": str(snapshot.snapshot_id)}],
            notes="Insufficient return sample for quantitative summary.",
        )

    mean_ret = _mean(returns)
    stdev = _sample_stdev(returns)
    realized = snapshot.features.get("realized_vol_20")
    payload: dict[str, Any] = {
        "return_count": len(returns),
        "mean_simple_return": str(mean_ret),
        "stdev_simple_return": str(stdev),
        "realized_vol_20": (
            str(realized.value)
            if realized is not None
            and realized.status == FeatureValueStatus.OK
            and realized.value is not None
            else None
        ),
        "not_a_profitability_claim": True,
        "feature_schema_version": snapshot.schema_version,
    }
    return AnalystEvidence(
        analyst_type=AnalystType.QUANTITATIVE,
        status=EvidenceStatus.OK,
        payload=payload,
        evidence_time=evidence_time,
        sources=[{"kind": "feature_snapshot", "snapshot_id": str(snapshot.snapshot_id)}],
        notes="Descriptive return statistics only; not edge or profit probability.",
    )
