"""Persistence helpers for Jev evaluation results."""

from __future__ import annotations

from sqlalchemy.orm import Session

from aegis.db.models import JevResultRow
from aegis.schemas.evidence import JevResult


def persist_jev_result(session: Session, result: JevResult) -> JevResultRow:
    row = JevResultRow(
        evidence_package_id=result.evidence_package_id,
        model=result.model,
        status=result.status.value,
        answers=dict(result.answers),
        usage=dict(result.usage),
        latency_ms=result.latency_ms,
        confidence_notes=result.confidence_notes,
    )
    session.add(row)
    return row
