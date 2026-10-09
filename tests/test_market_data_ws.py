"""WebSocket kline handling, reconnect metrics, malformed frames."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from aegis.market_data.metrics import MarketDataMetrics
from aegis.market_data.ws_client import ToobitWsMarketDataClient
from aegis.schemas.common import MarketType, Timeframe


@pytest.mark.asyncio
async def test_ws_kline_update_and_finalize() -> None:
    metrics = MarketDataMetrics()
    client = ToobitWsMarketDataClient(
        symbols=["BTCUSDT"],
        interval=Timeframe.M1,
        market_type=MarketType.SPOT,
        metrics=metrics,
    )
    received = datetime(2024, 1, 1, 0, 0, 30, tzinfo=UTC)
    msg1 = {
        "symbol": "BTCUSDT",
        "topic": "kline_1m",
        "sendTime": 1_704_067_230_000,
        "data": [
            {
                "t": 1_704_067_200_000,
                "s": "BTCUSDT",
                "sn": "BTCUSDT",
                "o": "100",
                "h": "101",
                "l": "99",
                "c": "100.5",
                "v": "1",
            }
        ],
    }
    await client.handle_message_text(json.dumps(msg1), received_at=received)
    assert len(client.candles) == 1
    assert client.candles[0].is_final is False

    msg2 = {
        "symbol": "BTCUSDT",
        "topic": "kline_1m",
        "sendTime": 1_704_067_290_000,
        "data": [
            {
                "t": 1_704_067_260_000,
                "s": "BTCUSDT",
                "sn": "BTCUSDT",
                "o": "100.5",
                "h": "102",
                "l": "100",
                "c": "101",
                "v": "2",
            }
        ],
    }
    await client.handle_message_text(json.dumps(msg2), received_at=received)
    assert len(client.candles) == 2
    assert client.candles[0].is_final is True
    assert client.candles[1].is_final is False
    assert metrics.events_received == 2


@pytest.mark.asyncio
async def test_ws_malformed_and_duplicate() -> None:
    metrics = MarketDataMetrics()
    client = ToobitWsMarketDataClient(
        symbols=["BTCUSDT"],
        interval=Timeframe.M1,
        metrics=metrics,
    )
    await client.handle_message_text("not-json")
    assert metrics.malformed == 1

    good = {
        "symbol": "BTCUSDT",
        "topic": "kline_1m",
        "sendTime": 1_704_067_230_000,
        "data": [
            {
                "t": 1_704_067_200_000,
                "s": "BTCUSDT",
                "o": "1",
                "h": "1",
                "l": "1",
                "c": "1",
                "v": "1",
            }
        ],
    }
    raw = json.dumps(good)
    await client.handle_message_text(raw)
    await client.handle_message_text(raw)
    assert metrics.duplicates >= 1


def test_subscribe_message_shape() -> None:
    client = ToobitWsMarketDataClient(symbols=["ETHUSDT", "BTCUSDT"], interval=Timeframe.M5)
    msg = client.subscribe_message()
    assert msg["topic"] == "kline_5m"
    assert msg["event"] == "sub"
    assert msg["symbol"] == "ETHUSDT,BTCUSDT"
