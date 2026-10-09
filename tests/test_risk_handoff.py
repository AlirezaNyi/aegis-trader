"""Order Manager handoff gate for RiskDecision."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aegis.risk.handoff import (
    RiskHandoffDenied,
    assert_may_submit_to_order_manager,
    may_submit_to_order_manager,
)
from aegis.schemas.risk import RiskDecision, RiskDecisionType


def _decision(
    kind: RiskDecisionType,
    *,
    expires_at: datetime | None = None,
) -> RiskDecision:
    now = datetime(2026, 10, 9, tzinfo=UTC)
    return RiskDecision(
        decision=kind,
        policy_version="0.1-draft",
        rules_evaluated=["RP-KILL-SWITCH"],
        rejection_reasons=(
            [{"rule_id": "RP-KILL-SWITCH", "detail": "active"}]
            if kind == RiskDecisionType.REJECT
            else []
        ),
        validated_order_params={"action": "BUY"} if kind == RiskDecisionType.APPROVE else None,
        decided_at=now,
        expires_at=expires_at or datetime(2026, 10, 9, 1, 0, tzinfo=UTC),
        correlation_id="corr-handoff",
        proposal_id=uuid4(),
    )


def test_reject_never_may_submit() -> None:
    decision = _decision(RiskDecisionType.REJECT)
    assert may_submit_to_order_manager(decision, now=datetime(2026, 10, 9, tzinfo=UTC)) is False
    with pytest.raises(RiskHandoffDenied):
        assert_may_submit_to_order_manager(
            decision, now=datetime(2026, 10, 9, tzinfo=UTC)
        )


def test_approve_may_submit() -> None:
    decision = _decision(RiskDecisionType.APPROVE)
    now = datetime(2026, 10, 9, tzinfo=UTC)
    assert may_submit_to_order_manager(decision, now=now) is True
    assert_may_submit_to_order_manager(decision, now=now)


def test_expired_approve_may_not_submit() -> None:
    decision = _decision(
        RiskDecisionType.APPROVE,
        expires_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    later = datetime(2026, 10, 9, 0, 0, 1, tzinfo=UTC)
    assert may_submit_to_order_manager(decision, now=later) is False
