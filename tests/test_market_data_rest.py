"""REST market-data client tests with httpx MockTransport."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from aegis.interfaces.clock import Clock
from aegis.market_data.gateway import MarketDataGateway
from aegis.market_data.klines import MalformedKlineError
from aegis.market_data.rest_client import ToobitRestMarketDataClient
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import InstrumentRef


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/api/v1/time"):
        return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
    if path.endswith("/api/v1/exchangeInfo"):
        return httpx.Response(
            200,
            json={
                "symbols": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "TRADING",
                        "baseAsset": "ETH",
                        "quoteAsset": "USDT",
                        "baseAssetPrecision": "0.0001",
                        "quotePrecision": "0.01",
                        "filters": [
                            {
                                "filterType": "PRICE_FILTER",
                                "tickSize": "0.01",
                                "minPrice": "0.01",
                                "maxPrice": "100000",
                            },
                            {
                                "filterType": "LOT_SIZE",
                                "stepSize": "0.0001",
                                "minQty": "0.0001",
                                "maxQty": "4000",
                            },
                            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                        ],
                    }
                ],
                "contracts": [
                    {
                        "symbol": "BTC-SWAP-USDT",
                        "status": "TRADING",
                        "baseAsset": "BTC-SWAP-USDT",
                        "quoteAsset": "USDT",
                        "quoteAssetPrecision": "0.1",
                        "contractMultiplier": "0.0001",
                        "inverse": False,
                        "filters": [
                            {
                                "filterType": "PRICE_FILTER",
                                "tickSize": "0.01",
                                "minPrice": "0.01",
                                "maxPrice": "100000",
                            },
                            {
                                "filterType": "LOT_SIZE",
                                "stepSize": "0.0001",
                                "minQty": "0.0001",
                                "maxQty": "4000",
                            },
                            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                        ],
                    }
                ],
            },
        )
    if path.endswith("/quote/v1/klines"):
        # Two finalized bars + one incomplete relative to FixedClock(2024-01-01 00:03 UTC)
        rows = [
            [
                1_704_067_200_000,
                "100",
                "101",
                "99",
                "100.5",
                "10",
                1_704_067_259_999,
                "1000",
                5,
                "1",
                "1",
            ],
            [
                1_704_067_260_000,
                "100.5",
                "102",
                "100",
                "101",
                "11",
                1_704_067_319_999,
                "1100",
                6,
                "1",
                "1",
            ],
            [
                1_704_067_320_000,
                "101",
                "103",
                "100.5",
                "102",
                "12",
                1_704_067_379_999,
                "1200",
                7,
                "1",
                "1",
            ],
        ]
        return httpx.Response(200, json=rows)
    return httpx.Response(404, json={"error": "missing"})


def test_exchange_info_spot_and_futures() -> None:
    transport = httpx.MockTransport(_handler)
    client = httpx.Client(transport=transport, base_url="https://api.toobit.com")
    clock = FixedClock(datetime(2024, 1, 1, tzinfo=UTC))
    rest = ToobitRestMarketDataClient(client=client, clock=clock)
    meta = rest.get_exchange_info()
    symbols = {m.symbol: m for m in meta}
    assert symbols["ETHUSDT"].market_type == MarketType.SPOT
    assert symbols["ETHUSDT"].tick_size is not None
    assert symbols["BTC-SWAP-USDT"].market_type == MarketType.FUTURES
    assert symbols["BTC-SWAP-USDT"].contract_multiplier is not None


def test_klines_finalization_and_gateway_quality() -> None:
    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport, base_url="https://api.toobit.com")
    # During the third 1m bar (open 00:02, close ~00:02:59.999) so it is incomplete.
    clock: Clock = FixedClock(datetime(2024, 1, 1, 0, 2, 30, tzinfo=UTC))
    rest = ToobitRestMarketDataClient(client=http, clock=clock)
    gateway = MarketDataGateway(rest, clock=clock)
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    candles = list(gateway.get_candles(instrument, Timeframe.M1, limit=3))
    assert len(candles) == 3
    assert candles[0].is_final is True
    assert candles[1].is_final is True
    assert candles[2].is_final is False
    assert candles[0].open_time < candles[1].open_time
    snap = gateway.freshness(symbol="ETHUSDT", interval=Timeframe.M1)
    assert snap["stale"] is False


def test_malformed_kline_raises() -> None:
    def bad_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/quote/v1/klines"):
            return httpx.Response(200, json=[["not-a-number"]])
        return _handler(request)

    transport = httpx.MockTransport(bad_handler)
    http = httpx.Client(transport=transport, base_url="https://api.toobit.com")
    rest = ToobitRestMarketDataClient(
        client=http, clock=FixedClock(datetime(2024, 1, 1, tzinfo=UTC))
    )
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    with pytest.raises(MalformedKlineError):
        rest.get_klines(instrument, Timeframe.M1, limit=1)
