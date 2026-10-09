"""Build ExecutionPort — Null unless live gates + credentials are present."""

from __future__ import annotations

import httpx

from aegis.config.settings import Settings
from aegis.exchange.futures_client import ToobitFuturesExecutionClient
from aegis.exchange.paths import REST_BASE_URL
from aegis.exchange.router import ToobitExecutionRouter
from aegis.exchange.spot_client import ToobitSpotExecutionClient
from aegis.guards.live import live_execution_permitted
from aegis.interfaces.execution import ExecutionPort, NullExecutionPort


def build_execution_port(
    settings: Settings,
    *,
    client: httpx.Client | None = None,
) -> ExecutionPort:
    """Return live router only when live_execution_permitted and credentials exist.

    create_app must not construct a live submit client by default in Phase 7 —
    callers (parent wiring) decide when to invoke this factory. Default path is
    NullExecutionPort.
    """
    if not live_execution_permitted(settings):
        return NullExecutionPort()
    if not settings.toobit_credentials_present:
        return NullExecutionPort()

    shared = client
    spot = ToobitSpotExecutionClient(
        api_key=settings.toobit_api_key,
        api_secret=settings.toobit_api_secret,
        client=shared,
        base_url=REST_BASE_URL,
        recv_window_ms=settings.exchange_recv_window_ms,
        timeout_seconds=settings.exchange_timeout_seconds,
    )
    futures = ToobitFuturesExecutionClient(
        api_key=settings.toobit_api_key,
        api_secret=settings.toobit_api_secret,
        client=shared,
        base_url=REST_BASE_URL,
        recv_window_ms=settings.exchange_recv_window_ms,
        timeout_seconds=settings.exchange_timeout_seconds,
    )
    return ToobitExecutionRouter(spot=spot, futures=futures)
