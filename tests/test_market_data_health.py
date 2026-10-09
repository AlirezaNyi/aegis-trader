"""Readiness includes market-data metrics when configured."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from aegis.config.settings import Settings, TradingMode
from aegis.main import create_app
from aegis.market_data.metrics import MarketDataMetrics


def test_ready_includes_market_data_metrics() -> None:
    settings = Settings(trading_mode=TradingMode.PAPER, require_database=False)
    app = create_app(settings)
    metrics = MarketDataMetrics()
    metrics.record_event(
        received_at=datetime.now(UTC),
        exchange_event_at=datetime.now(UTC),
    )
    app.state.market_data_metrics = metrics
    client = TestClient(app)
    body = client.get("/ready").json()
    assert body["phase"] == 4
    assert body["market_data"]["configured"] is True
    assert body["market_data"]["events_received"] == 1
    assert body["live_submit_client"] == "not_constructed"
