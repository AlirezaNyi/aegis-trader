"""Persistence helpers for risk decisions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from aegis.db.models import RiskDecisionRow
from aegis.schemas.risk import RiskDecision


def persist_risk_decision(session: Session, decision: RiskDecision) -> RiskDecisionRow:
    row = RiskDecisionRow(
        proposal_id=decision.proposal_id,
        correlation_id=decision.correlation_id,
        decision=decision.decision.value,
        policy_version=decision.policy_version,
        rules_evaluated=list(decision.rules_evaluated),
        rejection_reasons=list(decision.rejection_reasons),
        validated_order_params=decision.validated_order_params,
        account_state_refs=list(decision.account_state_refs),
        market_state_refs=list(decision.market_state_refs),
        decided_at=decision.decided_at,
        expires_at=decision.expires_at,
    )
    session.add(row)
    return row
