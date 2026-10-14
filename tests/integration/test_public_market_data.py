"""Public Toobit market data (no credentials). Opt-in: AEGIS_TEST_PUBLIC_NETWORK=1."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.market_data.depth import spread_bps
from aegis.market_data.rest_client import ToobitRestMarketDataClient
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import InstrumentRef

pytestmark = pytest.mark.integration_public

_ADA = InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT")


async def test_klines_are_ordered_and_closed_bars_are_final() -> None:
    async with ToobitRestMarketDataClient() as client:
        candles = await client.get_klines(_ADA, Timeframe.M5, limit=20)
    assert len(candles) >= 2
    opens = [c.open_time for c in candles]
    assert opens == sorted(opens)
    now = datetime.now(tz=UTC)
    for candle in candles:
        assert candle.close_time > candle.open_time  # kline close-time fix holds live
        assert candle.is_final == (candle.close_time < now)


async def test_depth_top_of_book_is_sane() -> None:
    async with ToobitRestMarketDataClient() as client:
        top = await client.get_depth(_ADA)
    assert top.best_bid > 0
    assert top.best_ask > top.best_bid
    assert spread_bps(top) >= 0
