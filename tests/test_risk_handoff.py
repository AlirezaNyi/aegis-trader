"""Order Manager handoff gate for RiskDecision."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from aegis.risk.handoff import (
    IntentBindingError,
    RiskHandoffDenied,
    assert_intent_matches_decision,
    assert_may_submit_to_order_manager,
    may_submit_to_order_manager,
)
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import OrderIntent, OrderSide
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


_INSTRUMENT = InstrumentRef(
    exchange="toobit",
    market_type=MarketType.SPOT,
    symbol="ADAUSDT",
)


def _bound_decision_and_intent(
    *,
    params: dict | None = None,
    side: OrderSide = OrderSide.BUY,
    price: Decimal | None = Decimal("0.50"),
    quantity: Decimal = Decimal("200"),
    order_type: str = "LIMIT",
    time_in_force: str | None = None,
) -> tuple[RiskDecision, OrderIntent]:
    proposal_id = uuid4()
    now = datetime(2026, 10, 9, tzinfo=UTC)
    decision = RiskDecision(
        decision=RiskDecisionType.APPROVE,
        policy_version="1.0",
        validated_order_params=params
        if params is not None
        else {
            "action": "BUY",
            "instrument": {
                "exchange": "toobit",
                "market_type": "spot",
                "symbol": "ADAUSDT",
            },
            "entry_conditions": {
                "entry_price": "0.50",
                "order_type": order_type,
                **({"time_in_force": time_in_force} if time_in_force is not None else {}),
            },
            "sizing": {"entry_price": "0.50", "notional": "100"},
        },
        decided_at=now,
        expires_at=datetime(2026, 10, 9, 1, 0, tzinfo=UTC),
        correlation_id="corr-handoff",
        proposal_id=proposal_id,
    )
    intent = OrderIntent(
        intent_id=uuid4(),
        proposal_id=proposal_id,
        risk_decision_correlation_id="corr-handoff",
        client_order_id=f"paper-{uuid4().hex[:16]}",
        ledger_kind=LedgerKind.PAPER,
        instrument=_INSTRUMENT,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        time_in_force=time_in_force,
        created_at=now,
    )
    return decision, intent


def test_intent_binding_rejects_missing_approved_params() -> None:
    decision, intent = _bound_decision_and_intent(params=None)
    decision = decision.model_copy(update={"validated_order_params": None})
    with pytest.raises(IntentBindingError, match="validated_order_params"):
        assert_intent_matches_decision(decision, intent)


def test_intent_binding_rejects_changed_material_fields() -> None:
    decision, intent = _bound_decision_and_intent()
    altered = intent.model_copy(
        update={"side": OrderSide.SELL, "price": Decimal("9.99"), "quantity": Decimal("999")}
    )
    with pytest.raises(IntentBindingError):
        assert_intent_matches_decision(decision, altered)


def test_intent_binding_rejects_order_type_mismatch() -> None:
    decision, intent = _bound_decision_and_intent(order_type="LIMIT")
    altered = intent.model_copy(update={"order_type": "MARKET"})
    with pytest.raises(IntentBindingError, match="order_type"):
        assert_intent_matches_decision(decision, altered)


def test_intent_binding_rejects_time_in_force_mismatch() -> None:
    decision, intent = _bound_decision_and_intent(time_in_force="GTC")
    altered = intent.model_copy(update={"time_in_force": "IOC"})
    with pytest.raises(IntentBindingError, match="time_in_force"):
        assert_intent_matches_decision(decision, altered)
