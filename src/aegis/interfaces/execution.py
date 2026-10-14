"""Execution port — Null by default; live/paper adapters implement submit."""

from __future__ import annotations

from typing import Protocol

from aegis.schemas.orders import Order, OrderIntent


class ExecutionPort(Protocol):
    async def submit(self, intent: OrderIntent) -> Order:
        """Submit an order intent to paper or live adapter."""

    async def cancel(self, client_order_id: str) -> Order:
        """Cancel by client order id where supported."""

    async def get_order(self, client_order_id: str) -> Order | None:
        """Query order state."""


class NullExecutionPort:
    """Refuses all execution I/O. Default until a live/paper port is wired."""

    async def submit(self, intent: OrderIntent) -> Order:
        raise RuntimeError(
            "Execution port is null (live client not constructed). "
            f"Refusing submit for client_order_id={intent.client_order_id!r} "
            f"ledger_kind={intent.ledger_kind.value!r}."
        )

    async def cancel(self, client_order_id: str) -> Order:
        raise RuntimeError(
            f"Execution port is null. Refusing cancel {client_order_id!r}."
        )

    async def get_order(self, client_order_id: str) -> Order | None:
        raise RuntimeError(
            f"Execution port is null. Refusing query {client_order_id!r}."
        )

    async def aclose(self) -> None:
        """Nothing to close (no client is ever constructed)."""
