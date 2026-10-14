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


async def test_exchange_info_spot_and_futures() -> None:
    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.toobit.com")
    clock = FixedClock(datetime(2024, 1, 1, tzinfo=UTC))
    rest = ToobitRestMarketDataClient(client=client, clock=clock)
    meta = await rest.get_exchange_info()
    symbols = {m.symbol: m for m in meta}
    assert symbols["ETHUSDT"].market_type == MarketType.SPOT
    assert symbols["ETHUSDT"].tick_size is not None
    assert symbols["BTC-SWAP-USDT"].market_type == MarketType.FUTURES
    assert symbols["BTC-SWAP-USDT"].contract_multiplier is not None


async def test_klines_finalization_and_gateway_quality() -> None:
    transport = httpx.MockTransport(_handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.toobit.com")
    # During the third 1m bar (open 00:02, close ~00:02:59.999) so it is incomplete.
    clock: Clock = FixedClock(datetime(2024, 1, 1, 0, 2, 30, tzinfo=UTC))
    rest = ToobitRestMarketDataClient(client=http, clock=clock)
    gateway = MarketDataGateway(rest, clock=clock)
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    candles = list(await gateway.get_candles(instrument, Timeframe.M1, limit=3))
    assert len(candles) == 3
    assert candles[0].is_final is True
    assert candles[1].is_final is True
    assert candles[2].is_final is False
    assert candles[0].open_time < candles[1].open_time
    snap = gateway.freshness(symbol="ETHUSDT", interval=Timeframe.M1)
    assert snap["stale"] is False


async def test_gateway_rest_repoll_keeps_previous_newest_bar() -> None:
    """Historical REST batches must not drop the prior tip via stream-duplicate filter."""
    transport = httpx.MockTransport(_handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.toobit.com")
    clock: Clock = FixedClock(datetime(2024, 1, 1, 0, 2, 30, tzinfo=UTC))
    rest = ToobitRestMarketDataClient(client=http, clock=clock)
    gateway = MarketDataGateway(rest, clock=clock)
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    first = list(await gateway.get_candles(instrument, Timeframe.M1, limit=3))
    second = list(await gateway.get_candles(instrument, Timeframe.M1, limit=3))
    assert len(first) == 3
    assert len(second) == 3
    assert [c.open_time for c in first] == [c.open_time for c in second]
    assert gateway.stream_events() == []
    snap = gateway.freshness(symbol="ETHUSDT", interval=Timeframe.M1)
    assert snap["stale"] is False


async def test_malformed_kline_raises() -> None:
    def bad_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/quote/v1/klines"):
            return httpx.Response(200, json=[["not-a-number"]])
        return _handler(request)

    transport = httpx.MockTransport(bad_handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.toobit.com")
    rest = ToobitRestMarketDataClient(
        client=http, clock=FixedClock(datetime(2024, 1, 1, tzinfo=UTC))
    )
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    with pytest.raises(MalformedKlineError):
        await rest.get_klines(instrument, Timeframe.M1, limit=1)


def test_kline_zero_close_time_is_derived_and_in_progress_bar_not_final() -> None:
    """Toobit returned 0 at index 6 (observed 2026-10-13); never treat that as final."""
    from aegis.market_data.klines import parse_rest_kline_row

    open_ms = 1791916200000  # 2026-10-13T18:30:00Z
    row = [open_ms, "0.2356", "0.2361", "0.2354", "0.2356", "66875.2", 0, "15758.4", 41]
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT")
    mid_bar = datetime(2026, 10, 13, 18, 33, tzinfo=UTC)
    candle = parse_rest_kline_row(
        row, instrument=instrument, interval=Timeframe.M5, received_at=mid_bar, now=mid_bar
    )
    assert candle.close_time == datetime(2026, 10, 13, 18, 34, 59, 999000, tzinfo=UTC)
    assert candle.is_final is False
    after = datetime(2026, 10, 13, 18, 35, 1, tzinfo=UTC)
    final = parse_rest_kline_row(
        row, instrument=instrument, interval=Timeframe.M5, received_at=after, now=after
    )
    assert final.is_final is True


async def test_server_time_and_owned_client_lifecycle() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="https://api.toobit.com"
    )
    async with ToobitRestMarketDataClient(
        client=http, clock=FixedClock(datetime(2024, 1, 1, tzinfo=UTC))
    ) as rest:
        server_time = await rest.get_server_time()
    assert server_time == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    # Injected clients are owned by the caller and stay open after aexit.
    assert http.is_closed is False
    await http.aclose()

    owned = ToobitRestMarketDataClient()
    await owned.aclose()
    assert owned._client.is_closed is True


async def test_gateway_metadata_cached_after_first_fetch() -> None:
    calls: list[str] = []

    def counting(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _handler(request)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(counting), base_url="https://api.toobit.com"
    )
    clock = FixedClock(datetime(2024, 1, 1, tzinfo=UTC))
    gateway = MarketDataGateway(ToobitRestMarketDataClient(client=http, clock=clock), clock=clock)
    first = await gateway.get_metadata()
    second = await gateway.get_metadata()
    assert [m.symbol for m in first] == [m.symbol for m in second]
    assert calls == ["/api/v1/exchangeInfo"]
    await gateway.refresh_metadata()
    assert len(calls) == 2


async def test_http_error_status_propagates_without_retry() -> None:
    calls: list[str] = []

    def failing(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(failing), base_url="https://api.toobit.com"
    )
    clock = FixedClock(datetime(2024, 1, 1, tzinfo=UTC))
    rest = ToobitRestMarketDataClient(client=http, clock=clock)
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    with pytest.raises(httpx.HTTPStatusError):
        await rest.get_klines(instrument, Timeframe.M1, limit=1)
    assert len(calls) == 1
