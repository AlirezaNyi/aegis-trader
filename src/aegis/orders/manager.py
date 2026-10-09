"""Order Manager — live submit after Risk APPROVE; never blind-retry."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from aegis.config.settings import Settings
from aegis.exchange.errors import AmbiguousSubmitError
from aegis.guards.live import assert_live_execution_allowed, trading_ready
from aegis.interfaces.execution import ExecutionPort
from aegis.orders.idempotency import (
    IdempotencyError,
    SubmitAttemptTracker,
    blocks_resubmit,
    is_terminal,
)
from aegis.risk.handoff import assert_may_submit_to_order_manager
from aegis.schemas.common import LedgerKind
from aegis.schemas.orders import Order, OrderIntent, OrderStatus
from aegis.schemas.risk import RiskDecision


class OrderManagerError(RuntimeError):
    """Base Order Manager error."""


class OrderSubmitBlocked(OrderManagerError):
    """New submits blocked (kill switch, reconcile critical, gates)."""


class OrderBindError(OrderManagerError):
    """RiskDecision does not bind to the OrderIntent."""


class OrderManager:
    """Tracks live intents, enforces INV-01 / timeout→reconcile / ledger_kind=LIVE."""

    def __init__(
        self,
        settings: Settings,
        execution: ExecutionPort,
        *,
        reconciler: object | None = None,
    ) -> None:
        self._settings = settings
        self._execution = execution
        self._reconciler = reconciler
        self._orders: dict[str, Order] = {}
        self._intents: dict[str, OrderIntent] = {}
        self._tracker = SubmitAttemptTracker()
        self._critical_unknown = False
        self._block_new_submits = False

    @property
    def orders(self) -> dict[str, Order]:
        return self._orders

    @property
    def intents(self) -> dict[str, OrderIntent]:
        return self._intents

    @property
    def critical_unknown(self) -> bool:
        return self._critical_unknown

    @property
    def block_new_submits(self) -> bool:
        return self._block_new_submits or self._critical_unknown

    def set_reconciler(self, reconciler: object) -> None:
        self._reconciler = reconciler

    def submit(
        self,
        decision: RiskDecision,
        intent: OrderIntent,
        *,
        now: datetime | None = None,
    ) -> Order:
        clock = now if now is not None else datetime.now(tz=UTC)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=UTC)

        assert_may_submit_to_order_manager(decision, now=clock)
        self._assert_decision_binds_intent(decision, intent)
        self._assert_live_path_allowed(intent)

        if self.block_new_submits:
            raise OrderSubmitBlocked(
                "new live submits blocked: critical unknown / reconcile incomplete"
            )

        existing = self._orders.get(intent.client_order_id)
        if existing is not None:
            if is_terminal(existing.status) or blocks_resubmit(existing.status):
                return existing

        try:
            self._tracker.ensure_first_submit(intent.client_order_id)
        except IdempotencyError:
            if existing is not None:
                return existing
            raise

        self._intents[intent.client_order_id] = intent
        remember = getattr(self._execution, "remember_intent", None)
        if callable(remember):
            remember(intent)

        placeholder = Order(
            order_id=uuid4(),
            intent_id=intent.intent_id,
            client_order_id=intent.client_order_id,
            exchange_order_id=None,
            ledger_kind=LedgerKind.LIVE,
            instrument=intent.instrument,
            status=OrderStatus.SUBMIT_ATTEMPTED,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            filled_quantity=Decimal("0"),
            price=intent.price,
            created_at=clock,
            updated_at=clock,
        )
        self._orders[intent.client_order_id] = placeholder
        self._tracker.mark_attempted(intent.client_order_id)

        try:
            order = self._execution.submit(intent)
        except AmbiguousSubmitError:
            unknown = placeholder.model_copy(
                update={"status": OrderStatus.UNKNOWN, "updated_at": clock}
            )
            self._orders[intent.client_order_id] = unknown
            self._critical_unknown = True
            self._block_new_submits = True
            if self._reconciler is not None:
                query = getattr(self._reconciler, "query_and_update", None)
                if callable(query):
                    query(self, intent.client_order_id, now=clock)
            # Never second submit — return current local state
            return self._orders[intent.client_order_id]
        except Exception:
            # Any post-attempt failure is treated as ambiguous capital risk.
            unknown = placeholder.model_copy(
                update={"status": OrderStatus.UNKNOWN, "updated_at": clock}
            )
            self._orders[intent.client_order_id] = unknown
            self._critical_unknown = True
            self._block_new_submits = True
            if self._reconciler is not None:
                query = getattr(self._reconciler, "query_and_update", None)
                if callable(query):
                    query(self, intent.client_order_id, now=clock)
            return self._orders[intent.client_order_id]

        if order.ledger_kind != LedgerKind.LIVE:
            raise OrderManagerError(
                f"execution returned non-live ledger_kind={order.ledger_kind.value!r}"
            )
        self._orders[intent.client_order_id] = order
        return order

    def cancel(self, client_order_id: str, *, now: datetime | None = None) -> Order:
        clock = now if now is not None else datetime.now(tz=UTC)
        order = self._execution.cancel(client_order_id)
        updated = order.model_copy(update={"updated_at": clock})
        self._orders[client_order_id] = updated
        return updated

    def apply_exchange_order(
        self,
        client_order_id: str,
        exchange_order: Order | None,
        *,
        now: datetime | None = None,
    ) -> Order:
        """Apply get_order result (reconcile / partial fills)."""
        clock = now if now is not None else datetime.now(tz=UTC)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=UTC)

        local = self._orders.get(client_order_id)
        if exchange_order is None:
            if local is None:
                intent = self._intents.get(client_order_id)
                if intent is None:
                    raise OrderManagerError(
                        f"missing local state for client_order_id={client_order_id!r}"
                    )
                missing = Order(
                    order_id=uuid4(),
                    intent_id=intent.intent_id,
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    ledger_kind=LedgerKind.LIVE,
                    instrument=intent.instrument,
                    status=OrderStatus.MISSING,
                    side=intent.side,
                    order_type=intent.order_type,
                    quantity=intent.quantity,
                    filled_quantity=Decimal("0"),
                    price=intent.price,
                    created_at=clock,
                    updated_at=clock,
                )
                self._orders[client_order_id] = missing
                self._recompute_critical_flags()
                return missing
            missing = local.model_copy(
                update={"status": OrderStatus.MISSING, "updated_at": clock}
            )
            self._orders[client_order_id] = missing
            self._recompute_critical_flags()
            return missing

        # Preserve local order_id / intent_id when updating from exchange
        if local is not None:
            merged = local.model_copy(
                update={
                    "status": exchange_order.status,
                    "filled_quantity": exchange_order.filled_quantity,
                    "exchange_order_id": exchange_order.exchange_order_id,
                    "price": exchange_order.price or local.price,
                    "updated_at": clock,
                }
            )
        else:
            merged = exchange_order.model_copy(update={"updated_at": clock})
        self._orders[client_order_id] = merged
        self._recompute_critical_flags()
        return merged

    def allow_new_submits(self) -> None:
        """Owner/policy clear: only when no UNKNOWN or MISSING live orders remain."""
        self._recompute_critical_flags()
        if not self._critical_unknown and not self._has_missing():
            self._block_new_submits = False

    def load_orders(self, orders: dict[str, Order]) -> None:
        """Restart recovery: seed tracked orders (ledger_kind=live only)."""
        for client_order_id, order in orders.items():
            if order.ledger_kind != LedgerKind.LIVE:
                raise OrderManagerError(
                    f"refuse loading non-live order client_order_id={client_order_id!r}"
                )
            self._orders[client_order_id] = order
            if blocks_resubmit(order.status):
                self._tracker.mark_attempted(client_order_id)
            if order.status == OrderStatus.UNKNOWN:
                self._critical_unknown = True
                self._block_new_submits = True

    def load_intents(self, intents: dict[str, OrderIntent]) -> None:
        for client_order_id, intent in intents.items():
            if intent.ledger_kind != LedgerKind.LIVE:
                raise OrderManagerError(
                    f"refuse loading non-live intent client_order_id={client_order_id!r}"
                )
            self._intents[client_order_id] = intent

    def _has_missing(self) -> bool:
        return any(o.status == OrderStatus.MISSING for o in self._orders.values())

    def _recompute_critical_flags(self) -> None:
        unknown = any(o.status == OrderStatus.UNKNOWN for o in self._orders.values())
        missing = self._has_missing()
        self._critical_unknown = unknown
        # MISSING after ambiguous submit must not silently reopen new order flow.
        if unknown or missing:
            self._block_new_submits = True
        else:
            self._block_new_submits = False

    def _assert_decision_binds_intent(
        self, decision: RiskDecision, intent: OrderIntent
    ) -> None:
        if decision.proposal_id != intent.proposal_id:
            raise OrderBindError(
                "RiskDecision.proposal_id does not match OrderIntent.proposal_id"
            )
        if decision.correlation_id != intent.risk_decision_correlation_id:
            raise OrderBindError(
                "RiskDecision.correlation_id does not match "
                "OrderIntent.risk_decision_correlation_id"
            )

    def _assert_live_path_allowed(self, intent: OrderIntent) -> None:
        if intent.ledger_kind != LedgerKind.LIVE:
            raise OrderManagerError(
                f"OrderManager refuses ledger_kind={intent.ledger_kind.value!r}"
            )
        if self._settings.kill_switch or not trading_ready(self._settings):
            raise OrderSubmitBlocked("kill_switch or trading_ready blocks new live orders")
        assert_live_execution_allowed(self._settings)
