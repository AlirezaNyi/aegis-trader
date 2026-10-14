"""Jev evaluation port — official TypeSafe API only in later phases; mock here."""

from __future__ import annotations

from typing import Protocol

from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import EvidencePackage, JevResult


class JevPort(Protocol):
    async def evaluate(self, package: EvidencePackage) -> JevResult:
        """Evaluate an evidence package (async; must never raise for transport errors)."""


class UnavailableJevPort:
    """Returns unavailable when Jev is not configured or intentionally mocked."""

    async def evaluate(self, package: EvidencePackage) -> JevResult:
        return JevResult(
            evidence_package_id=package.package_id,
            model=None,
            status=EvidenceStatus.UNAVAILABLE,
            answers={},
            usage={},
            latency_ms=0,
            confidence_notes=(
                "Jev confidence is distribution concentration, "
                "not calibrated probability of profit."
            ),
        )
