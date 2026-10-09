"""Map verified Toobit exchange order statuses onto local OrderStatus."""

from __future__ import annotations

from aegis.schemas.orders import OrderStatus

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


def map_exchange_status(status: str) -> OrderStatus:
    key = status.strip().upper()
    mapped = _EXCHANGE_TO_LOCAL.get(key)
    if mapped is None:
        raise ValueError(f"unknown exchange order status: {status!r}")
    return mapped
