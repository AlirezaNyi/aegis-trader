"""Toobit public REST market-data client (verified endpoints only)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from aegis.interfaces.clock import Clock, SystemClock
from aegis.market_data.constants import (
    KLINES_MAX_LIMIT,
    PATH_EXCHANGE_INFO,
    PATH_KLINES,
    PATH_TIME,
    REST_BASE_URL,
    SUPPORTED_KLINE_INTERVALS,
)
from aegis.market_data.klines import MalformedKlineError, parse_rest_kline_row
from aegis.market_data.metadata import parse_exchange_info
from aegis.market_data.timeutil import datetime_to_ms
from aegis.schemas.common import Timeframe
from aegis.schemas.market import Candle, InstrumentRef
from aegis.schemas.metadata import SymbolMetadata


class ToobitRestMarketDataClient:
    """HTTP client for public Toobit market data. Does not place orders."""

    def __init__(
        self,
        *,
        base_url: str = REST_BASE_URL,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=self._base_url, timeout=timeout_seconds)
        self._clock = clock or SystemClock()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ToobitRestMarketDataClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_server_time(self) -> datetime:
        response = self._client.get(PATH_TIME)
        response.raise_for_status()
        payload = response.json()
        return datetime.fromtimestamp(int(payload["serverTime"]) / 1000.0, tz=UTC)

    def get_exchange_info(self) -> list[SymbolMetadata]:
        response = self._client.get(PATH_EXCHANGE_INFO)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("exchangeInfo response must be an object")
        return parse_exchange_info(payload)

    def get_klines(
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

        response = self._client.get(PATH_KLINES, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("klines response must be a list")

        now = self._clock.now()
        received_at = now
        candles: list[Candle] = []
        for row in payload:
            try:
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
            except MalformedKlineError:
                raise
        return candles
