"""Verified Toobit market-data endpoints (docs checked 2026-10-09, re-checked Phase 2).

Source: https://api-docs.toobit.com/api/spot-market-data
        https://api-docs.toobit.com/api/spot-websocket-market-data
"""

from __future__ import annotations

REST_BASE_URL = "https://api.toobit.com"
WS_BASE_URL = "wss://stream.toobit.com"
WS_MARKET_PATH = "/quote/ws/v1"

PATH_TIME = "/api/v1/time"
PATH_EXCHANGE_INFO = "/api/v1/exchangeInfo"
PATH_KLINES = "/quote/v1/klines"
PATH_DEPTH = "/quote/v1/depth"
PATH_TRADES = "/quote/v1/trades"

# Public market data does not require API keys per official docs.
SUPPORTED_KLINE_INTERVALS = frozenset({"1m", "5m", "15m"})

# Docs: without startTime/endTime, only the latest kline is returned.
# Historical range requires startTime and/or endTime; limit max 1000.
KLINES_MAX_LIMIT = 1000

# Client must send ping; server disconnects within ~5 minutes without heartbeat.
WS_PING_INTERVAL_SECONDS = 30.0
WS_MAX_INCOMING_MESSAGES_PER_SECOND = 5  # documented WS control-message limit
