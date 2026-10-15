"""Order Manager — live submit after Risk APPROVE; never blind-retry."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from aegis.config.settings import Settings
from aegis.exchange.errors import (
    AmbiguousSubmitError,
    ExchangeRejectError,
    RateLimitError,
    is_definitive_submit_reject,
)
from aegis.guards.live import assert_live_execution_allowed, trading_ready
from aegis.interfaces.execution import ExecutionPort
from aegis.logging import get_logger
from aegis.orders.idempotency import (
    IdempotencyError,
    SubmitAttemptTracker,
    blocks_resubmit,
    is_terminal,
)
from aegis.risk.handoff import (
    IntentBindingError,
    assert_intent_matches_decision,
    assert_may_submit_to_order_manager,
)
from aegis.schemas.common import LedgerKind
from aegis.schemas.orders import Order, OrderIntent, OrderStatus
from aegis.schemas.risk import RiskDecision

logger = get_logger("aegis.orders.manager")


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
        # Sticky: client_order_ids whose reconcile query failed. Cleared only by
        # ``clear_reconcile_incomplete`` after a fully successful reconcile batch
        # that covered every one of them (restart recovery or sweep) — never by
        # ``_recompute_critical_flags`` / cancel / apply_exchange_order.
        self._reconcile_pending: set[str] = set()
        # Sticky for the process lifetime: persisted live order rows that could not
        # be parsed at startup. Fix the rows and restart; there is no clear method.
        self._state_load_failed: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def orders(self) -> dict[str, Order]:
        return self._orders

    @property
    def intents(self) -> dict[str, OrderIntent]:
        return self._intents

    @property
    def execution(self) -> ExecutionPort:
        return self._execution

    @property
    def critical_unknown(self) -> bool:
        return self._critical_unknown

    @property
    def reconcile_incomplete(self) -> bool:
        return bool(self._reconcile_pending)

    @property
    def reconcile_pending_ids(self) -> frozenset[str]:
        return frozenset(self._reconcile_pending)

    @property
    def state_load_failed(self) -> bool:
        return bool(self._state_load_failed)

    @property
    def block_new_submits(self) -> bool:
        return (
            self._block_new_submits
            or self._critical_unknown
            or bool(self._reconcile_pending)
            or bool(self._state_load_failed)
        )

    def mark_reconcile_incomplete(self, client_order_ids: Iterable[str]) -> None:
        """Record failed reconcile queries; blocks new submits until a full
        successful reconcile covers every recorded id."""
        ids = [coid for coid in client_order_ids]
        if not ids:
            return
        self._reconcile_pending.update(ids)
        logger.warning(
            "reconcile_incomplete pending=%s",
            len(self._reconcile_pending),
            extra={"event": "reconcile_incomplete", "component": "order_manager"},
        )

    def clear_reconcile_incomplete(self, covered_ids: Iterable[str]) -> bool:
        """Clear the sticky block after a fully successful reconcile batch.

        Callers (restart recovery, ``Reconciler.reconcile_open_orders``) must pass
        the ids the error-free batch queried. Clears only when that batch covered
        every pending id; returns whether the block is now clear.
        """
        covered = set(covered_ids)
        if self._reconcile_pending <= covered:
            self._reconcile_pending.clear()
        return not self._reconcile_pending

    def mark_state_load_failed(self, client_order_ids: Iterable[str]) -> None:
        """Persisted live state could not be loaded: block new submits for the
        rest of this process (no clear path; fix the rows and restart)."""
        ids = list(client_order_ids) or ["<unidentified>"]
        self._state_load_failed.update(ids)
        logger.error(
            "live_state_load_failed rows=%s",
            len(ids),
            extra={"event": "live_state_load_failed", "component": "order_manager"},
        )

    @property
    def lock(self) -> asyncio.Lock:
        """Serializes live submit / cancel / reconcile batches on this manager.

        ``submit`` holds it across the gate re-check, the idempotency check, the
        network call and the post-ambiguity reconcile, so a second concurrent submit
        can neither pass the idempotency check for the same ``client_order_id`` nor
        slip past ``block_new_submits`` while an earlier submit is still unresolved.
        Not re-entrant: ``Reconciler.query_and_update`` never acquires it.
        """
        return self._lock

    def set_reconciler(self, reconciler: object) -> None:
        self._reconciler = reconciler

    async def submit(
        self,
        decision: RiskDecision,
        intent: OrderIntent,
        *,
        now: datetime | None = None,
    ) -> Order:
        # Fast fail before waiting on the lock: a REJECT / unbound / gated decision
        # never queues behind other submits and never reaches the adapter (INV-01).
        self._precheck(decision, intent, now=now)
        async with self._lock:
            # Re-check everything under the lock: gates or blocks may have changed
            # while this call waited (kill switch, UNKNOWN from a prior submit).
            clock = self._precheck(decision, intent, now=now)
            return await self._submit_locked(intent, clock)

    def _precheck(
        self,
        decision: RiskDecision,
        intent: OrderIntent,
        *,
        now: datetime | None,
    ) -> datetime:
        clock = now if now is not None else datetime.now(tz=UTC)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=UTC)
        assert_may_submit_to_order_manager(decision, now=clock)
        self._assert_decision_binds_intent(decision, intent)
        self._assert_live_path_allowed(intent)
        return clock

    async def _submit_locked(self, intent: OrderIntent, clock: datetime) -> Order:
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
        # Placeholder + attempt mark are written before the network call.
        self._orders[intent.client_order_id] = placeholder
        self._tracker.mark_attempted(intent.client_order_id)

        try:
            order = await self._execution.submit(intent)
        except asyncio.CancelledError:
            # Cancelled mid-flight: the request may have reached the exchange.
            self._mark_unknown(placeholder, clock)
            self._log_ambiguous_submit(intent.client_order_id, "CancelledError")
            raise
        except AmbiguousSubmitError as exc:
            # INV-04: timeout / 5xx / 429 / bodyless 4xx / execution-unknown or
            # unparseable code → UNKNOWN → reconcile.
            self._log_ambiguous_submit(intent.client_order_id, type(exc).__name__, exc)
            return await self._mark_unknown_and_reconcile(placeholder, clock)
        except RateLimitError as exc:
            # HTTP 429 on submit does not prove the order was refused: ambiguous.
            self._log_ambiguous_submit(intent.client_order_id, type(exc).__name__, exc)
            return await self._mark_unknown_and_reconcile(placeholder, clock)
        except ExchangeRejectError as exc:
            if not is_definitive_submit_reject(exc):
                # e.g. 4xx without a parseable Toobit code: never assume not-on-book.
                self._log_ambiguous_submit(intent.client_order_id, type(exc).__name__, exc)
                return await self._mark_unknown_and_reconcile(placeholder, clock)
            # Definitive reject (parseable non-ambiguous exchange code, or a local
            # pre-submit guard): not on the book. Terminal REJECTED; does not block
            # later submits and is never retried automatically.
            rejected = placeholder.model_copy(
                update={"status": OrderStatus.REJECTED, "updated_at": clock}
            )
            self._orders[intent.client_order_id] = rejected
            logger.warning(
                "live_submit_rejected client_order_id=%s err=%s code=%s http=%s",
                intent.client_order_id,
                type(exc).__name__,
                exc.code,
                exc.http_status,
                extra={"event": "live_submit_rejected", "component": "order_manager"},
            )
            return rejected
        except Exception as exc:  # noqa: BLE001 — safety boundary: any other post-attempt failure is ambiguous capital risk → UNKNOWN + block + reconcile, never crash or resubmit
            self._log_ambiguous_submit(intent.client_order_id, type(exc).__name__)
            return await self._mark_unknown_and_reconcile(placeholder, clock)

        if order.ledger_kind != LedgerKind.LIVE:
            raise OrderManagerError(
                f"execution returned non-live ledger_kind={order.ledger_kind.value!r}"
            )
        self._orders[intent.client_order_id] = order
        if order.status == OrderStatus.UNKNOWN:
            # Ack without a recognised status: treat like an ambiguous submit.
            return await self._mark_unknown_and_reconcile(order, clock)
        return order

    @staticmethod
    def _log_ambiguous_submit(
        client_order_id: str, err: str, exc: Exception | None = None
    ) -> None:
        logger.warning(
            "live_submit_ambiguous client_order_id=%s err=%s code=%s http=%s",
            client_order_id,
            err,
            getattr(exc, "code", None),
            getattr(exc, "http_status", None),
            extra={"event": "live_submit_ambiguous", "component": "order_manager"},
        )

    def _mark_unknown(self, base: Order, clock: datetime) -> None:
        self._orders[base.client_order_id] = base.model_copy(
            update={"status": OrderStatus.UNKNOWN, "updated_at": clock}
        )
        self._critical_unknown = True
        self._block_new_submits = True

    async def _mark_unknown_and_reconcile(self, base: Order, clock: datetime) -> Order:
        client_order_id = base.client_order_id
        self._mark_unknown(base, clock)
        if self._reconciler is not None:
            query = getattr(self._reconciler, "query_and_update", None)
            if callable(query):
                try:
                    await query(self, client_order_id, now=clock)
                except Exception as exc:  # noqa: BLE001 — safety boundary: reconcile failure keeps the order UNKNOWN and blocks
                    logger.warning(
                        "reconcile_after_submit_failed client_order_id=%s err=%s",
                        client_order_id,
                        type(exc).__name__,
                        extra={
                            "event": "reconcile_after_submit_failed",
                            "component": "order_manager",
                        },
                    )
                    current = self._orders.get(client_order_id)
                    if current is None or current.status != OrderStatus.UNKNOWN:
                        self._orders[client_order_id] = base.model_copy(
                            update={"status": OrderStatus.UNKNOWN, "updated_at": clock}
                        )
                    self._critical_unknown = True
                    self._block_new_submits = True
                    self.mark_reconcile_incomplete([client_order_id])
        # Never second submit — return current local state
        return self._orders[client_order_id]

    async def cancel(self, client_order_id: str, *, now: datetime | None = None) -> Order:
        async with self._lock:
            clock = now if now is not None else datetime.now(tz=UTC)
            order = await self._execution.cancel(client_order_id)
            updated = order.model_copy(update={"updated_at": clock})
            self._orders[client_order_id] = updated
            # An UNKNOWN (or MISSING) cancel ack must block new submits until reconciled.
            self._recompute_critical_flags()
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
        try:
            assert_intent_matches_decision(decision, intent)
        except IntentBindingError as exc:
            raise OrderBindError(str(exc)) from exc

    def _assert_live_path_allowed(self, intent: OrderIntent) -> None:
        if intent.ledger_kind != LedgerKind.LIVE:
            raise OrderManagerError(
                f"OrderManager refuses ledger_kind={intent.ledger_kind.value!r}"
            )
        if self._settings.kill_switch or not trading_ready(self._settings):
            raise OrderSubmitBlocked("kill_switch or trading_ready blocks new live orders")
        assert_live_execution_allowed(self._settings)
