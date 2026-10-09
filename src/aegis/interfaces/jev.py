"""Jev evaluation port — official TypeSafe API only in later phases; mock here."""

from __future__ import annotations

from typing import Protocol

from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import EvidencePackage, JevResult


class JevPort(Protocol):
    def evaluate(self, package: EvidencePackage) -> JevResult:
        """Evaluate an evidence package."""


class UnavailableJevPort:
    """Returns unavailable when Jev is not configured or intentionally mocked."""

    def evaluate(self, package: EvidencePackage) -> JevResult:
        return JevResult(
            evidence_package_id=package.package_id,
            model=None,
            status=EvidenceStatus.UNAVAILABLE,
            answers={},
            usage={},
            latency_ms=0,
        )
