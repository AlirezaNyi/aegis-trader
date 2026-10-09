"""Strategy Researcher — records hypotheses; never activates production strategies."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from aegis.schemas.common import ApiModel, EvidenceStatus
from aegis.schemas.evidence import AnalystEvidence, AnalystType
from aegis.schemas.features import FeatureSnapshot


class StrategyHypothesis(ApiModel):
    hypothesis_id: UUID = Field(default_factory=uuid4)
    strategy_id: str
    strategy_version: str
    statement: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"


def analyze_strategy_researcher(
    snapshot: FeatureSnapshot,
    hypothesis: StrategyHypothesis | None,
    *,
    evidence_time: datetime,
) -> AnalystEvidence:
    leakage_controls = {
        "finalized_candles_only": not snapshot.include_intrabar,
        "as_of_enforced": True,
        "look_ahead_blocked": True,
        "include_intrabar": snapshot.include_intrabar,
    }

    if hypothesis is None:
        return AnalystEvidence(
            analyst_type=AnalystType.STRATEGY_RESEARCHER,
            status=EvidenceStatus.NOT_APPLICABLE,
            payload={
                "reason": "no_hypothesis",
                "leakage_controls": leakage_controls,
                "activates_production_strategy": False,
            },
            evidence_time=evidence_time,
            sources=[{"kind": "feature_snapshot", "snapshot_id": str(snapshot.snapshot_id)}],
            notes="No hypothesis supplied; researcher is idle.",
        )

    payload: dict[str, Any] = {
        "hypothesis_id": str(hypothesis.hypothesis_id),
        "strategy_id": hypothesis.strategy_id,
        "strategy_version": hypothesis.strategy_version,
        "statement": hypothesis.statement,
        "parameters": hypothesis.parameters,
        "status": hypothesis.status,
        "leakage_controls": leakage_controls,
        "activates_production_strategy": False,
        "feature_schema_version": snapshot.schema_version,
    }
    return AnalystEvidence(
        analyst_type=AnalystType.STRATEGY_RESEARCHER,
        status=EvidenceStatus.OK,
        payload=payload,
        evidence_time=evidence_time,
        sources=[
            {"kind": "feature_snapshot", "snapshot_id": str(snapshot.snapshot_id)},
            {"kind": "hypothesis", "hypothesis_id": str(hypothesis.hypothesis_id)},
        ],
        notes="Hypothesis recorded as draft evidence only.",
    )
