"""Live reconciler — exchange state is authoritative after query by clientOrderId."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aegis.interfaces.execution import ExecutionPort
from aegis.orders.manager import OrderManager
from aegis.schemas.orders import Order, OrderStatus


@dataclass(frozen=True)
class ReconcileResult:
    client_order_id: str
    found: bool
    status: OrderStatus
    order: Order | None
    detail: str


class Reconciler:
    """Query exchange by client_order_id and update OrderManager local state."""

    def __init__(self, execution: ExecutionPort) -> None:
        self._execution = execution

    def query_and_update(
        self,
        manager: OrderManager,
        client_order_id: str,
        *,
        now: datetime | None = None,
    ) -> ReconcileResult:
        clock = now if now is not None else datetime.now(tz=UTC)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=UTC)

        exchange_order = self._execution.get_order(client_order_id)
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
