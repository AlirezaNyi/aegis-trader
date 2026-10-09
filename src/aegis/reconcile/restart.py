"""Restart recovery — reconcile open/unknown intents before new submits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from aegis.orders.idempotency import is_terminal
from aegis.orders.manager import OrderManager
from aegis.reconcile.service import Reconciler, ReconcileResult
from aegis.schemas.orders import OrderIntent, OrderStatus


@dataclass
class RestartRecoveryResult:
    ok: bool
    reconciled: list[ReconcileResult] = field(default_factory=list)
    blocked: bool = False
    detail: str = ""


def recover_open_intents(
    manager: OrderManager,
    reconciler: Reconciler,
    *,
    intents: dict[str, OrderIntent] | None = None,
    now: datetime | None = None,
) -> RestartRecoveryResult:
    """Load open intents, reconcile non-terminal / UNKNOWN before allowing submits."""
    clock = now if now is not None else datetime.now(tz=UTC)
    if intents:
        manager.load_intents(intents)

    results: list[ReconcileResult] = []
    for client_order_id, order in list(manager.orders.items()):
        if order.status == OrderStatus.UNKNOWN or not is_terminal(order.status):
            results.append(
                reconciler.query_and_update(manager, client_order_id, now=clock)
            )

    # Also reconcile intents that have no local order yet (crash mid-submit)
    for client_order_id, intent in list(manager.intents.items()):
        if client_order_id in manager.orders:
            continue
        remember = getattr(manager._execution, "remember_intent", None)
        if callable(remember):
            remember(intent)
        results.append(reconciler.query_and_update(manager, client_order_id, now=clock))

    manager.allow_new_submits()
    blocked = manager.block_new_submits
    unknown_left = any(o.status == OrderStatus.UNKNOWN for o in manager.orders.values())
    ok = not unknown_left and not blocked
    return RestartRecoveryResult(
        ok=ok,
        reconciled=results,
        blocked=blocked,
        detail=(
            "restart recovery complete"
            if ok
            else "critical unknown remains; new submits blocked"
        ),
    )
