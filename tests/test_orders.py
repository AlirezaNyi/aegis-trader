"""Phase 7 Order Manager tests — mocks only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.exchange.errors import AmbiguousSubmitError
from aegis.guards.live import LiveExecutionNotAllowed
from aegis.orders.manager import OrderManager, OrderSubmitBlocked
from aegis.reconcile.service import Reconciler
from aegis.risk.handoff import RiskHandoffDenied
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import Order, OrderIntent, OrderStatus
from aegis.schemas.risk import RiskDecision, RiskDecisionType


def _live_settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base: dict[str, object] = {
        "trading_mode": TradingMode.LIVE,
        "live_armed": True,
        "kill_switch": False,
        "require_database": False,
        "toobit_api_key": "k",
        "toobit_api_secret": "s",
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _intent(
    *,
    proposal_id: object | None = None,
    correlation_id: str = "corr-live",
    client_order_id: str | None = None,
    ledger_kind: LedgerKind = LedgerKind.LIVE,
) -> OrderIntent:
    from uuid import UUID

    pid = proposal_id if isinstance(proposal_id, UUID) else uuid4()
    return OrderIntent(
        intent_id=uuid4(),
        proposal_id=pid,
        risk_decision_correlation_id=correlation_id,
        client_order_id=client_order_id or f"c-{uuid4().hex[:8]}",
        ledger_kind=ledger_kind,
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT"),
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("1"),
        price=Decimal("100"),
        created_at=datetime(2026, 10, 9, tzinfo=UTC),
    )


def _decision(
    kind: RiskDecisionType,
    *,
    proposal_id: object | None = None,
    correlation_id: str = "corr-live",
    expires_at: datetime | None = None,
) -> RiskDecision:
    from uuid import UUID

    now = datetime(2026, 10, 9, tzinfo=UTC)
    pid = proposal_id if isinstance(proposal_id, UUID) else uuid4()
    return RiskDecision(
        decision=kind,
        policy_version="test",
        rules_evaluated=["TEST"],
        rejection_reasons=(
            [{"rule_id": "TEST", "detail": "reject"}] if kind == RiskDecisionType.REJECT else []
        ),
        validated_order_params={"action": "BUY"} if kind == RiskDecisionType.APPROVE else None,
        decided_at=now,
        expires_at=expires_at or (now + timedelta(hours=1)),
        correlation_id=correlation_id,
        proposal_id=pid,
    )


def _paired() -> tuple[RiskDecision, OrderIntent]:
    proposal_id = uuid4()
    intent = _intent(proposal_id=proposal_id)
    decision = _decision(RiskDecisionType.APPROVE, proposal_id=proposal_id)
    return decision, intent


class SpyAdapter:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.orders: dict[str, Order] = {}
        self._intents: dict[str, OrderIntent] = {}
        self.fail_ambiguous = False
        self.get_responses: dict[str, Order | None] = {}

    def remember_intent(self, intent: OrderIntent) -> None:
        self._intents[intent.client_order_id] = intent

    def submit(self, intent: OrderIntent) -> Order:
        self.submit_calls += 1
        if self.fail_ambiguous:
            raise AmbiguousSubmitError("timeout", client_order_id=intent.client_order_id)
        now = datetime(2026, 10, 9, tzinfo=UTC)
        order = Order(
            order_id=uuid4(),
            intent_id=intent.intent_id,
            client_order_id=intent.client_order_id,
            exchange_order_id="ex-1",
            ledger_kind=LedgerKind.LIVE,
            instrument=intent.instrument,
            status=OrderStatus.OPEN,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            filled_quantity=Decimal("0"),
            price=intent.price,
            created_at=now,
            updated_at=now,
        )
        self.orders[intent.client_order_id] = order
        return order

    def cancel(self, client_order_id: str) -> Order:
        order = self.orders[client_order_id]
        updated = order.model_copy(update={"status": OrderStatus.CANCELED})
        self.orders[client_order_id] = updated
        return updated

    def get_order(self, client_order_id: str) -> Order | None:
        if client_order_id in self.get_responses:
            return self.get_responses[client_order_id]
        return self.orders.get(client_order_id)


def test_reject_never_calls_adapter() -> None:
    spy = SpyAdapter()
    mgr = OrderManager(_live_settings(), spy)
    proposal_id = uuid4()
    intent = _intent(proposal_id=proposal_id)
    with pytest.raises(RiskHandoffDenied):
        mgr.submit(
            _decision(RiskDecisionType.REJECT, proposal_id=proposal_id),
            intent,
            now=datetime(2026, 10, 9, tzinfo=UTC),
        )
    assert spy.submit_calls == 0


def test_approve_bind_submits_once() -> None:
    spy = SpyAdapter()
    mgr = OrderManager(_live_settings(), spy)
    decision, intent = _paired()
    order = mgr.submit(decision, intent, now=datetime(2026, 10, 9, tzinfo=UTC))
    assert order.status == OrderStatus.OPEN
    assert spy.submit_calls == 1
    # Duplicate decision/intent returns existing without second adapter submit
    again = mgr.submit(decision, intent, now=datetime(2026, 10, 9, tzinfo=UTC))
    assert again.client_order_id == order.client_order_id
    assert spy.submit_calls == 1


def test_unknown_blocks_second_submit_until_reconcile() -> None:
    spy = SpyAdapter()
    spy.fail_ambiguous = True
    reconciler = Reconciler(spy)
    mgr = OrderManager(_live_settings(), spy, reconciler=reconciler)

    decision, intent = _paired()
    # First submit: ambiguous → UNKNOWN; reconcile finds nothing → MISSING
    spy.get_responses[intent.client_order_id] = None
    result = mgr.submit(decision, intent, now=datetime(2026, 10, 9, tzinfo=UTC))
    assert result.status in {OrderStatus.UNKNOWN, OrderStatus.MISSING}
    assert spy.submit_calls == 1
    # MISSING after ambiguous submit keeps new-order flow blocked until owner clear.
    assert mgr.block_new_submits is True

    spy.fail_ambiguous = False
    decision2, intent2 = _paired()
    with pytest.raises(OrderSubmitBlocked):
        mgr.submit(decision2, intent2, now=datetime(2026, 10, 9, tzinfo=UTC))
    assert spy.submit_calls == 1


def test_unknown_blocks_until_found_reconcile() -> None:
    spy = SpyAdapter()
    spy.fail_ambiguous = True
    reconciler = Reconciler(spy)
    mgr = OrderManager(_live_settings(), spy, reconciler=reconciler)
    decision, intent = _paired()
    now = datetime(2026, 10, 9, tzinfo=UTC)
    found = Order(
        order_id=uuid4(),
        intent_id=intent.intent_id,
        client_order_id=intent.client_order_id,
        exchange_order_id="ex-9",
        ledger_kind=LedgerKind.LIVE,
        instrument=intent.instrument,
        status=OrderStatus.OPEN,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        filled_quantity=Decimal("0"),
        price=intent.price,
        created_at=now,
        updated_at=now,
    )
    spy.get_responses[intent.client_order_id] = found
    order = mgr.submit(decision, intent, now=now)
    assert order.status == OrderStatus.OPEN
    assert spy.submit_calls == 1
    assert mgr.block_new_submits is False

    # Same client_order_id never second-submits (returns existing)
    again = mgr.submit(decision, intent, now=now)
    assert again.status == OrderStatus.OPEN
    assert spy.submit_calls == 1


def test_duplicate_client_order_id() -> None:
    spy = SpyAdapter()
    mgr = OrderManager(_live_settings(), spy)
    decision, intent = _paired()
    mgr.submit(decision, intent, now=datetime(2026, 10, 9, tzinfo=UTC))
    # New decision/proposal but same client_order_id must not resubmit
    decision2 = _decision(
        RiskDecisionType.APPROVE,
        proposal_id=intent.proposal_id,
        correlation_id=intent.risk_decision_correlation_id,
    )
    intent2 = intent.model_copy()
    order2 = mgr.submit(decision2, intent2, now=datetime(2026, 10, 9, tzinfo=UTC))
    assert order2.client_order_id == intent.client_order_id
    assert spy.submit_calls == 1


def test_partial_fill_status_map() -> None:
    spy = SpyAdapter()
    mgr = OrderManager(_live_settings(), spy)
    decision, intent = _paired()
    now = datetime(2026, 10, 9, tzinfo=UTC)
    mgr.submit(decision, intent, now=now)
    partial = spy.orders[intent.client_order_id].model_copy(
        update={
            "status": OrderStatus.PARTIALLY_FILLED,
            "filled_quantity": Decimal("0.4"),
        }
    )
    spy.get_responses[intent.client_order_id] = partial
    updated = Reconciler(spy).query_and_update(mgr, intent.client_order_id, now=now)
    assert updated.status == OrderStatus.PARTIALLY_FILLED
    assert updated.order is not None
    assert updated.order.filled_quantity == Decimal("0.4")


def test_kill_switch_blocks() -> None:
    # LIVE + kill_switch cannot construct Settings; mutate after valid live settings.
    settings = _live_settings()
    object.__setattr__(settings, "kill_switch", True)
    spy = SpyAdapter()
    mgr = OrderManager(settings, spy)
    decision, intent = _paired()
    with pytest.raises((OrderSubmitBlocked, LiveExecutionNotAllowed)):
        mgr.submit(decision, intent, now=datetime(2026, 10, 9, tzinfo=UTC))
    assert spy.submit_calls == 0


def test_live_gates_paper_mode_refuses() -> None:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
    )
    spy = SpyAdapter()
    mgr = OrderManager(settings, spy)
    decision, intent = _paired()
    with pytest.raises((OrderSubmitBlocked, LiveExecutionNotAllowed)):
        mgr.submit(decision, intent, now=datetime(2026, 10, 9, tzinfo=UTC))
    assert spy.submit_calls == 0
