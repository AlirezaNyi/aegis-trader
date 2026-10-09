"""Phase 7 Toobit exchange adapter tests — mocks only; no real orders."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

import aegis.exchange as exchange
from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.exchange.errors import AmbiguousSubmitError, RateLimitError
from aegis.exchange.factory import build_execution_port
from aegis.exchange.futures_client import ToobitFuturesExecutionClient
from aegis.exchange.signing import build_query_string, sign_params, sign_v2_json
from aegis.exchange.spot_client import ToobitSpotExecutionClient
from aegis.exchange.withdraw import WithdrawForbiddenError, assert_withdraw_forbidden
from aegis.interfaces.execution import NullExecutionPort
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import OrderIntent, OrderStatus

SECRET = "test-secret-not-real"
API_KEY = "test-api-key-not-real"


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base: dict[str, object] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
        "toobit_api_key": "",
        "toobit_api_secret": "",
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _intent(
    *,
    market_type: MarketType = MarketType.SPOT,
    symbol: str = "ETHUSDT",
    client_order_id: str = "c-spot-1",
    side: str = "BUY",
) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        proposal_id=uuid4(),
        risk_decision_correlation_id="corr",
        client_order_id=client_order_id,
        ledger_kind=LedgerKind.LIVE,
        instrument=InstrumentRef(market_type=market_type, symbol=symbol),
        side=side,
        order_type="LIMIT",
        quantity=Decimal("1"),
        price=Decimal("100"),
        created_at=datetime.now(UTC),
    )


def test_live_submit_supported_flag() -> None:
    assert exchange.LIVE_SUBMIT_SUPPORTED is True
    assert exchange.PHASE == 7


def test_sign_params_lowercase_hex_no_secret_in_output() -> None:
    params = {"symbol": "ETHUSDT", "timestamp": 1_700_000_000_000, "recvWindow": 5000}
    sig = sign_params(SECRET, params)
    assert sig == sig.lower()
    assert all(c in "0123456789abcdef" for c in sig)
    assert SECRET not in sig
    assert API_KEY not in sig
    expected = hmac.new(
        SECRET.encode(),
        build_query_string(params).encode(),
        hashlib.sha256,
    ).hexdigest()
    assert sig == expected


def test_sign_v2_json_concatenates_query_and_body() -> None:
    query = "category=USDT&recvWindow=5000&timestamp=1700000000000"
    body = '{"symbol":"BTC-SWAP-USDT","side":"BUY"}'
    sig = sign_v2_json(SECRET, query, body)
    assert SECRET not in sig
    expected = hmac.new(
        SECRET.encode(),
        f"{query}{body}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert sig == expected


def test_spot_submit_mock_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-BB-APIKEY") == API_KEY
        assert "signature=" in str(request.url)
        assert SECRET not in str(request.url)
        assert SECRET not in (request.content.decode() if request.content else "")
        return httpx.Response(
            200,
            json={
                "symbol": "ETHUSDT",
                "orderId": "99",
                "clientOrderId": "c-spot-1",
                "status": "NEW",
                "side": "BUY",
                "type": "LIMIT",
                "origQty": "1",
                "executedQty": "0",
                "price": "100",
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.toobit.com")
    client = ToobitSpotExecutionClient(
        api_key=API_KEY, api_secret=SECRET, client=http
    )
    order = client.submit(_intent())
    assert order.status == OrderStatus.OPEN
    assert order.exchange_order_id == "99"
    assert order.ledger_kind == LedgerKind.LIVE


def test_futures_requires_client_order_id() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"code": 200, "data": {}}))
    http = httpx.Client(transport=transport, base_url="https://api.toobit.com")
    client = ToobitFuturesExecutionClient(
        api_key=API_KEY, api_secret=SECRET, client=http
    )
    intent = _intent(
        market_type=MarketType.FUTURES,
        symbol="BTC-SWAP-USDT",
        client_order_id="   ",
        side="LONG",
    )
    with pytest.raises(Exception, match="newClientOrderId|client"):
        client.submit(intent)


def test_futures_submit_sends_new_client_order_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "orderId": "1",
                    "clientOrderId": "fut-1",
                    "status": "NEW",
                    "side": "BUY",
                    "type": "LIMIT",
                    "origQty": "1",
                    "executedQty": "0",
                    "price": "100",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.toobit.com")
    client = ToobitFuturesExecutionClient(
        api_key=API_KEY, api_secret=SECRET, client=http
    )
    order = client.submit(
        _intent(
            market_type=MarketType.FUTURES,
            symbol="BTC-SWAP-USDT",
            client_order_id="fut-1",
            side="LONG",
        )
    )
    assert "newClientOrderId" in str(seen["body"])
    assert order.status == OrderStatus.OPEN


def test_timeout_raises_ambiguous() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.toobit.com")
    client = ToobitSpotExecutionClient(
        api_key=API_KEY, api_secret=SECRET, client=http
    )
    with pytest.raises(AmbiguousSubmitError) as exc:
        client.submit(_intent())
    assert exc.value.client_order_id == "c-spot-1"


def test_http_5xx_raises_ambiguous() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"msg": "unavailable"})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.toobit.com")
    client = ToobitSpotExecutionClient(
        api_key=API_KEY, api_secret=SECRET, client=http
    )
    with pytest.raises(AmbiguousSubmitError):
        client.submit(_intent())


def test_http_429_rate_limit_header() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"X-Api-Limit-Reset-Timestamp": "1700000005000"},
            json={"msg": "rate"},
        )

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.toobit.com")
    client = ToobitSpotExecutionClient(
        api_key=API_KEY, api_secret=SECRET, client=http
    )
    with pytest.raises(RateLimitError) as exc:
        client.submit(_intent())
    assert exc.value.reset_timestamp_ms == 1_700_000_005_000


def test_withdraw_forbidden() -> None:
    with pytest.raises(WithdrawForbiddenError):
        assert_withdraw_forbidden("/api/v1/account/withdraw")
    with pytest.raises(WithdrawForbiddenError):
        assert_withdraw_forbidden("/api/v1/account/withdraw?foo=1")


def test_factory_returns_null_when_not_armed() -> None:
    # LIVE + live_armed=false cannot construct Settings (startup validation).
    # Paper mode with credentials still yields Null — gates not satisfied.
    port = build_execution_port(
        _settings(
            trading_mode=TradingMode.PAPER,
            live_armed=False,
            toobit_api_key=API_KEY,
            toobit_api_secret=SECRET,
        )
    )
    assert isinstance(port, NullExecutionPort)


def test_factory_returns_null_without_credentials() -> None:
    port = build_execution_port(
        _settings(
            trading_mode=TradingMode.LIVE,
            live_armed=True,
            kill_switch=False,
            toobit_api_key="",
            toobit_api_secret="",
        )
    )
    assert isinstance(port, NullExecutionPort)


def test_factory_returns_router_when_live_and_creds() -> None:
    port = build_execution_port(
        _settings(
            trading_mode=TradingMode.LIVE,
            live_armed=True,
            kill_switch=False,
            toobit_api_key=API_KEY,
            toobit_api_secret=SECRET,
        )
    )
    assert not isinstance(port, NullExecutionPort)
    assert type(port).__name__ == "ToobitExecutionRouter"
