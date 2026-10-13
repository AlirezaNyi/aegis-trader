"""Toobit Spot execution client — credential plane only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from aegis.exchange._http import (
    NO_SUCH_ORDER_CODE,
    classify_submit_response,
    empty_unknown_order,
    ensure_live_intent,
    merge_query_into_order,
    order_from_exchange_payload,
    parse_error_code,
    signed_form_request,
)
from aegis.exchange.errors import (
    ExchangeError,
    ExchangeRejectError,
    OrderTestRejectError,
    RateLimitError,
)
from aegis.exchange.paths import REST_BASE_URL, VERIFIED_QUERY_PATHS, VERIFIED_SUBMIT_PATHS
from aegis.exchange.precision import SymbolFilters, apply_spot_precision
from aegis.exchange.side_map import spot_side
from aegis.schemas.common import MarketType
from aegis.schemas.orders import Order, OrderIntent, OrderStatus

SPOT_ORDER_TEST_PATH = "/api/v1/spot/orderTest"


@dataclass(frozen=True)
class OrderTestResult:
    """Outcome of ``POST /api/v1/spot/orderTest`` (validation only; no order)."""

    http_status: int
    accepted: bool
    code: int | None
    msg: str | None


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

    def _validated_spot_intent(
        self,
        intent: OrderIntent,
        *,
        filters: SymbolFilters | None,
        reference_price: Decimal | None,
    ) -> OrderIntent:
        ensure_live_intent(intent)
        if intent.instrument.market_type != MarketType.SPOT:
            raise ExchangeError(
                f"Spot client refuses market_type={intent.instrument.market_type.value!r}"
            )
        if filters is None:
            return intent
        return apply_spot_precision(intent, filters, reference_price=reference_price)

    @staticmethod
    def _order_params(intent: OrderIntent) -> dict[str, Any]:
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
        return params

    def submit(
        self,
        intent: OrderIntent,
        *,
        filters: SymbolFilters | None = None,
        reference_price: Decimal | None = None,
    ) -> Order:
        """Submit (or, with ``use_order_test``, validate-only) a Spot order.

        * ``filters`` — optional exchangeInfo filters; when given the precision guard
          rounds/refuses before any network call (``OrderPrecisionError``).
        * Parseable error body ``code`` (HTTP 200 or 4xx) → ``ExchangeRejectError``;
          execution-unknown / unparseable codes, 4xx without a parseable body,
          HTTP 429 (``RateLimitedSubmitError``), 5xx and timeouts →
          ``AmbiguousSubmitError`` (see ``_http.classify_submit_response``).
        * ``use_order_test=True`` never calls ``/api/v1/spot/order``; success
          returns a ``TEST_ACCEPTED`` order (nothing reached the matching engine).
        """
        intent = self._validated_spot_intent(
            intent, filters=filters, reference_price=reference_price
        )
        if self._use_order_test:
            try:
                result = self.test_order(intent)
            except RateLimitError as exc:
                # orderTest never reaches the matching engine: a 429 here is a
                # definitive (non-blocking) refusal, not an ambiguous live submit.
                raise OrderTestRejectError(
                    f"spot orderTest rate limited HTTP 429 "
                    f"client_order_id={intent.client_order_id!r}",
                    client_order_id=intent.client_order_id,
                    http_status=429,
                ) from exc
            if not result.accepted:
                raise OrderTestRejectError(
                    f"spot orderTest rejected HTTP {result.http_status} code={result.code} "
                    f"client_order_id={intent.client_order_id!r}",
                    client_order_id=intent.client_order_id,
                    http_status=result.http_status,
                    code=result.code,
                    msg=result.msg,
                )
            order = empty_unknown_order(intent).model_copy(
                update={"status": OrderStatus.TEST_ACCEPTED}
            )
            self._orders[intent.client_order_id] = order
            return order

        path = VERIFIED_SUBMIT_PATHS["spot_order"]
        self.remember_intent(intent)
        response = signed_form_request(
            self._client,
            method="POST",
            path=path,
            api_key=self._api_key,
            api_secret=self._api_secret,
            params=self._order_params(intent),
            recv_window_ms=self._recv_window_ms,
            client_order_id=intent.client_order_id,
        )
        payload = classify_submit_response(
            response, client_order_id=intent.client_order_id
        )
        order = order_from_exchange_payload(payload, intent)
        self._orders[intent.client_order_id] = order
        return order

    def test_order(self, intent: OrderIntent) -> OrderTestResult:
        """``POST /api/v1/spot/orderTest`` only. Never touches ``/api/v1/spot/order``.

        Returns the HTTP status and any exchange ``code``/``msg``; does not raise on
        exchange rejects (transport errors and HTTP 429 still raise).
        """
        path = VERIFIED_SUBMIT_PATHS["spot_order_test"]
        if path != SPOT_ORDER_TEST_PATH:
            raise ExchangeError(f"order test path mismatch: {path!r}")
        response = signed_form_request(
            self._client,
            method="POST",
            path=path,
            api_key=self._api_key,
            api_secret=self._api_secret,
            params=self._order_params(intent),
            recv_window_ms=self._recv_window_ms,
        )
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        is_error, code, msg = parse_error_code(payload)
        accepted = (
            response.status_code < 300 and not is_error and isinstance(payload, dict)
        )
        return OrderTestResult(
            http_status=response.status_code, accepted=accepted, code=code, msg=msg
        )

    def cancel(self, client_order_id: str) -> Order:
        path = VERIFIED_QUERY_PATHS["spot_cancel_order"]
        response = signed_form_request(
            self._client,
            method="DELETE",
            path=path,
            api_key=self._api_key,
            api_secret=self._api_secret,
            # Official DELETE /api/v1/spot/order: `orderId` or `clientOrderId`
            # (verified 2026-10-13, docs/API_CONTRACTS.md §3.3).
            params={"clientOrderId": client_order_id},
            recv_window_ms=self._recv_window_ms,
        )
        if response.status_code >= 500:
            raise ExchangeError(
                f"spot cancel HTTP {response.status_code} (status unknown) "
                f"client_order_id={client_order_id!r}"
            )
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        is_error, code, msg = parse_error_code(payload)
        if is_error or response.status_code >= 400:
            raise ExchangeRejectError(
                f"spot cancel rejected HTTP {response.status_code} code={code} "
                f"client_order_id={client_order_id!r}",
                client_order_id=client_order_id,
                http_status=response.status_code,
                code=code,
                msg=msg,
            )
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
            # Official GET /api/v1/spot/order: `orderId` or `origClientOrderId`
            # (verified 2026-10-13, docs/API_CONTRACTS.md §3.3).
            params={"origClientOrderId": client_order_id},
            recv_window_ms=self._recv_window_ms,
        )
        if response.status_code == 404:
            return None
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        is_error, code, _msg = parse_error_code(payload)
        if is_error and code == NO_SUCH_ORDER_CODE:
            # -2013 NO_SUCH_ORDER "Order does not exist." (HTTP 200 or 4xx)
            return None
        if is_error or response.status_code >= 400:
            raise ExchangeError(
                f"spot get_order HTTP {response.status_code} code={code} "
                f"client_order_id={client_order_id!r}"
            )
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
