"""Toobit public WebSocket market stream client with reconnect/backoff.

Verified: wss://stream.toobit.com/quote/ws/v1
Subscribe with topic kline_$interval, event=sub, JSON ping/pong heartbeat.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from aegis.market_data.constants import WS_BASE_URL, WS_MARKET_PATH, WS_PING_INTERVAL_SECONDS
from aegis.market_data.klines import MalformedKlineError, parse_ws_kline_payload
from aegis.market_data.metrics import MarketDataMetrics
from aegis.market_data.quality import CandleStreamTracker, DataQualityIssueKind
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, NormalizedMarketEvent

logger = logging.getLogger("aegis.market_data.ws")


class ToobitWsMarketDataClient:
    """Async WS client. Transport is injectable for deterministic tests."""

    def __init__(
        self,
        *,
        symbols: Iterable[str],
        interval: Timeframe,
        market_type: MarketType = MarketType.SPOT,
        base_url: str = WS_BASE_URL,
        metrics: MarketDataMetrics | None = None,
        tracker: CandleStreamTracker | None = None,
        connect: Callable[[str], Any] | None = None,
        ping_interval_seconds: float = WS_PING_INTERVAL_SECONDS,
        max_backoff_seconds: float = 30.0,
    ) -> None:
        self._symbols = list(symbols)
        self._interval = interval
        self._market_type = market_type
        self._url = f"{base_url.rstrip('/')}{WS_MARKET_PATH}"
        self.metrics = metrics or MarketDataMetrics()
        self.tracker = tracker or CandleStreamTracker()
        self._connect = connect
        self._ping_interval = ping_interval_seconds
        self._max_backoff = max_backoff_seconds
        self._running = False
        self._last_open_ms: dict[tuple[str, str], int] = {}
        self.candles: list[Candle] = []
        self.events: list[NormalizedMarketEvent] = []

    def subscribe_message(self) -> dict[str, Any]:
        return {
            "symbol": ",".join(self._symbols),
            "topic": f"kline_{self._interval.value}",
            "event": "sub",
            "params": {"binary": False},
        }

    async def run_forever(self) -> None:
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                await self._session()
                backoff = 1.0
            except asyncio.CancelledError:
                self._running = False
                raise
            except Exception:
                logger.exception("websocket session failed; reconnecting")
                self.metrics.mark_connected(False)
                self.metrics.record_reconnect()
                await asyncio.sleep(backoff)
                backoff = min(self._max_backoff, backoff * 2)

    def stop(self) -> None:
        self._running = False

    async def handle_message_text(self, raw: str, *, received_at: datetime | None = None) -> None:
        """Process one WS text frame (also used by unit tests)."""
        await self._handle_raw(raw, received_at=received_at or datetime.now(UTC))

    async def _session(self) -> None:
        if self._connect is None:
            import websockets

            connect_cm = websockets.connect(self._url)
        else:
            connect_cm = self._connect(self._url)

        async with connect_cm as ws:
            self.metrics.mark_connected(True)
            await ws.send(json.dumps(self.subscribe_message()))
            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    if not self._running:
                        break
                    await self._handle_raw(raw)
            finally:
                ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ping_task
                self.metrics.mark_connected(False)

    async def _ping_loop(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)
            payload = {"ping": int(datetime.now(UTC).timestamp() * 1000)}
            await ws.send(json.dumps(payload))

    async def _handle_raw(self, raw: str | bytes, *, received_at: datetime | None = None) -> None:
        received = received_at or datetime.now(UTC)
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.metrics.record_malformed()
            return

        if not isinstance(message, dict):
            self.metrics.record_malformed()
            return

        if "pong" in message or "ping" in message:
            return

        topic = str(message.get("topic", ""))
        symbol = str(message.get("symbol", ""))
        send_time = message.get("sendTime")
        exchange_event_at = (
            datetime.fromtimestamp(int(send_time) / 1000.0, tz=UTC) if send_time else None
        )
        self.metrics.record_event(received_at=received, exchange_event_at=exchange_event_at)
        self.events.append(
            NormalizedMarketEvent(
                market_type=self._market_type,
                symbol=symbol or ",".join(self._symbols),
                event_time=exchange_event_at or received,
                received_at=received,
                channel=topic or f"kline_{self._interval.value}",
                payload=message,
            )
        )

        if not topic.startswith("kline"):
            return

        data = message.get("data") or []
        if not isinstance(data, list):
            self.metrics.record_malformed()
            return

        for item in data:
            if not isinstance(item, dict):
                self.metrics.record_malformed()
                continue
            try:
                open_ms = int(item["t"])
                key = (str(item.get("s", symbol)), self._interval.value)
                prev_open = self._last_open_ms.get(key)
                if prev_open is not None and open_ms > prev_open:
                    self._finalize_open(key[0], prev_open)

                candle = parse_ws_kline_payload(
                    item,
                    market_type=self._market_type,
                    interval=self._interval,
                    received_at=received,
                    is_final=False,
                )
                self._last_open_ms[key] = open_ms
                issues = self.tracker.observe(candle)
                for issue in issues:
                    if issue.kind == DataQualityIssueKind.GAP:
                        self.metrics.record_gap()
                    elif issue.kind == DataQualityIssueKind.DUPLICATE:
                        self.metrics.record_duplicate()
                    elif issue.kind == DataQualityIssueKind.OUT_OF_ORDER:
                        self.metrics.record_out_of_order()
                self.candles.append(candle)
            except (MalformedKlineError, KeyError, TypeError, ValueError):
                self.metrics.record_malformed()

    def _finalize_open(self, symbol: str, open_ms: int) -> None:
        open_dt = datetime.fromtimestamp(open_ms / 1000.0, tz=UTC)
        for idx in range(len(self.candles) - 1, -1, -1):
            candle = self.candles[idx]
            if (
                candle.instrument.symbol == symbol
                and candle.interval == self._interval
                and candle.open_time == open_dt
                and not candle.is_final
            ):
                self.candles[idx] = candle.model_copy(update={"is_final": True})
                break
