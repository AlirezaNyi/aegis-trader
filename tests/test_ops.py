"""Phase 8 ops metrics, alerts dry-run, correlation, shutdown readiness."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aegis.config.settings import Settings, TradingMode
from aegis.main import create_app
from aegis.ops.alerts import AlertEvaluator
from aegis.ops.metrics import OpsMetrics


def test_metrics_scrape_and_alerts_dry_run() -> None:
    settings = Settings(trading_mode=TradingMode.PAPER, require_database=False)
    client = TestClient(create_app(settings))

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    assert "aegis_ops_info" in metrics.text
    assert "aegis_kill_switch_active 0" in metrics.text

    dry = client.get("/ops/alerts/dry-run")
    assert dry.status_code == 200
    body = dry.json()
    assert body["channel"] == "dry_run"
    assert body["notification_delivered"] is False
    names = {s["name"] for s in body["signals"]}
    assert "stale_market_data" in names
    assert "emergency_stop" in names
    assert "unknown_order_state" in names


def test_alert_evaluator_fires_on_unknown_and_kill() -> None:
    ops = OpsMetrics()
    ops.record_order("unknown")
    ops.set_kill_switch(True)
    ops.set_disk_free(100)
    result = AlertEvaluator().dry_run(ops)
    firing = {s["name"] for s in result["signals"] if s["firing"]}
    assert "unknown_order_state" in firing
    assert "emergency_stop" in firing
    assert "disk_exhaustion" in firing


def test_correlation_id_middleware_echo() -> None:
    settings = Settings(trading_mode=TradingMode.PAPER, require_database=False)
    client = TestClient(create_app(settings))
    response = client.get("/health", headers={"X-Correlation-Id": "corr-test-1"})
    assert response.status_code == 200
    assert response.headers.get("x-correlation-id") == "corr-test-1"


def test_ready_false_when_shutting_down() -> None:
    settings = Settings(trading_mode=TradingMode.PAPER, require_database=False)
    app = create_app(settings)
    client = TestClient(app)
    assert client.get("/ready").json()["ready"] is True
    app.state.shutdown_gate.begin_shutdown()
    body = client.get("/ready").json()
    assert body["ready"] is False
    assert body["shutting_down"] is True
    assert body["phase"] == 8


def test_ready_false_when_kill_switch_in_paper() -> None:
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
        kill_switch=True,
    )
    client = TestClient(create_app(settings))
    body = client.get("/ready").json()
    assert body["ready"] is False
    assert body["kill_switch"] is True


def test_compose_declares_resource_limits() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "mem_limit: 4g" in text
    assert "mem_limit: 2g" in text
    assert "AEGIS_TRADING_MODE: paper" in text
    assert 'AEGIS_LIVE_ARMED: "false"' in text
    # Safety gates must not be commented out (host .env may set live).
    assert "#AEGIS_TRADING_MODE" not in text
    assert "#AEGIS_LIVE_ARMED" not in text
    assert "healthcheck:" in text
    assert "env_file:" in text
    assert "- .env" in text
    # Must not blank credentials after env_file (secrets come from host .env).
    assert 'TOOBIT_API_KEY: ""' not in text
    assert 'TOOBIT_API_SECRET: ""' not in text
    # Compose DB host must stay on the postgres service, not host localhost.
    assert "postgresql+psycopg://aegis:aegis@postgres:5432/aegis" in text
