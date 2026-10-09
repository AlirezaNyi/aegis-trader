"""Optional persistence helpers for Phase 3 evidence tables (Postgres)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from aegis.analysts.strategy_researcher import StrategyHypothesis
from aegis.db.models import (
    AnalystEvidenceRow,
    EvidencePackageRow,
    FeatureSnapshotRow,
    StrategyExperimentRow,
)
from aegis.schemas.evidence import AnalystEvidence, EvidencePackage
from aegis.schemas.features import FeatureSnapshot


def persist_feature_snapshot(session: Session, snapshot: FeatureSnapshot) -> FeatureSnapshotRow:
    row = FeatureSnapshotRow(
        id=snapshot.snapshot_id,
        schema_version=snapshot.schema_version,
        exchange=snapshot.instrument.exchange,
        market_type=snapshot.market_type.value,
        symbol=snapshot.instrument.symbol,
        timeframe=snapshot.timeframe.value,
        as_of=snapshot.as_of,
        include_intrabar=snapshot.include_intrabar,
        candle_count=snapshot.candle_count,
        gap_detected=snapshot.gap_detected,
        features={
            name: value.model_dump(mode="json") for name, value in snapshot.features.items()
        },
        created_at=snapshot.created_at,
    )
    session.add(row)
    return row


def persist_evidence_package(
    session: Session,
    package: EvidencePackage,
    analyst_evidence: list[AnalystEvidence],
) -> EvidencePackageRow:
    pkg = EvidencePackageRow(
        id=package.package_id,
        schema_version=package.schema_version,
        correlation_id=package.correlation_id,
        exchange=package.instrument.exchange,
        market_type=package.market_type.value,
        symbol=package.instrument.symbol,
        timeframe=package.timeframe.value,
        as_of=package.as_of,
        feature_refs=list(package.feature_refs),
        created_at=package.created_at,
    )
    session.add(pkg)
    for evidence in analyst_evidence:
        session.add(
            AnalystEvidenceRow(
                package_id=package.package_id,
                analyst_type=evidence.analyst_type.value,
                status=evidence.status.value,
                payload=dict(evidence.payload),
                evidence_time=evidence.evidence_time,
                sources=list(evidence.sources),
                notes=evidence.notes,
            )
        )
    return pkg


def persist_strategy_experiment(
    session: Session,
    hypothesis: StrategyHypothesis,
    *,
    leakage_controls: dict[str, object] | None = None,
) -> StrategyExperimentRow:
    row = StrategyExperimentRow(
        hypothesis_id=hypothesis.hypothesis_id,
        strategy_id=hypothesis.strategy_id,
        strategy_version=hypothesis.strategy_version,
        statement=hypothesis.statement,
        parameters=dict(hypothesis.parameters),
        status=hypothesis.status,
        leakage_controls=dict(leakage_controls or {}),
    )
    session.add(row)
    return row
