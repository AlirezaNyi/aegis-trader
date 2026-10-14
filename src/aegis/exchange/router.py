"""Route live intents to Spot or Futures clients by instrument.market_type."""

from __future__ import annotations

from aegis.exchange.errors import ExchangeError, ExchangeRejectError
from aegis.exchange.futures_client import ToobitFuturesExecutionClient
from aegis.exchange.spot_client import ToobitSpotExecutionClient
from aegis.schemas.common import MarketType
from aegis.schemas.orders import Order, OrderIntent


class ToobitExecutionRouter:
    """ExecutionPort that selects Spot vs Futures by market type.

    ``futures=None`` disables Futures (e.g. Spot orderTest mode: no verified
    Futures orderTest exists); Futures intents are then refused before any I/O.
    """

    def __init__(
        self,
        *,
        spot: ToobitSpotExecutionClient,
        futures: ToobitFuturesExecutionClient | None,
    ) -> None:
        self._spot = spot
        self._futures = futures

    async def submit(self, intent: OrderIntent) -> Order:
        return await self._client_for(intent.instrument.market_type).submit(intent)

    async def cancel(self, client_order_id: str) -> Order:
        # Prefer whichever client knows the intent; Spot first then Futures.
        if self._spot.knows(client_order_id):
            return await self._spot.cancel(client_order_id)
        if self._futures is not None and self._futures.knows(client_order_id):
            return await self._futures.cancel(client_order_id)
        raise ExchangeError(
            f"router cancel: unknown client_order_id={client_order_id!r}"
        )

    async def get_order(self, client_order_id: str) -> Order | None:
        if self._spot.knows(client_order_id):
            return await self._spot.get_order(client_order_id)
        if self._futures is not None and self._futures.knows(client_order_id):
            return await self._futures.get_order(client_order_id)
        # Ambiguous restart: try spot then futures
        order = await self._spot.get_order(client_order_id)
        if order is not None or self._futures is None:
            return order
        return await self._futures.get_order(client_order_id)

    def remember_intent(self, intent: OrderIntent) -> None:
        client = self._client_for(intent.instrument.market_type)
        client.remember_intent(intent)

    async def aclose(self) -> None:
        try:
            await self._spot.aclose()
        finally:
            if self._futures is not None:
                await self._futures.aclose()

    async def __aenter__(self) -> ToobitExecutionRouter:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _client_for(
        self, market_type: MarketType
    ) -> ToobitSpotExecutionClient | ToobitFuturesExecutionClient:
        if market_type == MarketType.SPOT:
            return self._spot
        if market_type == MarketType.FUTURES:
            if self._futures is None:
                raise ExchangeRejectError(
                    "futures execution disabled for this router (order-test mode)"
                )
            return self._futures
        raise ExchangeError(f"unsupported market_type={market_type!r}")
