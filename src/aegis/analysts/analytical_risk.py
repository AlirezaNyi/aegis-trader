"""Analytical Risk Analyst — descriptive market risks only; never APPROVE/REJECT."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import AnalystEvidence, AnalystType
from aegis.schemas.features import FeatureSnapshot, FeatureValueStatus


def analyze_analytical_risk(
    snapshot: FeatureSnapshot,
    *,
    evidence_time: datetime,
    spread: Decimal | None = None,
    funding_rate: Decimal | None = None,
) -> AnalystEvidence:
    payload: dict[str, Any] = {
        "approves_or_rejects": False,
        "does_not_size_or_leverage": True,
        "not_the_risk_engine": True,
        "feature_schema_version": snapshot.schema_version,
    }

    atr = snapshot.features.get("atr_14")
    sma = snapshot.features.get("sma_20")
    if (
        atr is not None
        and atr.status == FeatureValueStatus.OK
        and atr.value is not None
        and sma is not None
        and sma.status == FeatureValueStatus.OK
        and sma.value is not None
        and sma.value != 0
    ):
        atr_pct = atr.value / sma.value
        if atr_pct >= Decimal("0.02"):
            regime = "high"
        elif atr_pct <= Decimal("0.005"):
            regime = "low"
        else:
            regime = "medium"
        payload["volatility_regime"] = regime
        payload["atr_pct_vs_sma"] = str(atr_pct)
        payload["volatility_status"] = "ok"
    else:
        payload["volatility_regime"] = None
        payload["volatility_status"] = "unavailable"

    if spread is None:
        payload["liquidity"] = {"status": "unavailable", "reason": "spread_not_provided"}
    else:
        payload["liquidity"] = {"status": "ok", "spread": str(spread)}

    if funding_rate is None:
        payload["funding"] = {"status": "unavailable", "reason": "funding_not_provided"}
    else:
        payload["funding"] = {"status": "ok", "funding_rate": str(funding_rate)}

    return AnalystEvidence(
        analyst_type=AnalystType.ANALYTICAL_RISK,
        status=EvidenceStatus.OK,
        payload=payload,
        evidence_time=evidence_time,
        sources=[{"kind": "feature_snapshot", "snapshot_id": str(snapshot.snapshot_id)}],
        notes="Analytical risk observations only; deterministic Risk Engine is separate.",
    )
