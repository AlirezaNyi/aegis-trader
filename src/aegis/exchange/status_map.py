"""Map verified Toobit exchange order statuses onto local OrderStatus."""

from __future__ import annotations

from aegis.logging import get_logger
from aegis.schemas.orders import OrderStatus

logger = get_logger("aegis.exchange.status_map")

# Verified exchange enums (docs/API_CONTRACTS.md §3.5)
_EXCHANGE_TO_LOCAL: dict[str, OrderStatus] = {
    "PENDING_NEW": OrderStatus.PENDING_NEW,
    "NEW": OrderStatus.OPEN,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "CANCELLED": OrderStatus.CANCELED,  # defensive spelling
    "PENDING_CANCEL": OrderStatus.PENDING_CANCEL,
    "REJECTED": OrderStatus.REJECTED,
}


def map_exchange_status(status: str | None) -> OrderStatus:
    """Map a Toobit status; unknown / missing values map to ``UNKNOWN`` (fail closed).

    ``UNKNOWN`` makes the Order Manager block new submits and reconcile, so an
    unrecognised exchange state is never treated as open, filled, or rejected.
    """
    key = "" if status is None else str(status).strip().upper()
    mapped = _EXCHANGE_TO_LOCAL.get(key)
    if mapped is None:
        logger.warning(
            "unknown_exchange_order_status status=%r",
            key[:32],
            extra={"event": "unknown_exchange_order_status", "component": "exchange"},
        )
        return OrderStatus.UNKNOWN
    return mapped
