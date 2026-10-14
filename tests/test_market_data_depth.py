"""Public depth (order book) parsing and spread math."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from aegis.market_data.depth import (
    EmptyOrderBookError,
    MalformedDepthError,
    parse_depth_top,
    spread_bps,
)
from aegis.market_data.gateway import MarketDataGateway
from aegis.market_data.rest_client import ToobitRestMarketDataClient
from aegis.schemas.common import MarketType
from aegis.schemas.market import InstrumentRef, OrderBookTop

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
ADA = InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT")

# Shape captured from a real public request on 2026-10-13.
LIVE_SAMPLE = {
    "t": 1791917383472,
    "b": [["0.2365", "34491.8"], ["0.2364", "165942.4"], ["0.2363", "250209.2"]],
    "a": [["0.2366", "72941.8"], ["0.2367", "70399.7"], ["0.2368", "169831.8"]],
}


class FixedClock:
    def now(self) -> datetime:
        return NOW


def test_parse_live_sample_top_of_book() -> None:
    top = parse_depth_top(LIVE_SAMPLE, instrument=ADA, received_at=NOW)
    assert top.best_bid == Decimal("0.2365")
    assert top.best_bid_qty == Decimal("34491.8")
    assert top.best_ask == Decimal("0.2366")
    assert top.best_ask_qty == Decimal("72941.8")
    assert top.exchange_time == datetime.fromtimestamp(1791917383.472, tz=UTC)
    assert top.received_at == NOW


def test_best_levels_independent_of_sort_order() -> None:
    # Docs example lists levels ascending; live data showed bids descending.
    payload = {"t": 1, "b": [["1.0", "1"], ["3.0", "1"], ["2.0", "1"]],
               "a": [["5.0", "1"], ["4.0", "2"], ["6.0", "1"]]}
    top = parse_depth_top(payload, instrument=ADA, received_at=NOW)
    assert top.best_bid == Decimal("3.0")
    assert top.best_ask == Decimal("4.0")
    assert top.best_ask_qty == Decimal("2")


def test_spread_bps_math() -> None:
    top = parse_depth_top(LIVE_SAMPLE, instrument=ADA, received_at=NOW)
    expected = (Decimal("0.0001") / Decimal("0.23655")) * Decimal("10000")
    assert spread_bps(top) == expected
    assert Decimal("4.2") < spread_bps(top) < Decimal("4.3")


def test_spread_bps_locked_book_is_zero() -> None:
    top = OrderBookTop(
        instrument=ADA,
        best_bid=Decimal("100"),
        best_bid_qty=Decimal("1"),
        best_ask=Decimal("100"),
        best_ask_qty=Decimal("1"),
        received_at=NOW,
    )
    assert spread_bps(top) == Decimal("0")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"t": 1, "a": [["1", "1"]]},
        {"t": 1, "b": [["1", "1"]]},
        {"t": 1, "b": "x", "a": [["1", "1"]]},
        {"t": 1, "b": [["1"]], "a": [["2", "1"]]},
        {"t": 1, "b": [["abc", "1"]], "a": [["2", "1"]]},
        {"t": 1, "b": [["0", "1"]], "a": [["2", "1"]]},
        {"t": 1, "b": [["3", "1"]], "a": [["2", "1"]]},  # crossed
        {"t": "nope", "b": [["1", "1"]], "a": [["2", "1"]]},
        {"bids": [["1", "1"]], "asks": [["2", "1"]]},  # unverified key names
    ],
)
def test_malformed_depth_rejected(payload: object) -> None:
    with pytest.raises(MalformedDepthError):
        parse_depth_top(payload, instrument=ADA, received_at=NOW)


def test_empty_side_is_typed_error() -> None:
    with pytest.raises(EmptyOrderBookError):
        parse_depth_top({"t": 1, "b": [], "a": [["1", "1"]]}, instrument=ADA, received_at=NOW)


def _client(captured: list[httpx.Request]) -> ToobitRestMarketDataClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.url.path == "/quote/v1/depth"
        assert "X-BB-APIKEY" not in request.headers
        return httpx.Response(200, json=LIVE_SAMPLE)

    http = httpx.AsyncClient(
        base_url="https://api.toobit.com", transport=httpx.MockTransport(handler)
    )
    return ToobitRestMarketDataClient(client=http, clock=FixedClock())


async def test_rest_get_depth_params_and_parse() -> None:
    captured: list[httpx.Request] = []
    client = _client(captured)
    top = await client.get_depth(ADA, limit=5)
    assert top.best_bid == Decimal("0.2365")
    assert top.received_at == NOW
    assert captured[0].url.params["symbol"] == "ADAUSDT"
    assert captured[0].url.params["limit"] == "5"


@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_rest_get_depth_limit_bounds(limit: int) -> None:
    client = _client([])
    with pytest.raises(ValueError):
        await client.get_depth(ADA, limit=limit)


async def test_gateway_order_book_top() -> None:
    captured: list[httpx.Request] = []
    gateway = MarketDataGateway(_client(captured), clock=FixedClock())
    top = await gateway.get_order_book_top(ADA)
    assert top.best_ask == Decimal("0.2366")
    assert captured[0].url.params["limit"] == "5"
