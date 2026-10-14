"""Technical Analyst — descriptive labels only; never submits orders."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import AnalystEvidence, AnalystType
from aegis.schemas.features import FeatureSnapshot, FeatureValueStatus


def analyze_technical(
    snapshot: FeatureSnapshot,
    *,
    evidence_time: datetime,
) -> AnalystEvidence:
    critical = ("sma_20", "ema_20", "rsi_14", "atr_14")
    values: dict[str, Decimal] = {}
    missing: list[str] = []
    for name in critical:
        fv = snapshot.features.get(name)
        if fv is None or fv.status != FeatureValueStatus.OK or fv.value is None:
            missing.append(name)
        else:
            values[name] = fv.value
    if missing:
        return AnalystEvidence(
            analyst_type=AnalystType.TECHNICAL,
            status=EvidenceStatus.UNAVAILABLE,
            payload={"missing_features": missing, "schema_version": snapshot.schema_version},
            evidence_time=evidence_time,
            sources=[{"kind": "feature_snapshot", "snapshot_id": str(snapshot.snapshot_id)}],
            notes="Critical technical features unavailable.",
        )

    # Explicit narrowing (no `assert`): every critical value was checked above.
    sma = values["sma_20"]
    ema = values["ema_20"]
    rsi = values["rsi_14"]
    atr = values["atr_14"]

    if ema > sma:
        trend = "up"
    elif ema < sma:
        trend = "down"
    else:
        trend = "flat"

    if rsi >= Decimal("70"):
        rsi_zone = "overbought"
    elif rsi <= Decimal("30"):
        rsi_zone = "oversold"
    else:
        rsi_zone = "neutral"

    last_close_proxy = sma  # descriptive only; ATR% vs SMA as price proxy
    atr_pct = (atr / last_close_proxy) if last_close_proxy != 0 else None
    if atr_pct is None:
        vol_label = "unknown"
    elif atr_pct >= Decimal("0.02"):
        vol_label = "elevated"
    elif atr_pct <= Decimal("0.005"):
        vol_label = "compressed"
    else:
        vol_label = "moderate"

    volume_ratio = snapshot.features.get("volume_ratio")
    volume_ratio_value: Decimal | None = None
    volume_confirm: str
    if (
        volume_ratio is None
        or volume_ratio.status != FeatureValueStatus.OK
        or volume_ratio.value is None
    ):
        volume_confirm = "unavailable"
    else:
        volume_ratio_value = volume_ratio.value
        if volume_ratio_value >= Decimal("1.2"):
            volume_confirm = "above_average"
        elif volume_ratio_value <= Decimal("0.8"):
            volume_confirm = "below_average"
        else:
            volume_confirm = "average"

    payload: dict[str, Any] = {
        "trend": trend,
        "rsi_zone": rsi_zone,
        "volatility_label": vol_label,
        "atr_pct_vs_sma": str(atr_pct) if atr_pct is not None else None,
        "volume_confirm": volume_confirm,
        # Raw feature values (Decimal as string) copied from the snapshot so
        # downstream deterministic consumers do not re-derive them from labels.
        "rsi_14": str(rsi),
        "ema_20": str(ema),
        "sma_20": str(sma),
        "volume_ratio": str(volume_ratio_value) if volume_ratio_value is not None else None,
        "thresholds_are_descriptive_only": True,
        "not_an_order_signal": True,
        "feature_schema_version": snapshot.schema_version,
    }
    return AnalystEvidence(
        analyst_type=AnalystType.TECHNICAL,
        status=EvidenceStatus.OK,
        payload=payload,
        evidence_time=evidence_time,
        sources=[{"kind": "feature_snapshot", "snapshot_id": str(snapshot.snapshot_id)}],
        notes="Descriptive technical labels only; not trade authority.",
    )
