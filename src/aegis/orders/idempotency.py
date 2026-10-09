"""One submit attempt per client_order_id until terminal or reconciled."""

from __future__ import annotations

from aegis.schemas.orders import OrderStatus

TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
        OrderStatus.FAILED_TERMINAL,
        OrderStatus.MISSING,
    }
)

# Statuses that mean a live submit was already attempted (no second submit)
SUBMIT_ATTEMPTED_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.SUBMIT_ATTEMPTED,
        OrderStatus.PENDING_NEW,
        OrderStatus.OPEN,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.PENDING_CANCEL,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
        OrderStatus.UNKNOWN,
        OrderStatus.MISSING,
        OrderStatus.FAILED_TERMINAL,
    }
)


class IdempotencyError(RuntimeError):
    """Raised when a second submit is refused for the same client_order_id."""


class SubmitAttemptTracker:
    """Tracks client_order_ids that have already had a submit attempt."""

    def __init__(self) -> None:
        self._attempted: set[str] = set()

    def mark_attempted(self, client_order_id: str) -> None:
        self._attempted.add(client_order_id)

    def was_attempted(self, client_order_id: str) -> bool:
        return client_order_id in self._attempted

    def ensure_first_submit(self, client_order_id: str) -> None:
        if client_order_id in self._attempted:
            raise IdempotencyError(
                f"submit already attempted for client_order_id={client_order_id!r}; "
                "reconcile instead of resubmitting"
            )

    def clear(self, client_order_id: str) -> None:
        self._attempted.discard(client_order_id)


def is_terminal(status: OrderStatus) -> bool:
    return status in TERMINAL_STATUSES


def blocks_resubmit(status: OrderStatus) -> bool:
    return status in SUBMIT_ATTEMPTED_STATUSES
