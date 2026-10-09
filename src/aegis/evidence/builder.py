"""Evidence package builder — aggregates features + analyst evidence (no Jev)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.evidence import AnalystEvidence, EvidencePackage
from aegis.schemas.features import FeatureSnapshot
from aegis.schemas.market import InstrumentRef


def build_evidence_package(
    snapshot: FeatureSnapshot,
    analyst_evidence: list[AnalystEvidence],
    *,
    correlation_id: str,
    package_id: UUID | None = None,
    created_at: datetime | None = None,
) -> EvidencePackage:
    """Build a versioned EvidencePackage. Jev is not invoked in Phase 3."""
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
