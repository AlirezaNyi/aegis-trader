"""Order Manager — live execution after Risk APPROVE (Phase 7)."""

from __future__ import annotations

from aegis.orders.idempotency import IdempotencyError, SubmitAttemptTracker
from aegis.orders.manager import (
    OrderBindError,
    OrderManager,
    OrderManagerError,
    OrderSubmitBlocked,
)
from aegis.orders.mapping import futures_side_and_position, spot_side

PHASE = 7

__all__ = [
    "PHASE",
    "IdempotencyError",
    "SubmitAttemptTracker",
    "OrderBindError",
    "OrderManager",
    "OrderManagerError",
    "OrderSubmitBlocked",
    "futures_side_and_position",
    "spot_side",
]
