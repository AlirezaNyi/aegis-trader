"""Spot adapter hardening — typed rejects, unknown status, precision, orderTest.

Mocked httpx only; no real exchange requests.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.exchange.errors import (
    AmbiguousSubmitError,
    ExchangeError,
    ExchangeRejectError,
    OrderPrecisionError,
    OrderTestRejectError,
    RateLimitedSubmitError,
    RateLimitError,
    is_definitive_submit_reject,
)
from aegis.exchange.factory import build_execution_port
from aegis.exchange.futures_client import ToobitFuturesExecutionClient
from aegis.exchange.precision import (
    SymbolFilters,
    apply_spot_precision,
    round_down_to_step,
    round_up_to_step,
)
from aegis.exchange.router import ToobitExecutionRouter
from aegis.exchange.spot_client import SPOT_ORDER_TEST_PATH, ToobitSpotExecutionClient
from aegis.exchange.status_map import map_exchange_status
from aegis.orders.manager import OrderManager, OrderSubmitBlocked
from aegis.orders.persist import TestOrderPersistError, upsert_live_order
from aegis.reconcile.service import Reconciler
from aegis.risk.handoff import RiskHandoffDenied
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.metadata import SymbolMetadata
from aegis.schemas.orders import Order, OrderIntent, OrderStatus
from aegis.schemas.risk import RiskDecision, RiskDecisionType

API_KEY = "test-api-key-not-real"
SECRET = "test-secret-not-real"
NOW = datetime(2026, 10, 13, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- helpers


def _intent(
    *,
    client_order_id: str | None = None,
    side: str = "BUY",
    order_type: str = "LIMIT",
    quantity: str = "1",
    price: str | None = "100",
    market_type: MarketType = MarketType.SPOT,
    symbol: str = "ETHUSDT",
    proposal_id: Any = None,
) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        proposal_id=proposal_id or uuid4(),
        risk_decision_correlation_id="corr-live",
        client_order_id=client_order_id or f"c-{uuid4().hex[:8]}",
        ledger_kind=LedgerKind.LIVE,
        instrument=InstrumentRef(market_type=market_type, symbol=symbol),
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        price=None if price is None else Decimal(price),
        created_at=NOW,
    )


def _spot(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    use_order_test: bool = False,
) -> ToobitSpotExecutionClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.toobit.com")
    return ToobitSpotExecutionClient(
        api_key=API_KEY, api_secret=SECRET, client=http, use_order_test=use_order_test
    )


def _ack(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": "ETHUSDT",
        "orderId": "99",
        "clientOrderId": "c-1",
        "status": "NEW",
        "side": "BUY",
        "type": "LIMIT",
        "origQty": "1",
        "executedQty": "0",
        "price": "100",
    }
    body.update(overrides)
    return body


def _query(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(request.url)).query)


def _filters(**kw: Any) -> SymbolFilters:
    base: dict[str, Any] = {
        "symbol": "ETHUSDT",
        "tick_size": Decimal("0.01"),
        "step_size": Decimal("0.001"),
        "min_qty": Decimal("0.001"),
        "min_notional": Decimal("5"),
    }
    base.update(kw)
    return SymbolFilters(**base)


# ------------------------------------------------- error body → typed reject


@pytest.mark.parametrize("http_status", [200, 400])
async def test_submit_error_code_raises_typed_reject_never_new(http_status: int) -> None:
    client = _spot(
        lambda _r: httpx.Response(
            http_status, json={"code": -1131, "msg": "Balance insufficient"}
        )
    )
    with pytest.raises(ExchangeRejectError) as info:
        await client.submit(_intent(client_order_id="c-rej"))
    assert info.value.code == -1131
    assert info.value.msg == "Balance insufficient"
    assert info.value.http_status == http_status
    assert "c-rej" not in client._orders


@pytest.mark.parametrize("http_status", [400, 401, 403, 408, 418, 499])
@pytest.mark.parametrize(
    "body",
    [b"", b"<html>gateway</html>", b'{"msg": "no code"}', b"[]"],
)
async def test_submit_4xx_without_parseable_code_is_ambiguous(
    http_status: int, body: bytes
) -> None:
    client = _spot(lambda _r: httpx.Response(http_status, content=body))
    with pytest.raises(AmbiguousSubmitError):
        await client.submit(_intent())


@pytest.mark.parametrize("http_status", [200, 400])
@pytest.mark.parametrize("raw_code", ["abc", None, "", [1], {"x": 1}])
async def test_submit_unparseable_code_is_ambiguous(http_status: int, raw_code: Any) -> None:
    client = _spot(
        lambda _r: httpx.Response(http_status, json={"code": raw_code, "msg": "x"})
    )
    with pytest.raises(AmbiguousSubmitError):
        await client.submit(_intent())


async def test_submit_429_with_code_body_is_still_ambiguous() -> None:
    client = _spot(
        lambda _r: httpx.Response(429, json={"code": -1003, "msg": "TOO_MANY_REQUESTS"})
    )
    with pytest.raises(RateLimitedSubmitError):
        await client.submit(_intent())


def _futures(handler: Callable[[httpx.Request], httpx.Response]) -> ToobitFuturesExecutionClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.toobit.com")
    return ToobitFuturesExecutionClient(api_key=API_KEY, api_secret=SECRET, client=http)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"msg": "rate"}),
        httpx.Response(400, content=b""),
        httpx.Response(499, content=b"<html/>"),
        httpx.Response(400, json={"code": -1131, "msg": "x"}),
        httpx.Response(200, json={"code": -1131, "msg": "x"}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(503, content=b""),
    ],
)
async def test_futures_submit_errors_are_ambiguous(response: httpx.Response) -> None:
    client = _futures(lambda _r: response)
    with pytest.raises(AmbiguousSubmitError):
        await client.submit(_intent(market_type=MarketType.FUTURES, symbol="ETH-SWAP-USDT"))


def test_is_definitive_submit_reject_classification() -> None:
    assert is_definitive_submit_reject(ExchangeRejectError("r", code=-1131, http_status=200))
    assert is_definitive_submit_reject(ExchangeRejectError("r", code=-1131, http_status=400))
    assert is_definitive_submit_reject(ExchangeRejectError("local guard"))
    assert is_definitive_submit_reject(OrderPrecisionError("too small"))
    assert is_definitive_submit_reject(OrderTestRejectError("t", http_status=400))
    assert not is_definitive_submit_reject(ExchangeRejectError("r", http_status=400))
    assert not is_definitive_submit_reject(ExchangeRejectError("r", http_status=429, code=-1))
    assert not is_definitive_submit_reject(ExchangeRejectError("r", http_status=502, code=-1))
    assert not is_definitive_submit_reject(ExchangeRejectError("r", code=-1007, http_status=200))


@pytest.mark.parametrize("code", [-1000, -1001, -1006, -1007, -1141, -1146])
async def test_submit_execution_unknown_codes_are_ambiguous(code: int) -> None:
    client = _spot(lambda _r: httpx.Response(200, json={"code": code, "msg": "x"}))
    with pytest.raises(AmbiguousSubmitError):
        await client.submit(_intent())


async def test_submit_2xx_non_json_is_ambiguous() -> None:
    client = _spot(lambda _r: httpx.Response(200, content=b"<html>oops</html>"))
    with pytest.raises(AmbiguousSubmitError):
        await client.submit(_intent())


async def test_submit_ack_without_status_is_unknown_not_new() -> None:
    body = _ack()
    del body["status"]
    client = _spot(lambda _r: httpx.Response(200, json=body))
    order = await client.submit(_intent())
    assert order.status == OrderStatus.UNKNOWN


async def test_submit_ack_ok_still_open() -> None:
    client = _spot(lambda _r: httpx.Response(200, json=_ack()))
    assert (await client.submit(_intent())).status == OrderStatus.OPEN


async def test_reject_msg_is_truncated_and_printable() -> None:
    client = _spot(
        lambda _r: httpx.Response(200, json={"code": -1121, "msg": "bad\n" + "x" * 500})
    )
    with pytest.raises(ExchangeRejectError) as info:
        await client.submit(_intent())
    assert info.value.msg is not None
    assert "\n" not in info.value.msg
    assert len(info.value.msg) <= 200


# ------------------------------------------------- unknown status


def test_unknown_status_maps_to_unknown_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    assert map_exchange_status("SOMETHING_NEW") == OrderStatus.UNKNOWN
    assert map_exchange_status(None) == OrderStatus.UNKNOWN
    assert map_exchange_status("partially_filled") == OrderStatus.PARTIALLY_FILLED
    assert any("unknown_exchange_order_status" in r.getMessage() for r in caplog.records)


async def test_submit_with_unknown_status_returns_unknown() -> None:
    client = _spot(lambda _r: httpx.Response(200, json=_ack(status="WEIRD")))
    assert (await client.submit(_intent())).status == OrderStatus.UNKNOWN


# ------------------------------------------------- cancel / query param names


async def test_cancel_uses_client_order_id_and_get_uses_orig_client_order_id() -> None:
    seen: list[tuple[str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, _query(request)))
        if request.method == "POST":
            return httpx.Response(200, json=_ack(clientOrderId="c-q"))
        if request.method == "DELETE":
            return httpx.Response(200, json=_ack(clientOrderId="c-q", status="CANCELED"))
        return httpx.Response(200, json=_ack(clientOrderId="c-q", status="CANCELED"))

    client = _spot(handler)
    await client.submit(_intent(client_order_id="c-q"))
    assert (await client.cancel("c-q")).status == OrderStatus.CANCELED
    got = await client.get_order("c-q")
    assert got is not None and got.status == OrderStatus.CANCELED
    delete_q = next(q for m, q in seen if m == "DELETE")
    get_q = next(q for m, q in seen if m == "GET")
    assert delete_q["clientOrderId"] == ["c-q"] and "origClientOrderId" not in delete_q
    assert get_q["origClientOrderId"] == ["c-q"] and "clientOrderId" not in get_q


@pytest.mark.parametrize("http_status", [200, 400])
async def test_get_order_no_such_order_returns_none(http_status: int) -> None:
    client = _spot(
        lambda _r: httpx.Response(http_status, json={"code": -2013, "msg": "Order does not exist."})
    )
    assert await client.get_order("c-x") is None


async def test_get_order_other_error_code_raises() -> None:
    client = _spot(lambda _r: httpx.Response(200, json={"code": -1022, "msg": "sig"}))
    with pytest.raises(ExchangeError):
        await client.get_order("c-x")


async def test_cancel_error_code_is_typed_reject() -> None:
    client = _spot(lambda _r: httpx.Response(200, json={"code": -2011, "msg": "CANCEL_REJECTED"}))
    with pytest.raises(ExchangeRejectError) as info:
        await client.cancel("c-x")
    assert info.value.code == -2011


# ------------------------------------------------- precision guard


def test_round_helpers() -> None:
    assert round_down_to_step(Decimal("1.23456"), Decimal("0.001")) == Decimal("1.234")
    assert round_up_to_step(Decimal("1.23401"), Decimal("0.001")) == Decimal("1.235")
    assert round_down_to_step(Decimal("7"), Decimal("5")) == Decimal("5")


def test_precision_rounds_qty_down_and_buy_price_down() -> None:
    out = apply_spot_precision(_intent(quantity="0.12345", price="100.019"), _filters())
    assert out.quantity == Decimal("0.123")
    assert out.price == Decimal("100.01")


def test_precision_sell_price_rounds_up() -> None:
    out = apply_spot_precision(
        _intent(side="SELL", quantity="0.1", price="100.011"), _filters()
    )
    assert out.price == Decimal("100.02")


@pytest.mark.parametrize(
    ("intent_kw", "filters_kw", "reason"),
    [
        ({"quantity": "0.0009"}, {}, "minQty"),
        ({"quantity": "0.01", "price": "100"}, {}, "minNotional"),
        ({}, {"tick_size": None}, "missing"),
        ({}, {"min_notional": None}, "missing"),
        ({}, {"step_size": Decimal("0")}, "non-positive"),
        ({"order_type": "MARKET", "price": None}, {}, "MARKET BUY"),
        ({"side": "SELL", "order_type": "MARKET", "price": None}, {}, "reference_price"),
        ({"symbol": "BTCUSDT"}, {}, "filters are for"),
        ({"price": None}, {}, "positive price"),
    ],
)
def test_precision_rejects(
    intent_kw: dict[str, Any], filters_kw: dict[str, Any], reason: str
) -> None:
    with pytest.raises(OrderPrecisionError) as info:
        apply_spot_precision(_intent(**intent_kw), _filters(**filters_kw))
    assert reason in str(info.value)
    assert isinstance(info.value, ExchangeRejectError)


def test_precision_market_sell_with_reference_price() -> None:
    out = apply_spot_precision(
        _intent(side="SELL", order_type="MARKET", price=None, quantity="0.1009"),
        _filters(),
        reference_price=Decimal("100"),
    )
    assert out.quantity == Decimal("0.100")
    assert out.price is None


def test_symbol_filters_from_metadata() -> None:
    meta = SymbolMetadata(
        market_type=MarketType.SPOT,
        symbol="ETHUSDT",
        status="TRADING",
        base_asset="ETH",
        quote_asset="USDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.0001"),
        min_qty=Decimal("0.0001"),
        min_notional=Decimal("1"),
    )
    f = SymbolFilters.from_metadata(meta)
    assert (f.tick_size, f.step_size, f.min_qty, f.min_notional) == (
        Decimal("0.01"),
        Decimal("0.0001"),
        Decimal("0.0001"),
        Decimal("1"),
    )


async def test_submit_with_filters_sends_rounded_values() -> None:
    seen: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(_query(request))
        return httpx.Response(200, json=_ack())

    client = _spot(handler)
    await client.submit(_intent(quantity="0.12345", price="100.019"), filters=_filters())
    assert seen["quantity"] == ["0.123"]
    assert seen["price"] == ["100.01"]


async def test_precision_reject_makes_no_http_call() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=_ack())

    client = _spot(handler)
    with pytest.raises(OrderPrecisionError):
        await client.submit(_intent(quantity="0.0001"), filters=_filters())
    assert calls == []


# ------------------------------------------------- orderTest mode


async def test_order_test_mode_never_calls_order_endpoint() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={})

    client = _spot(handler, use_order_test=True)
    order = await client.submit(_intent())
    assert paths == [SPOT_ORDER_TEST_PATH]
    assert order.status == OrderStatus.TEST_ACCEPTED
    assert order.filled_quantity == 0


async def test_order_test_mode_reject_raises_typed() -> None:
    client = _spot(
        lambda _r: httpx.Response(200, json={"code": -1137, "msg": "precision"}),
        use_order_test=True,
    )
    with pytest.raises(ExchangeRejectError) as info:
        await client.submit(_intent())
    assert info.value.code == -1137


async def test_test_order_returns_code_and_msg() -> None:
    client = _spot(lambda _r: httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."}))
    result = await client.test_order(_intent())
    assert result.accepted is False
    assert (result.http_status, result.code, result.msg) == (400, -1121, "Invalid symbol.")


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base: dict[str, object] = {
        "trading_mode": TradingMode.LIVE,
        "live_armed": True,
        "kill_switch": False,
        "require_database": False,
        "toobit_api_key": API_KEY,
        "toobit_api_secret": SECRET,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


async def test_factory_use_order_test_spot_only_and_test_path() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.toobit.com")
    port = build_execution_port(_settings(), client=http, use_order_test=True)
    assert isinstance(port, ToobitExecutionRouter)
    assert (await port.submit(_intent())).status == OrderStatus.TEST_ACCEPTED
    with pytest.raises(ExchangeRejectError):
        await port.submit(_intent(market_type=MarketType.FUTURES, symbol="BTC-SWAP-USDT"))
    assert paths == [SPOT_ORDER_TEST_PATH]


def test_factory_use_order_test_still_requires_live_gates() -> None:
    port = build_execution_port(
        _settings(trading_mode=TradingMode.PAPER, live_armed=False), use_order_test=True
    )
    assert type(port).__name__ == "NullExecutionPort"


# ------------------------------------------------- Order Manager


def _decision(proposal_id: Any, kind: RiskDecisionType = RiskDecisionType.APPROVE) -> RiskDecision:
    return RiskDecision(
        decision=kind,
        policy_version="test",
        rules_evaluated=["TEST"],
        rejection_reasons=(
            [{"rule_id": "TEST", "detail": "r"}] if kind == RiskDecisionType.REJECT else []
        ),
        validated_order_params={"action": "BUY"} if kind == RiskDecisionType.APPROVE else None,
        decided_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        correlation_id="corr-live",
        proposal_id=proposal_id,
    )


def _paired() -> tuple[RiskDecision, OrderIntent]:
    pid = uuid4()
    return _decision(pid), _intent(proposal_id=pid)


class ScriptedPort:
    def __init__(self) -> None:
        self.submit_exc: BaseException | None = None
        self.submit_status = OrderStatus.OPEN
        self.get_exc: BaseException | None = None
        self.get_result: Order | None = None
        self.submit_calls = 0
        self.get_calls = 0

    async def submit(self, intent: OrderIntent) -> Order:
        self.submit_calls += 1
        if self.submit_exc is not None:
            raise self.submit_exc
        return Order(
            order_id=uuid4(),
            intent_id=intent.intent_id,
            client_order_id=intent.client_order_id,
            ledger_kind=LedgerKind.LIVE,
            instrument=intent.instrument,
            status=self.submit_status,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            price=intent.price,
            created_at=NOW,
            updated_at=NOW,
        )

    async def cancel(self, client_order_id: str) -> Order:
        raise NotImplementedError

    async def get_order(self, client_order_id: str) -> Order | None:
        self.get_calls += 1
        if self.get_exc is not None:
            raise self.get_exc
        return self.get_result


@pytest.mark.parametrize(
    "exc",
    [
        ExchangeRejectError("rej", code=-1131, http_status=200),
        ExchangeRejectError("rej", code=-1131, http_status=400),
        OrderPrecisionError("too small"),
        OrderTestRejectError("orderTest refused", http_status=400),
        ExchangeRejectError("futures disabled (local)"),
    ],
)
async def test_manager_explicit_reject_is_rejected_and_does_not_block(exc: Exception) -> None:
    port = ScriptedPort()
    port.submit_exc = exc
    mgr = OrderManager(_settings(), port, reconciler=Reconciler(port))
    decision, intent = _paired()
    order = await mgr.submit(decision, intent, now=NOW)
    assert order.status == OrderStatus.REJECTED
    assert mgr.block_new_submits is False
    assert port.get_calls == 0  # no reconcile needed for a definitive reject
    # Same client_order_id is never resubmitted.
    assert (await mgr.submit(decision, intent, now=NOW)).status == OrderStatus.REJECTED
    assert port.submit_calls == 1
    # A new, separately approved intent may proceed.
    port.submit_exc = None
    d2, i2 = _paired()
    assert (await mgr.submit(d2, i2, now=NOW)).status == OrderStatus.OPEN
    assert port.submit_calls == 2


@pytest.mark.parametrize(
    "exc",
    [
        RateLimitError("429"),
        RateLimitedSubmitError("429", client_order_id="x"),
        ExchangeRejectError("bodyless 4xx", http_status=400),
        ExchangeRejectError("bodyless 408", http_status=408),
        ExchangeRejectError("bodyless 499", http_status=499),
        ExchangeRejectError("429 code", http_status=429, code=-1003),
        ExchangeRejectError("exec unknown", http_status=200, code=-1007),
    ],
)
async def test_manager_non_definitive_reject_is_unknown_blocks_and_reconciles(
    exc: Exception,
) -> None:
    port = ScriptedPort()
    port.submit_exc = exc
    mgr = OrderManager(_settings(), port, reconciler=Reconciler(port))
    decision, intent = _paired()
    order = await mgr.submit(decision, intent, now=NOW)
    # get_result None => reconcile marks MISSING; never REJECTED, flow stays blocked.
    assert order.status in {OrderStatus.UNKNOWN, OrderStatus.MISSING}
    assert mgr.block_new_submits is True
    assert port.get_calls == 1
    assert port.submit_calls == 1
    d2, i2 = _paired()
    with pytest.raises(OrderSubmitBlocked):
        await mgr.submit(d2, i2, now=NOW)
    assert port.submit_calls == 1


def _spot_router_handler(
    submit_response: httpx.Response, calls: list[tuple[str, str]]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            # get_order during reconcile: order not found.
            return httpx.Response(200, json={"code": -2013, "msg": "Order does not exist."})
        return submit_response

    return handler


@pytest.mark.parametrize(
    "submit_response",
    [
        httpx.Response(429, headers={"X-Api-Limit-Reset-Timestamp": "1"}, json={"msg": "r"}),
        httpx.Response(408, content=b""),
        httpx.Response(499, content=b""),
        httpx.Response(200, json={"code": "not-an-int", "msg": "x"}),
    ],
)
async def test_manager_spot_ambiguous_http_is_unknown_and_reconciles(
    submit_response: httpx.Response,
) -> None:
    calls: list[tuple[str, str]] = []
    client = _spot(_spot_router_handler(submit_response, calls))
    mgr = OrderManager(_settings(), client, reconciler=Reconciler(client))
    decision, intent = _paired()
    order = await mgr.submit(decision, intent, now=NOW)
    assert order.status != OrderStatus.REJECTED
    assert order.status in {OrderStatus.UNKNOWN, OrderStatus.MISSING}
    assert mgr.block_new_submits is True
    assert [m for m, _ in calls] == ["POST", "GET"]  # one submit, then get_order


async def test_manager_spot_parseable_reject_is_rejected_not_blocked() -> None:
    calls: list[tuple[str, str]] = []
    client = _spot(
        _spot_router_handler(
            httpx.Response(400, json={"code": -1131, "msg": "Balance insufficient"}), calls
        )
    )
    mgr = OrderManager(_settings(), client, reconciler=Reconciler(client))
    decision, intent = _paired()
    order = await mgr.submit(decision, intent, now=NOW)
    assert order.status == OrderStatus.REJECTED
    assert mgr.block_new_submits is False
    assert [m for m, _ in calls] == ["POST"]  # no reconcile for a definitive reject


async def test_order_test_mode_429_is_definitive_order_test_reject() -> None:
    client = _spot(lambda _r: httpx.Response(429, json={"msg": "r"}), use_order_test=True)
    with pytest.raises(OrderTestRejectError) as info:
        await client.submit(_intent())
    assert info.value.http_status == 429


async def test_manager_cancel_unknown_ack_blocks_new_submits() -> None:
    port = ScriptedPort()
    mgr = OrderManager(_settings(), port)
    decision, intent = _paired()
    placed = await mgr.submit(decision, intent, now=NOW)
    assert placed.status == OrderStatus.OPEN
    assert mgr.block_new_submits is False

    async def cancel(client_order_id: str) -> Order:
        return placed.model_copy(update={"status": OrderStatus.UNKNOWN})

    port.cancel = cancel  # type: ignore[method-assign]
    await mgr.cancel(intent.client_order_id, now=NOW)
    assert mgr.critical_unknown is True
    assert mgr.block_new_submits is True
    d2, i2 = _paired()
    with pytest.raises(OrderSubmitBlocked):
        await mgr.submit(d2, i2, now=NOW)
    assert port.submit_calls == 1


async def test_manager_cancel_clean_ack_keeps_flow_open() -> None:
    port = ScriptedPort()
    mgr = OrderManager(_settings(), port)
    decision, intent = _paired()
    placed = await mgr.submit(decision, intent, now=NOW)

    async def cancel(client_order_id: str) -> Order:
        return placed.model_copy(update={"status": OrderStatus.CANCELED})

    port.cancel = cancel  # type: ignore[method-assign]
    await mgr.cancel(intent.client_order_id, now=NOW)
    assert mgr.block_new_submits is False


def test_persist_refuses_test_accepted() -> None:
    session = MagicMock()
    _decision_unused, intent = _paired()
    order = Order(
        order_id=uuid4(),
        intent_id=intent.intent_id,
        client_order_id=intent.client_order_id,
        ledger_kind=LedgerKind.LIVE,
        instrument=intent.instrument,
        status=OrderStatus.TEST_ACCEPTED,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        price=intent.price,
        created_at=NOW,
        updated_at=NOW,
    )
    with pytest.raises(TestOrderPersistError):
        upsert_live_order(session, order)
    session.add.assert_not_called()
    session.scalars.assert_not_called()


async def test_manager_ambiguous_still_unknown_and_blocks() -> None:
    port = ScriptedPort()
    port.submit_exc = AmbiguousSubmitError("timeout", client_order_id="x")
    port.get_exc = ExchangeError("query failed")
    mgr = OrderManager(_settings(), port, reconciler=Reconciler(port))
    decision, intent = _paired()
    order = await mgr.submit(decision, intent, now=NOW)  # reconcile raising must not propagate
    assert order.status == OrderStatus.UNKNOWN
    assert mgr.block_new_submits is True
    assert port.get_calls == 1
    assert port.submit_calls == 1


async def test_manager_generic_error_reconcile_raises_stays_unknown() -> None:
    port = ScriptedPort()
    port.submit_exc = RuntimeError("boom")
    port.get_exc = RuntimeError("reconcile down")
    mgr = OrderManager(_settings(), port, reconciler=Reconciler(port))
    decision, intent = _paired()
    assert (await mgr.submit(decision, intent, now=NOW)).status == OrderStatus.UNKNOWN
    assert mgr.critical_unknown is True


async def test_manager_ack_with_unknown_status_blocks_and_reconciles() -> None:
    port = ScriptedPort()
    port.submit_status = OrderStatus.UNKNOWN
    mgr = OrderManager(_settings(), port, reconciler=Reconciler(port))
    decision, intent = _paired()
    order = await mgr.submit(decision, intent, now=NOW)
    assert port.get_calls == 1
    # get_result None ⇒ MISSING; flow stays blocked either way.
    assert order.status in {OrderStatus.UNKNOWN, OrderStatus.MISSING}
    assert mgr.block_new_submits is True


async def test_manager_reject_decision_never_reaches_port() -> None:
    port = ScriptedPort()
    mgr = OrderManager(_settings(), port)
    pid = uuid4()
    with pytest.raises(RiskHandoffDenied):
        await mgr.submit(_decision(pid, RiskDecisionType.REJECT), _intent(proposal_id=pid), now=NOW)
    assert port.submit_calls == 0


async def test_manager_test_accepted_is_terminal_and_not_blocking() -> None:
    port = ScriptedPort()
    port.submit_status = OrderStatus.TEST_ACCEPTED
    mgr = OrderManager(_settings(), port)
    decision, intent = _paired()
    assert (await mgr.submit(decision, intent, now=NOW)).status == OrderStatus.TEST_ACCEPTED
    assert mgr.block_new_submits is False
    assert (await mgr.submit(decision, intent, now=NOW)).status == OrderStatus.TEST_ACCEPTED
    assert port.submit_calls == 1


# ------------------------------------------------- order test script


def _load_script() -> ModuleType:
    path = ROOT / "scripts" / "toobit_order_test.py"
    spec = importlib.util.spec_from_file_location("toobit_order_test_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_refuses_without_confirmation(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _load_script()

    def _no_network(*_a: Any, **_k: Any) -> None:
        raise AssertionError("network client must not be built")

    monkeypatch.setattr(script.httpx, "AsyncClient", _no_network)
    rc = script.main(["--symbol", "ETHUSDT", "--price", "100", "--quantity", "1"])
    assert rc == 2
    assert "refused" in capsys.readouterr().out


def test_script_request_hook_only_allows_order_test() -> None:
    script = _load_script()
    ok = httpx.Request("POST", f"https://api.toobit.com{SPOT_ORDER_TEST_PATH}?x=1")
    script._assert_order_test_only(ok)
    for method, path in [
        ("POST", "/api/v1/spot/order"),
        ("DELETE", "/api/v1/spot/order"),
        ("POST", "/api/v1/account/withdraw"),
        ("GET", SPOT_ORDER_TEST_PATH),
    ]:
        with pytest.raises(script.ForbiddenPathError):
            script._assert_order_test_only(httpx.Request(method, f"https://api.toobit.com{path}"))


async def test_script_async_hook_refuses_order_endpoint() -> None:
    script = _load_script()
    await script._order_test_hook(
        httpx.Request("POST", f"https://api.toobit.com{SPOT_ORDER_TEST_PATH}")
    )
    with pytest.raises(script.ForbiddenPathError):
        await script._order_test_hook(
            httpx.Request("POST", "https://api.toobit.com/api/v1/spot/order")
        )


def test_script_sends_only_order_test_and_prints_no_secret(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _load_script()
    paths: list[str] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={})

    def fake_client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["event_hooks"]["request"], "orderTest path hook must be installed"
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(script.httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(
        script,
        "Settings",
        lambda **_k: _settings(trading_mode=TradingMode.PAPER, live_armed=False),
    )
    rc = script.main(
        [
            "--symbol",
            "ETHUSDT",
            "--price",
            "100",
            "--quantity",
            "1",
            "--i-understand-order-test",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert paths == [SPOT_ORDER_TEST_PATH]
    assert "http_status=200" in out
    assert SECRET not in out and API_KEY not in out
