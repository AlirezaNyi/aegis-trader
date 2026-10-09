"""Toobit Futures v2 execution client — credential plane only."""

from __future__ import annotations

from typing import Any

import httpx

from aegis.exchange._http import (
    ensure_live_intent,
    merge_query_into_order,
    order_from_exchange_payload,
    signed_v2_json_post,
    signed_v2_query,
)
from aegis.exchange.errors import ExchangeError
from aegis.exchange.paths import REST_BASE_URL, VERIFIED_QUERY_PATHS, VERIFIED_SUBMIT_PATHS
from aegis.exchange.side_map import futures_side_and_position
from aegis.schemas.common import MarketType
from aegis.schemas.orders import Order, OrderIntent


class ToobitFuturesExecutionClient:
    """SIGNED Futures submit / query / cancel via verified `/api/v2/futures/*` paths."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        client: httpx.Client | None = None,
        base_url: str = REST_BASE_URL,
        recv_window_ms: int = 5000,
        timeout_seconds: float = 10.0,
        category: str = "USDT",
    ) -> None:
        if not api_key.strip() or not api_secret.strip():
            raise ExchangeError("Toobit Futures client requires api_key and api_secret")
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._recv_window_ms = recv_window_ms
        self._category = category
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
        self._intents[intent.client_order_id] = intent

    def submit(self, intent: OrderIntent) -> Order:
        ensure_live_intent(intent)
        if intent.instrument.market_type != MarketType.FUTURES:
            raise ExchangeError(
                f"Futures client refuses market_type={intent.instrument.market_type.value!r}"
            )
        if not intent.client_order_id.strip():
            raise ExchangeError("Futures v2 requires mandatory newClientOrderId")

        side, position_side = futures_side_and_position(intent)
        body: dict[str, Any] = {
            "symbol": intent.instrument.symbol,
            "side": side,
            "positionSide": position_side,
            "type": intent.order_type.upper(),
            "newClientOrderId": intent.client_order_id,
            "quantity": str(intent.quantity),
        }
        if intent.price is not None and intent.order_type.upper() == "LIMIT":
            body["price"] = str(intent.price)
        if intent.time_in_force:
            body["timeInForce"] = intent.time_in_force

        self.remember_intent(intent)
        path = VERIFIED_SUBMIT_PATHS["futures_order"]
        response = signed_v2_json_post(
            self._client,
            path=path,
            api_key=self._api_key,
            api_secret=self._api_secret,
            body=body,
            recv_window_ms=self._recv_window_ms,
            category=self._category,
            client_order_id=intent.client_order_id,
        )
        if response.status_code >= 400:
            raise ExchangeError(
                f"futures submit HTTP {response.status_code} "
                f"client_order_id={intent.client_order_id!r}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExchangeError("futures submit response must be an object")
        if payload.get("code") not in (200, "200", None) and "data" not in payload:
            # Allow flat payloads in mocks; require success code when present without data
            if "status" not in payload and payload.get("code") not in (0, "0"):
                raise ExchangeError(
                    f"futures submit rejected code={payload.get('code')!r} "
                    f"client_order_id={intent.client_order_id!r}"
                )
        order = order_from_exchange_payload(payload, intent)
        self._orders[intent.client_order_id] = order
        return order

    def cancel(self, client_order_id: str) -> Order:
        path = VERIFIED_QUERY_PATHS["futures_cancel_order"]
        response = signed_v2_query(
            self._client,
            method="DELETE",
            path=path,
            api_key=self._api_key,
            api_secret=self._api_secret,
            params={
                "origClientOrderId": client_order_id,
                "category": self._category,
            },
            recv_window_ms=self._recv_window_ms,
        )
        if response.status_code >= 400:
            raise ExchangeError(
                f"futures cancel HTTP {response.status_code} "
                f"client_order_id={client_order_id!r}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExchangeError("futures cancel response must be an object")
        return self._apply_payload(client_order_id, payload)

    def get_order(self, client_order_id: str) -> Order | None:
        path = VERIFIED_QUERY_PATHS["futures_get_order"]
        response = signed_v2_query(
            self._client,
            method="GET",
            path=path,
            api_key=self._api_key,
            api_secret=self._api_secret,
            params={
                "origClientOrderId": client_order_id,
                "category": self._category,
            },
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
                f"futures get_order HTTP {response.status_code} "
                f"client_order_id={client_order_id!r}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExchangeError("futures get_order response must be an object")
        # Empty / not-found style success envelopes
        if payload.get("data") is None and "status" not in payload:
            code = payload.get("code")
            if code not in (200, "200", 0, "0", None):
                return None
            if "orderId" not in payload and "clientOrderId" not in payload:
                return None
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
