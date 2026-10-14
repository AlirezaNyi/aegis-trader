"""Restart recovery — reconcile open/unknown intents before new submits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from aegis.orders.manager import OrderManager
from aegis.orders.persist import LiveOrderLoadError
from aegis.reconcile.service import Reconciler, ReconcileResult, ids_requiring_reconcile
from aegis.schemas.orders import OrderIntent, OrderStatus


@dataclass
class RestartRecoveryResult:
    ok: bool
    reconciled: list[ReconcileResult] = field(default_factory=list)
    blocked: bool = False
    detail: str = ""
    errors: dict[str, str] = field(default_factory=dict)


async def recover_open_intents(
    manager: OrderManager,
    reconciler: Reconciler,
    *,
    intents: dict[str, OrderIntent] | None = None,
    load_error: LiveOrderLoadError | None = None,
    now: datetime | None = None,
) -> RestartRecoveryResult:
    """Load open intents, reconcile non-terminal / UNKNOWN before allowing submits.

    Runs under ``manager.lock`` so no submit can interleave. A failed query keeps
    that order's state (UNKNOWN stays blocking), is reported in ``errors`` and sets
    the manager's sticky reconcile-incomplete block (cleared only by a later fully
    successful sweep / recovery). ``load_error`` (from ``load_live_orders``) marks
    persisted live state as unloadable: new submits stay blocked for this process.
    """
    clock = now if now is not None else datetime.now(tz=UTC)
    async with manager.lock:
        if load_error is not None:
            manager.mark_state_load_failed(load_error.failed_client_order_ids)
        if intents:
            manager.load_intents(intents)

        ids = ids_requiring_reconcile(manager)
        # Also reconcile intents that have no local order yet (crash mid-submit)
        remember = getattr(manager.execution, "remember_intent", None)
        for client_order_id, intent in list(manager.intents.items()):
            if client_order_id in manager.orders:
                continue
            if client_order_id in ids:
                continue
            if callable(remember):
                remember(intent)
            ids.append(client_order_id)

        batch = await reconciler.reconcile_ids(manager, ids, now=clock)

        if not batch.errors:
            # Only a fully answered recovery may lift blocks; MISSING still blocks,
            # and an unloadable persisted row keeps the state-load block.
            manager.clear_reconcile_incomplete(ids)
            manager.allow_new_submits()
        blocked = manager.block_new_submits
        unknown_left = any(
            o.status == OrderStatus.UNKNOWN for o in manager.orders.values()
        )
    ok = (
        not unknown_left
        and not blocked
        and not batch.errors
        and not manager.state_load_failed
    )
    return RestartRecoveryResult(
        ok=ok,
        reconciled=batch.results,
        blocked=blocked,
        errors=dict(batch.errors),
        detail=(
            "restart recovery complete"
            if ok
            else (
                "persisted live order state unloadable; new submits blocked"
                if manager.state_load_failed
                else "critical unknown / reconcile incomplete; new submits blocked"
            )
        ),
    )
