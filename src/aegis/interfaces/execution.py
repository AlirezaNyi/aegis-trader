"""Execution port — Phase 1 never submits to a live exchange."""

from __future__ import annotations

from typing import Protocol

from aegis.schemas.orders import Order, OrderIntent


class ExecutionPort(Protocol):
    def submit(self, intent: OrderIntent) -> Order:
        """Submit an order intent to paper or live adapter."""

    def cancel(self, client_order_id: str) -> Order:
        """Cancel by client order id where supported."""

    def get_order(self, client_order_id: str) -> Order | None:
        """Query order state."""


class NullExecutionPort:
    """Refuses all execution I/O. Used until paper/live adapters exist."""

    def submit(self, intent: OrderIntent) -> Order:
        raise RuntimeError(
            "Execution is disabled in Phase 1. "
            f"Refusing submit for client_order_id={intent.client_order_id!r} "
            f"ledger_kind={intent.ledger_kind.value!r}."
        )

    def cancel(self, client_order_id: str) -> Order:
        raise RuntimeError(
            f"Execution is disabled in Phase 1. Refusing cancel {client_order_id!r}."
        )

    def get_order(self, client_order_id: str) -> Order | None:
        raise RuntimeError(
            f"Execution is disabled in Phase 1. Refusing query {client_order_id!r}."
        )
