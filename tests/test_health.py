"""HTTP health/readiness tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aegis.config.settings import Settings, TradingMode
from aegis.main import create_app


def test_health_ok() -> None:
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
    )
    client = TestClient(create_app(settings))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "aegis"


def test_ready_paper_without_db_requirement() -> None:
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
    )
    client = TestClient(create_app(settings))
    response = client.get("/ready")
    body = response.json()
    assert response.status_code == 200
    assert body["ready"] is True
    assert body["trading_mode"] == "paper"
    assert body["live_execution_permitted"] is False
    assert body["live_submit_client"] == "not_constructed"
    assert body["phase"] == 5
