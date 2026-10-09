"""Audit M5: Settings secrets must not sit on app.state or leak via ops HTTP."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from aegis.config.runtime_public import (
    runtime_public_from_settings,
    settings_without_secrets,
)
from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.main import create_app

_SECRET_MARKERS = (
    "toobit_api_key",
    "toobit_api_secret",
    "typesafe_api_key",
    "llm_api_key",
    "super-secret",
    "leak-me",
    "sk-test",
)


def _app_with_secrets() -> object:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        require_database=False,
        toobit_api_key="leak-me-toobit-key",
        toobit_api_secret="super-secret-toobit",
        typesafe_api_key="leak-me-typesafe",
        llm_api_key="sk-test-llm",
        database_url="postgresql+psycopg://user:dbpass@localhost:5432/aegis",
    )
    return create_app(settings)


def test_runtime_public_has_no_secret_fields() -> None:
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        toobit_api_key="x",
        toobit_api_secret="y",
    )
    runtime = runtime_public_from_settings(settings)
    assert not hasattr(runtime, "toobit_api_key")
    assert not hasattr(runtime, "toobit_api_secret")
    assert not hasattr(runtime, "llm_api_key")
    assert runtime.trading_mode == TradingMode.PAPER
    assert runtime.live_armed is False


def test_settings_without_secrets_clears_credentials() -> None:
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        toobit_api_key="keep-out",
        toobit_api_secret="keep-out",
        typesafe_api_key="keep-out",
        llm_api_key="keep-out",
        database_url="postgresql+psycopg://u:p@db:5432/aegis",
    )
    safe = settings_without_secrets(settings)
    assert safe.toobit_api_key == ""
    assert safe.toobit_api_secret == ""
    assert safe.typesafe_api_key == ""
    assert safe.llm_api_key == ""
    assert "redacted" in safe.database_url
    assert settings.toobit_api_key == "keep-out"


def test_create_app_does_not_expose_settings_on_state() -> None:
    app = _app_with_secrets()
    assert not hasattr(app.state, "settings")
    assert hasattr(app.state, "runtime")
    assert hasattr(app.state, "database_probe")
    deps = app.state.paper_cycle_deps
    assert deps.settings.toobit_api_key == ""
    assert deps.settings.toobit_api_secret == ""
    assert deps.settings.llm_api_key == ""
    assert deps.settings.typesafe_api_key == ""
    # Request-adjacent deps share redacted Settings (safety M3).
    assert app.state.paper_broker._settings.toobit_api_secret == ""
    assert app.state.order_manager._settings.toobit_api_key == ""
    assert app.state.order_manager._settings.llm_api_key == ""


def test_ops_http_responses_do_not_leak_secrets() -> None:
    client = TestClient(_app_with_secrets())
    for path in ("/health", "/ready", "/metrics", "/ops/alerts/dry-run"):
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.text.lower()
        for marker in _SECRET_MARKERS:
            assert marker.lower() not in body, f"{path} leaked {marker}"
        if path in {"/health", "/ready", "/ops/alerts/dry-run"}:
            payload = json.dumps(response.json()).lower()
            for marker in _SECRET_MARKERS:
                assert marker.lower() not in payload, f"{path} json leaked {marker}"


def test_ready_still_reports_gates() -> None:
    clear_settings_cache()
    app = create_app(
        Settings(
            trading_mode=TradingMode.PAPER,
            require_database=False,
            kill_switch=True,
        )
    )
    body = TestClient(app).get("/ready").json()
    assert body["ready"] is False
    assert body["kill_switch"] is True
    assert body["trading_mode"] == "paper"
    assert body["live_armed"] is False
    assert "toobit" not in json.dumps(body).lower()
