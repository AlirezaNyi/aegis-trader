"""Order Manager gate — REJECT never reaches submit (INV-01)."""

from __future__ import annotations

from datetime import UTC, datetime

from aegis.schemas.risk import RiskDecision, RiskDecisionType


class RiskHandoffDenied(RuntimeError):
    """Raised when a non-APPROVE decision is passed toward Order Manager."""


def may_submit_to_order_manager(
    decision: RiskDecision,
    *,
    now: datetime | None = None,
) -> bool:
    """True only for non-expired APPROVE decisions.

    Order Manager must still re-check live gates (trading_mode / live_armed /
    kill_switch) at submit time; this gate alone is not live-submit authority.
    """
    if decision.decision != RiskDecisionType.APPROVE:
        return False
    clock = now if now is not None else datetime.now(tz=UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    expires = decision.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > clock


def assert_may_submit_to_order_manager(
    decision: RiskDecision,
    *,
    now: datetime | None = None,
) -> None:
    if not may_submit_to_order_manager(decision, now=now):
        raise RiskHandoffDenied(
            f"Order Manager handoff denied: decision={decision.decision.value} "
            f"expires_at={decision.expires_at.isoformat()}"
        )
