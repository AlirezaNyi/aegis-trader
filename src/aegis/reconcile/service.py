"""Live reconciler — exchange state is authoritative after query by clientOrderId.

Only the verified per-order query (``ExecutionPort.get_order``) is used. Account-level
inventory endpoints (open orders / balances / positions) remain UNVERIFIED and are not
called here (see ``LIMITATIONS.md``). Nothing in this module ever submits an order.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from aegis.interfaces.execution import ExecutionPort
from aegis.logging import get_logger
from aegis.ops.metrics import OpsMetrics
from aegis.ops.shutdown import ShutdownGate
from aegis.orders.idempotency import is_terminal
from aegis.orders.manager import OrderManager
from aegis.schemas.orders import Order, OrderStatus

logger = get_logger("aegis.reconcile.service")

# Engineering defaults (NOT risk policy): bound concurrent signed get_order calls so
# a sweep cannot burst the exchange rate limit, and space sweeps out in time.
DEFAULT_RECONCILE_CONCURRENCY = 4
DEFAULT_SWEEP_INTERVAL_SECONDS = 30.0
DEFAULT_SWEEP_JITTER_SECONDS = 5.0


@dataclass(frozen=True)
class ReconcileResult:
    client_order_id: str
    found: bool
    status: OrderStatus
    order: Order | None
    detail: str


@dataclass(frozen=True)
class ReconcileBatchResult:
    """Outcome of one batch over every non-terminal / UNKNOWN live order.

    ``errors`` maps client_order_id → exception class name for queries that failed;
    those orders keep their prior local state (UNKNOWN stays UNKNOWN and blocking).
    ``missing`` lists ids the exchange reported as not found — reported only; the
    block they cause is never auto-cleared here (owner decision pending).
    """

    results: list[ReconcileResult] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    block_new_submits: bool = False

    @property
    def queried(self) -> int:
        return len(self.results) + len(self.errors)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing and not self.block_new_submits


def open_order_ids(manager: OrderManager) -> list[str]:
    """Client order ids whose local state is non-terminal or UNKNOWN."""
    return [
        client_order_id
        for client_order_id, order in manager.orders.items()
        if order.status == OrderStatus.UNKNOWN or not is_terminal(order.status)
    ]


def ids_requiring_reconcile(manager: OrderManager) -> list[str]:
    """Open/UNKNOWN orders plus every id whose earlier reconcile query failed.

    Pending ids that only have a local intent (crash mid-submit) are re-registered
    with the execution port so ``get_order`` can resolve them.
    """
    ids = open_order_ids(manager)
    remember = getattr(manager.execution, "remember_intent", None)
    for client_order_id in sorted(manager.reconcile_pending_ids):
        if client_order_id in ids:
            continue
        intent = manager.intents.get(client_order_id)
        if intent is not None and callable(remember):
            remember(intent)
        ids.append(client_order_id)
    return ids


class Reconciler:
    """Query exchange by client_order_id and update OrderManager local state."""

    def __init__(
        self,
        execution: ExecutionPort,
        *,
        concurrency: int = DEFAULT_RECONCILE_CONCURRENCY,
    ) -> None:
        if concurrency < 1:
            raise ValueError("reconcile concurrency must be >= 1")
        self._execution = execution
        self._concurrency = concurrency

    async def query_and_update(
        self,
        manager: OrderManager,
        client_order_id: str,
        *,
        now: datetime | None = None,
    ) -> ReconcileResult:
        """Single verified ``get_order`` + apply. Does not take ``manager.lock``.

        The Order Manager calls this while already holding its lock (after an
        ambiguous submit); other callers should hold ``manager.lock`` or use
        ``reconcile_open_orders``. Exceptions propagate to the caller.
        """
        clock = _utc(now)
        exchange_order = await self._execution.get_order(client_order_id)
        updated = manager.apply_exchange_order(
            client_order_id, exchange_order, now=clock
        )
        if exchange_order is None:
            return ReconcileResult(
                client_order_id=client_order_id,
                found=False,
                status=OrderStatus.MISSING,
                order=updated,
                detail="exchange get_order returned none",
            )
        return ReconcileResult(
            client_order_id=client_order_id,
            found=True,
            status=updated.status,
            order=updated,
            detail=f"reconciled to status={updated.status.value}",
        )

    async def reconcile_ids(
        self,
        manager: OrderManager,
        client_order_ids: list[str],
        *,
        now: datetime | None = None,
    ) -> ReconcileBatchResult:
        """Query each id with bounded concurrency; one failure never aborts others.

        Caller must hold ``manager.lock`` (``reconcile_open_orders`` and restart
        recovery do). Never submits.
        """
        clock = _utc(now)
        semaphore = asyncio.Semaphore(self._concurrency)
        results: dict[str, ReconcileResult] = {}
        errors: dict[str, str] = {}

        async def _one(client_order_id: str) -> None:
            async with semaphore:
                try:
                    results[client_order_id] = await self.query_and_update(
                        manager, client_order_id, now=clock
                    )
                except Exception as exc:  # noqa: BLE001 — per-order isolation boundary: a failed query keeps prior state (UNKNOWN stays blocking) and must not abort the batch
                    errors[client_order_id] = type(exc).__name__
                    logger.warning(
                        "reconcile_query_failed client_order_id=%s err=%s",
                        client_order_id,
                        type(exc).__name__,
                        extra={"event": "reconcile_query_failed", "component": "reconciler"},
                    )

        unique = list(dict.fromkeys(client_order_ids))
        await asyncio.gather(*(_one(coid) for coid in unique))
        if errors:
            # Sticky: only a later fully successful batch covering these ids clears it.
            manager.mark_reconcile_incomplete(errors)
        ordered = [results[coid] for coid in unique if coid in results]
        missing = [r.client_order_id for r in ordered if not r.found]
        return ReconcileBatchResult(
            results=ordered,
            errors=errors,
            missing=missing,
            block_new_submits=manager.block_new_submits,
        )

    async def reconcile_open_orders(
        self,
        manager: OrderManager,
        *,
        now: datetime | None = None,
    ) -> ReconcileBatchResult:
        """Reconcile every non-terminal / UNKNOWN live order under ``manager.lock``.

        Holding the lock means no submit can run while the batch is in progress.
        Blocks caused by MISSING are reported, never auto-cleared. Ids whose earlier
        query failed are re-queried; the sticky reconcile-incomplete block is cleared
        only when this batch had no query errors at all.
        """
        async with manager.lock:
            ids = ids_requiring_reconcile(manager)
            batch = await self.reconcile_ids(manager, ids, now=now)
            if not batch.errors:
                manager.clear_reconcile_incomplete(ids)
            return ReconcileBatchResult(
                results=batch.results,
                errors=batch.errors,
                missing=batch.missing,
                block_new_submits=manager.block_new_submits,
            )


class ReconcileSweeper:
    """Periodic background reconcile of open/UNKNOWN live orders. Never submits.

    Interval and jitter are engineering defaults, not risk policy. Stops when the
    ``ShutdownGate`` begins shutdown or ``stop()`` is called. Metrics (when given)
    record ``reconcile_runs`` per sweep and ``reconcile_failures`` per failed query
    or failed sweep.
    """

    def __init__(
        self,
        reconciler: Reconciler,
        manager: OrderManager,
        *,
        shutdown: ShutdownGate,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
        jitter_seconds: float = DEFAULT_SWEEP_JITTER_SECONDS,
        metrics: OpsMetrics | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("sweep interval_seconds must be positive")
        if jitter_seconds < 0:
            raise ValueError("sweep jitter_seconds must be >= 0")
        self._reconciler = reconciler
        self._manager = manager
        self._shutdown = shutdown
        self._interval = interval_seconds
        self._jitter = jitter_seconds
        self._metrics = metrics
        self._rng = rng or random.Random()  # noqa: S311 — scheduling jitter, not crypto
        self._sleep = sleep
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.sweeps = 0
        self.failed_sweeps = 0
        self.last_result: ReconcileBatchResult | None = None

    @property
    def stopping(self) -> bool:
        return self._stop.is_set() or self._shutdown.shutting_down

    def next_delay(self) -> float:
        jitter = self._rng.uniform(0.0, self._jitter) if self._jitter > 0 else 0.0
        return self._interval + jitter

    async def run_once(self, *, now: datetime | None = None) -> ReconcileBatchResult | None:
        """One sweep. Returns None if stopping or the sweep itself failed."""
        if self.stopping:
            return None
        try:
            result = await self._reconciler.reconcile_open_orders(self._manager, now=now)
        except Exception as exc:  # noqa: BLE001 — background boundary: a failed sweep is logged and counted; the loop keeps running and blocks stay as they are
            self.failed_sweeps += 1
            if self._metrics is not None:
                self._metrics.reconcile_failures += 1
            logger.warning(
                "reconcile_sweep_failed err=%s",
                type(exc).__name__,
                extra={"event": "reconcile_sweep_failed", "component": "reconcile_sweeper"},
            )
            return None
        self.sweeps += 1
        self.last_result = result
        if self._metrics is not None:
            self._metrics.reconcile_runs += 1
            self._metrics.reconcile_failures += len(result.errors)
        logger.info(
            "reconcile_sweep queried=%s errors=%s missing=%s blocked=%s",
            result.queried,
            len(result.errors),
            len(result.missing),
            result.block_new_submits,
            extra={"event": "reconcile_sweep", "component": "reconcile_sweeper"},
        )
        return result

    async def run(self) -> None:
        """Loop until shutdown/stop: sweep, then wait interval + jitter."""
        while not self.stopping:
            await self.run_once()
            if self.stopping:
                break
            await self._wait(self.next_delay())

    async def _wait(self, delay: float) -> None:
        if self._sleep is not None:
            await self._sleep(delay)
            return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except TimeoutError:
            return

    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self.run(), name="aegis-reconcile-sweeper")
        return self._task

    def stop(self) -> None:
        self._stop.set()

    async def aclose(self) -> None:
        """Signal stop and wait for the background task to finish."""
        self.stop()
        task = self._task
        if task is not None and not task.done():
            await task


def _utc(now: datetime | None) -> datetime:
    clock = now if now is not None else datetime.now(tz=UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    return clock
