"""Shared signed HTTP helpers for Spot/Futures clients (credential plane only)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from aegis.exchange.errors import AmbiguousSubmitError, ExchangeError, RateLimitError
from aegis.exchange.paths import (
    VERIFIED_QUERY_PATHS,
    VERIFIED_SUBMIT_PATHS,
    WITHDRAW_PATH,
)
from aegis.exchange.signing import build_query_string, compact_json, sign_params, sign_v2_json
from aegis.exchange.status_map import map_exchange_status
from aegis.exchange.withdraw import assert_withdraw_forbidden
from aegis.schemas.common import LedgerKind
from aegis.schemas.orders import Order, OrderIntent, OrderStatus

_ALLOWED_PATHS = frozenset(
    {
        *VERIFIED_SUBMIT_PATHS.values(),
        *VERIFIED_QUERY_PATHS.values(),
    }
)

_AMBIGUOUS_TRANSPORT = (
    httpx.TimeoutException,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.NetworkError,
)


def _assert_path_allowed(path: str) -> None:
    assert_withdraw_forbidden(path)
    if path == WITHDRAW_PATH or path not in _ALLOWED_PATHS:
        raise ExchangeError(f"path not on verified allowlist: {path!r}")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _header_api_key(api_key: str) -> dict[str, str]:
    return {"X-BB-APIKEY": api_key}


def raise_for_rate_limit(response: httpx.Response) -> None:
    if response.status_code != 429:
        return
    reset_raw = response.headers.get("X-Api-Limit-Reset-Timestamp")
    reset_ms: int | None = None
    if reset_raw is not None and str(reset_raw).strip().isdigit():
        reset_ms = int(reset_raw)
    raise RateLimitError(
        "Toobit rate limit HTTP 429",
        reset_timestamp_ms=reset_ms,
    )


def raise_if_ambiguous_submit(
    response: httpx.Response | None,
    *,
    client_order_id: str,
    exc: BaseException | None = None,
) -> None:
    if isinstance(exc, _AMBIGUOUS_TRANSPORT):
        raise AmbiguousSubmitError(
            f"submit transport ambiguity for client_order_id={client_order_id!r}",
            client_order_id=client_order_id,
        ) from exc
    if response is not None and response.status_code >= 500:
        raise AmbiguousSubmitError(
            f"submit HTTP {response.status_code} for client_order_id={client_order_id!r}",
            client_order_id=client_order_id,
        )


def signed_form_request(
    client: httpx.Client,
    *,
    method: str,
    path: str,
    api_key: str,
    api_secret: str,
    params: dict[str, Any],
    recv_window_ms: int,
    client_order_id: str | None = None,
) -> httpx.Response:
    """SIGNED request with query-string HMAC (Spot-style form/query params)."""
    _assert_path_allowed(path)
    ordered: dict[str, Any] = dict(params)
    ordered.setdefault("recvWindow", recv_window_ms)
    ordered.setdefault("timestamp", _now_ms())
    signature = sign_params(api_secret, ordered)
    ordered["signature"] = signature
    query = build_query_string(ordered)
    headers = _header_api_key(api_key)
    url = f"{path}?{query}"
    try:
        response = client.request(method.upper(), url, headers=headers)
    except _AMBIGUOUS_TRANSPORT as exc:
        if client_order_id is not None:
            raise_if_ambiguous_submit(None, client_order_id=client_order_id, exc=exc)
        raise
    raise_for_rate_limit(response)
    if client_order_id is not None and response.status_code >= 500:
        raise_if_ambiguous_submit(response, client_order_id=client_order_id)
    return response


def signed_v2_json_post(
    client: httpx.Client,
    *,
    path: str,
    api_key: str,
    api_secret: str,
    body: Mapping[str, Any],
    recv_window_ms: int,
    category: str = "USDT",
    client_order_id: str | None = None,
) -> httpx.Response:
    """SIGNED v2 POST: query has timestamp/recvWindow/category; body is compact JSON."""
    _assert_path_allowed(path)
    query_params: dict[str, Any] = {
        "category": category,
        "recvWindow": recv_window_ms,
        "timestamp": _now_ms(),
    }
    json_body = compact_json(dict(body))
    query_string = build_query_string(query_params)
    signature = sign_v2_json(api_secret, query_string, json_body)
    url = f"{path}?{query_string}&signature={signature}"
    headers = {
        **_header_api_key(api_key),
        "Content-Type": "application/json",
    }
    try:
        response = client.post(url, headers=headers, content=json_body.encode("utf-8"))
    except _AMBIGUOUS_TRANSPORT as exc:
        if client_order_id is not None:
            raise_if_ambiguous_submit(None, client_order_id=client_order_id, exc=exc)
        raise
    raise_for_rate_limit(response)
    if client_order_id is not None and response.status_code >= 500:
        raise_if_ambiguous_submit(response, client_order_id=client_order_id)
    return response


def signed_v2_query(
    client: httpx.Client,
    *,
    method: str,
    path: str,
    api_key: str,
    api_secret: str,
    params: dict[str, Any],
    recv_window_ms: int,
) -> httpx.Response:
    """SIGNED v2 GET/DELETE with query-string HMAC (no JSON body)."""
    _assert_path_allowed(path)
    ordered: dict[str, Any] = dict(params)
    ordered.setdefault("recvWindow", recv_window_ms)
    ordered.setdefault("timestamp", _now_ms())
    signature = sign_params(api_secret, ordered)
    ordered["signature"] = signature
    query = build_query_string(ordered)
    headers = _header_api_key(api_key)
    response = client.request(method.upper(), f"{path}?{query}", headers=headers)
    raise_for_rate_limit(response)
    return response


def order_from_exchange_payload(
    payload: Mapping[str, Any],
    intent: OrderIntent,
    *,
    now: datetime | None = None,
) -> Order:
    """Build local Order from Spot (flat) or Futures (data-wrapped) payload fields."""
    clock = now if now is not None else datetime.now(tz=UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)

    data = payload
    if "data" in payload and isinstance(payload["data"], dict):
        data = payload["data"]

    status_raw = str(data.get("status", "NEW"))
    status = map_exchange_status(status_raw)
    filled_raw = data.get("executedQty", data.get("executed_qty", "0"))
    qty_raw = data.get("origQty", data.get("quantity", intent.quantity))
    price_raw = data.get("price", intent.price)
    exchange_order_id = data.get("orderId")
    if exchange_order_id is not None:
        exchange_order_id = str(exchange_order_id)

    return Order(
        order_id=uuid4(),
        intent_id=intent.intent_id,
        client_order_id=intent.client_order_id,
        exchange_order_id=exchange_order_id,
        ledger_kind=LedgerKind.LIVE,
        instrument=intent.instrument,
        status=status,
        side=str(data.get("side", intent.side)),
        order_type=str(data.get("type", intent.order_type)),
        quantity=Decimal(str(qty_raw)),
        filled_quantity=Decimal(str(filled_raw)),
        price=Decimal(str(price_raw)) if price_raw not in (None, "") else intent.price,
        created_at=clock,
        updated_at=clock,
    )


def merge_query_into_order(
    existing: Order,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> Order:
    """Update an existing local order from a get_order exchange payload."""
    clock = now if now is not None else datetime.now(tz=UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    data = payload
    if "data" in payload and isinstance(payload["data"], dict):
        data = payload["data"]
    status = map_exchange_status(str(data.get("status", existing.status.value.upper())))
    filled_raw = data.get("executedQty", existing.filled_quantity)
    exchange_order_id = data.get("orderId", existing.exchange_order_id)
    if exchange_order_id is not None:
        exchange_order_id = str(exchange_order_id)
    price_raw = data.get("price", existing.price)
    return existing.model_copy(
        update={
            "status": status,
            "filled_quantity": Decimal(str(filled_raw)),
            "exchange_order_id": exchange_order_id,
            "price": (
                Decimal(str(price_raw)) if price_raw not in (None, "") else existing.price
            ),
            "updated_at": clock,
        }
    )


def ensure_live_intent(intent: OrderIntent) -> None:
    if intent.ledger_kind != LedgerKind.LIVE:
        raise ExchangeError(
            f"live exchange adapter refuses ledger_kind={intent.ledger_kind.value!r}"
        )


def empty_unknown_order(intent: OrderIntent, *, now: datetime | None = None) -> Order:
    clock = now if now is not None else datetime.now(tz=UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    return Order(
        order_id=uuid4(),
        intent_id=intent.intent_id,
        client_order_id=intent.client_order_id,
        exchange_order_id=None,
        ledger_kind=LedgerKind.LIVE,
        instrument=intent.instrument,
        status=OrderStatus.UNKNOWN,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        filled_quantity=Decimal("0"),
        price=intent.price,
        created_at=clock,
        updated_at=clock,
    )
