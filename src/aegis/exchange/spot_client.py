"""Toobit Spot execution client — credential plane only."""

from __future__ import annotations

from typing import Any

import httpx

from aegis.exchange._http import (
    ensure_live_intent,
    merge_query_into_order,
    order_from_exchange_payload,
    signed_form_request,
)
from aegis.exchange.errors import ExchangeError
from aegis.exchange.paths import REST_BASE_URL, VERIFIED_QUERY_PATHS, VERIFIED_SUBMIT_PATHS
from aegis.exchange.side_map import spot_side
from aegis.schemas.common import MarketType
from aegis.schemas.orders import Order, OrderIntent


class ToobitSpotExecutionClient:
    """SIGNED Spot submit / query / cancel via verified `/api/v1/spot/*` paths."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        client: httpx.Client | None = None,
        base_url: str = REST_BASE_URL,
        recv_window_ms: int = 5000,
        timeout_seconds: float = 10.0,
        use_order_test: bool = False,
    ) -> None:
        if not api_key.strip() or not api_secret.strip():
            raise ExchangeError("Toobit Spot client requires api_key and api_secret")
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._recv_window_ms = recv_window_ms
        self._use_order_test = use_order_test
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )
        self._intents: dict[str, OrderIntent] = {}
        self._orders: dict[str, Order] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def remember_intent(self, intent: OrderIntent) -> None:
        """Allow reconcile after ambiguous submit before ack is stored locally."""
        self._intents[intent.client_order_id] = intent

    def submit(self, intent: OrderIntent) -> Order:
        ensure_live_intent(intent)
        if intent.instrument.market_type != MarketType.SPOT:
            raise ExchangeError(
                f"Spot client refuses market_type={intent.instrument.market_type.value!r}"
            )
        path = (
            VERIFIED_SUBMIT_PATHS["spot_order_test"]
            if self._use_order_test
            else VERIFIED_SUBMIT_PATHS["spot_order"]
        )
        params: dict[str, Any] = {
            "symbol": intent.instrument.symbol,
            "side": spot_side(intent.side),
            "type": intent.order_type.upper(),
            "quantity": str(intent.quantity),
            "newClientOrderId": intent.client_order_id,
        }
        if intent.price is not None and intent.order_type.upper() != "MARKET":
            params["price"] = str(intent.price)
        if intent.time_in_force:
            params["timeInForce"] = intent.time_in_force

        self.remember_intent(intent)
        response = signed_form_request(
            self._client,
            method="POST",
            path=path,
            api_key=self._api_key,
            api_secret=self._api_secret,
            params=params,
            recv_window_ms=self._recv_window_ms,
            client_order_id=intent.client_order_id,
        )
        if response.status_code >= 400:
            raise ExchangeError(
                f"spot submit HTTP {response.status_code} "
                f"client_order_id={intent.client_order_id!r}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExchangeError("spot submit response must be an object")
        order = order_from_exchange_payload(payload, intent)
        self._orders[intent.client_order_id] = order
        return order

    def cancel(self, client_order_id: str) -> Order:
        path = VERIFIED_QUERY_PATHS["spot_cancel_order"]
        response = signed_form_request(
            self._client,
            method="DELETE",
            path=path,
            api_key=self._api_key,
            api_secret=self._api_secret,
            params={"clientOrderId": client_order_id},
            recv_window_ms=self._recv_window_ms,
        )
        if response.status_code >= 400:
            raise ExchangeError(
                f"spot cancel HTTP {response.status_code} "
                f"client_order_id={client_order_id!r}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExchangeError("spot cancel response must be an object")
        return self._apply_payload(client_order_id, payload)

    def get_order(self, client_order_id: str) -> Order | None:
        path = VERIFIED_QUERY_PATHS["spot_get_order"]
        response = signed_form_request(
            self._client,
            method="GET",
            path=path,
            api_key=self._api_key,
            api_secret=self._api_secret,
            params={"origClientOrderId": client_order_id},
            recv_window_ms=self._recv_window_ms,
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                body = None
            code = body.get("code") if isinstance(body, dict) else None
            if code in (-2013, 2013, "-2013"):
                return None
            raise ExchangeError(
                f"spot get_order HTTP {response.status_code} "
                f"client_order_id={client_order_id!r}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExchangeError("spot get_order response must be an object")
        return self._apply_payload(client_order_id, payload)

    def _apply_payload(self, client_order_id: str, payload: dict[str, Any]) -> Order:
        existing = self._orders.get(client_order_id)
        intent = self._intents.get(client_order_id)
        if existing is not None:
            order = merge_query_into_order(existing, payload)
        elif intent is not None:
            order = order_from_exchange_payload(payload, intent)
        else:
            raise ExchangeError(
                f"no local intent/order for client_order_id={client_order_id!r}"
            )
        self._orders[client_order_id] = order
        return order
