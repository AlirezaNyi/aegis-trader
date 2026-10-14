"""Toobit public REST market-data client (verified endpoints only)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from aegis.interfaces.clock import Clock, SystemClock
from aegis.market_data.constants import (
    DEPTH_DEFAULT_LIMIT,
    DEPTH_MAX_LIMIT,
    KLINES_MAX_LIMIT,
    PATH_DEPTH,
    PATH_EXCHANGE_INFO,
    PATH_KLINES,
    PATH_TIME,
    REST_BASE_URL,
    SUPPORTED_KLINE_INTERVALS,
)
from aegis.market_data.depth import parse_depth_top
from aegis.market_data.klines import parse_rest_kline_row
from aegis.market_data.metadata import parse_exchange_info
from aegis.market_data.timeutil import datetime_to_ms
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef, OrderBookTop
from aegis.schemas.metadata import SymbolMetadata


class ToobitRestMarketDataClient:
    """Async HTTP client for public Toobit market data. Does not place orders.

    Use as `async with ToobitRestMarketDataClient() as rest:` or call `aclose()`.
    No retries: callers decide whether to re-poll.
    """

    def __init__(
        self,
        *,
        base_url: str = REST_BASE_URL,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout_seconds
        )
        self._clock = clock or SystemClock()

    async def aclose(self) -> None:
        """Close the underlying client only when this instance created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> ToobitRestMarketDataClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def get_server_time(self) -> datetime:
        response = await self._client.get(PATH_TIME)
        response.raise_for_status()
        payload = response.json()
        return datetime.fromtimestamp(int(payload["serverTime"]) / 1000.0, tz=UTC)

    async def get_exchange_info(self) -> list[SymbolMetadata]:
        response = await self._client.get(PATH_EXCHANGE_INFO)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("exchangeInfo response must be an object")
        return parse_exchange_info(payload)

    async def get_klines(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        if interval.value not in SUPPORTED_KLINE_INTERVALS:
            raise ValueError(f"unsupported interval: {interval.value}")
        if limit < 1 or limit > KLINES_MAX_LIMIT:
            raise ValueError(f"limit must be 1..{KLINES_MAX_LIMIT}")

        params: dict[str, Any] = {
            "symbol": instrument.symbol,
            "interval": interval.value,
            "limit": limit,
        }
        # Docs: without start/end only the latest candle is returned.
        if start_time is not None:
            params["startTime"] = datetime_to_ms(start_time)
        if end_time is not None:
            params["endTime"] = datetime_to_ms(end_time)

        response = await self._client.get(PATH_KLINES, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("klines response must be a list")

        now = self._clock.now()
        received_at = now
        candles: list[Candle] = []
        for row in payload:
            # MalformedKlineError propagates: never guess a malformed row.
            candles.append(
                parse_rest_kline_row(
                    row,
                    instrument=instrument,
                    interval=interval,
                    received_at=received_at,
                    now=now,
                    source="rest",
                )
            )
        return candles

    async def get_depth(
        self,
        instrument: InstrumentRef,
        *,
        limit: int = DEPTH_DEFAULT_LIMIT,
    ) -> OrderBookTop:
        """Public `GET /quote/v1/depth`; returns best bid/ask only.

        Raises `MalformedDepthError` / `EmptyOrderBookError` when the payload does
        not match the verified `t`/`b`/`a` shape.
        """
        if limit < 1 or limit > DEPTH_MAX_LIMIT:
            raise ValueError(f"limit must be 1..{DEPTH_MAX_LIMIT}")
        response = await self._client.get(
            PATH_DEPTH,
            params={"symbol": instrument.symbol, "limit": limit},
        )
        response.raise_for_status()
        payload = response.json()
        return parse_depth_top(
            payload,
            instrument=instrument,
            received_at=self._clock.now(),
        )
