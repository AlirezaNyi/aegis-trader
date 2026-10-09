"""Phase 7 reconcile + restart recovery tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.exchange.errors import AmbiguousSubmitError
from aegis.orders.manager import OrderManager
from aegis.reconcile.restart import recover_open_intents
from aegis.reconcile.service import Reconciler
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import Order, OrderIntent, OrderStatus
from aegis.schemas.risk import RiskDecision, RiskDecisionType


def _live_settings() -> Settings:
    clear_settings_cache()
    return Settings(
        trading_mode=TradingMode.LIVE,
        live_armed=True,
        kill_switch=False,
        require_database=False,
        toobit_api_key="k",
        toobit_api_secret="s",
    )


def _intent(client_order_id: str = "c-rec-1") -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        proposal_id=uuid4(),
        risk_decision_correlation_id="corr-rec",
        client_order_id=client_order_id,
        ledger_kind=LedgerKind.LIVE,
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT"),
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("1"),
        price=Decimal("100"),
        created_at=datetime(2026, 10, 9, tzinfo=UTC),
    )


def _approve(intent: OrderIntent) -> RiskDecision:
    now = datetime(2026, 10, 9, tzinfo=UTC)
    return RiskDecision(
        decision=RiskDecisionType.APPROVE,
        policy_version="t",
        decided_at=now,
        expires_at=now + timedelta(hours=1),
        correlation_id=intent.risk_decision_correlation_id,
        proposal_id=intent.proposal_id,
        validated_order_params={"action": "BUY"},
    )


class MockAdapter:
    def __init__(self) -> None:
        self.submit_calls = 0
        self._intents: dict[str, OrderIntent] = {}
        self.get_map: dict[str, Order | None] = {}
        self.raise_ambiguous = False

    def remember_intent(self, intent: OrderIntent) -> None:
        self._intents[intent.client_order_id] = intent

    def submit(self, intent: OrderIntent) -> Order:
        self.submit_calls += 1
        if self.raise_ambiguous:
            raise AmbiguousSubmitError("timeout", client_order_id=intent.client_order_id)
        now = datetime(2026, 10, 9, tzinfo=UTC)
        return Order(
            order_id=uuid4(),
            intent_id=intent.intent_id,
            client_order_id=intent.client_order_id,
            exchange_order_id="ex",
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

    def cancel(self, client_order_id: str) -> Order:
        raise NotImplementedError

    def get_order(self, client_order_id: str) -> Order | None:
        return self.get_map.get(client_order_id)


def test_reconcile_after_ambiguous_finds_open() -> None:
    adapter = MockAdapter()
    adapter.raise_ambiguous = True
    reconciler = Reconciler(adapter)
    mgr = OrderManager(_live_settings(), adapter, reconciler=reconciler)
    intent = _intent("c-found")
    now = datetime(2026, 10, 9, tzinfo=UTC)
    adapter.get_map["c-found"] = Order(
        order_id=uuid4(),
        intent_id=intent.intent_id,
        client_order_id="c-found",
        exchange_order_id="99",
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
    order = mgr.submit(_approve(intent), intent, now=now)
    assert order.status == OrderStatus.OPEN
    assert adapter.submit_calls == 1


def test_reconcile_after_ambiguous_finds_filled() -> None:
    adapter = MockAdapter()
    adapter.raise_ambiguous = True
    reconciler = Reconciler(adapter)
    mgr = OrderManager(_live_settings(), adapter, reconciler=reconciler)
    intent = _intent("c-filled")
    now = datetime(2026, 10, 9, tzinfo=UTC)
    adapter.get_map["c-filled"] = Order(
        order_id=uuid4(),
        intent_id=intent.intent_id,
        client_order_id="c-filled",
        exchange_order_id="99",
        ledger_kind=LedgerKind.LIVE,
        instrument=intent.instrument,
        status=OrderStatus.FILLED,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        filled_quantity=Decimal("1"),
        price=intent.price,
        created_at=now,
        updated_at=now,
    )
    order = mgr.submit(_approve(intent), intent, now=now)
    assert order.status == OrderStatus.FILLED


def test_reconcile_not_found_missing() -> None:
    adapter = MockAdapter()
    adapter.raise_ambiguous = True
    reconciler = Reconciler(adapter)
    mgr = OrderManager(_live_settings(), adapter, reconciler=reconciler)
    intent = _intent("c-missing")
    adapter.get_map["c-missing"] = None
    order = mgr.submit(
        _approve(intent), intent, now=datetime(2026, 10, 9, tzinfo=UTC)
    )
    assert order.status == OrderStatus.MISSING
    assert mgr.block_new_submits is True


def test_restart_recovery() -> None:
    adapter = MockAdapter()
    reconciler = Reconciler(adapter)
    mgr = OrderManager(_live_settings(), adapter, reconciler=reconciler)
    intent = _intent("c-restart")
    now = datetime(2026, 10, 9, tzinfo=UTC)
    unknown = Order(
        order_id=uuid4(),
        intent_id=intent.intent_id,
        client_order_id="c-restart",
        exchange_order_id=None,
        ledger_kind=LedgerKind.LIVE,
        instrument=intent.instrument,
        status=OrderStatus.UNKNOWN,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        filled_quantity=Decimal("0"),
        price=intent.price,
        created_at=now,
        updated_at=now,
    )
    mgr.load_orders({"c-restart": unknown})
    mgr.load_intents({"c-restart": intent})
    assert mgr.block_new_submits is True

    adapter.get_map["c-restart"] = unknown.model_copy(
        update={
            "status": OrderStatus.FILLED,
            "filled_quantity": Decimal("1"),
            "exchange_order_id": "1",
        }
    )
    result = recover_open_intents(mgr, reconciler, now=now)
    assert result.ok is True
    assert mgr.orders["c-restart"].status == OrderStatus.FILLED
    assert mgr.block_new_submits is False
