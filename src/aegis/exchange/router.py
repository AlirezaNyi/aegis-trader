"""Route live intents to Spot or Futures clients by instrument.market_type."""

from __future__ import annotations

from aegis.exchange.errors import ExchangeError
from aegis.exchange.futures_client import ToobitFuturesExecutionClient
from aegis.exchange.spot_client import ToobitSpotExecutionClient
from aegis.schemas.common import MarketType
from aegis.schemas.orders import Order, OrderIntent


class ToobitExecutionRouter:
    """ExecutionPort that selects Spot vs Futures by market type."""

    def __init__(
        self,
        *,
        spot: ToobitSpotExecutionClient,
        futures: ToobitFuturesExecutionClient,
    ) -> None:
        self._spot = spot
        self._futures = futures

    def submit(self, intent: OrderIntent) -> Order:
        return self._client_for(intent.instrument.market_type).submit(intent)

    def cancel(self, client_order_id: str) -> Order:
        # Prefer whichever client knows the intent; Spot first then Futures.
        if client_order_id in self._spot._intents or client_order_id in self._spot._orders:
            return self._spot.cancel(client_order_id)
        if (
            client_order_id in self._futures._intents
            or client_order_id in self._futures._orders
        ):
            return self._futures.cancel(client_order_id)
        raise ExchangeError(
            f"router cancel: unknown client_order_id={client_order_id!r}"
        )

    def get_order(self, client_order_id: str) -> Order | None:
        if client_order_id in self._spot._intents or client_order_id in self._spot._orders:
            return self._spot.get_order(client_order_id)
        if (
            client_order_id in self._futures._intents
            or client_order_id in self._futures._orders
        ):
            return self._futures.get_order(client_order_id)
        # Ambiguous restart: try spot then futures
        order = self._spot.get_order(client_order_id)
        if order is not None:
            return order
        return self._futures.get_order(client_order_id)

    def remember_intent(self, intent: OrderIntent) -> None:
        client = self._client_for(intent.instrument.market_type)
        client.remember_intent(intent)

    def close(self) -> None:
        self._spot.close()
        self._futures.close()

    def _client_for(
        self, market_type: MarketType
    ) -> ToobitSpotExecutionClient | ToobitFuturesExecutionClient:
        if market_type == MarketType.SPOT:
            return self._spot
        if market_type == MarketType.FUTURES:
            return self._futures
        raise ExchangeError(f"unsupported market_type={market_type!r}")
