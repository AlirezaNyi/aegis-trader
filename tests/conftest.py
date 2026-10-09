"""Shared fixtures. Isolate settings from developer .env / shell env."""

from __future__ import annotations

import os

import pytest

from aegis.config.settings import clear_settings_cache

_ENV_KEYS = (
    "AEGIS_TRADING_MODE",
    "AEGIS_LIVE_ARMED",
    "AEGIS_KILL_SWITCH",
    "AEGIS_LOG_LEVEL",
    "AEGIS_REQUIRE_DATABASE",
    "AEGIS_LLM_PROVIDER",
    "AEGIS_LLM_API_KEY",
    "AEGIS_LLM_MODEL",
    "AEGIS_LLM_TOKEN_BUDGET",
    "AEGIS_LLM_COST_BUDGET",
    "AEGIS_LLM_LATENCY_BUDGET_MS",
    "AEGIS_PAPER_SOAK_ENABLED",
    "AEGIS_PAPER_SOAK_SYMBOL",
    "AEGIS_PAPER_SOAK_INTERVAL",
    "AEGIS_PAPER_SOAK_POLL_SECONDS",
    "DATABASE_URL",
    "TOOBIT_API_KEY",
    "TOOBIT_API_SECRET",
    "TYPESAFE_API_KEY",
)


def pytest_configure(config: pytest.Config) -> None:
    """Neutralize host .env before test modules import ``aegis.main:app``."""
    _ = config
    clear_settings_cache()
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    # Env vars override pydantic env_file; keep import of create_app safe.
    os.environ["AEGIS_TRADING_MODE"] = "paper"
    os.environ["AEGIS_LIVE_ARMED"] = "false"
    os.environ["AEGIS_KILL_SWITCH"] = "false"
    os.environ["AEGIS_REQUIRE_DATABASE"] = "false"
    os.environ["AEGIS_PAPER_SOAK_ENABLED"] = "false"
    os.environ["AEGIS_PAPER_SOAK_SYMBOL"] = "ADAUSDT"


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    clear_settings_cache()
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # Prevent accidental loading of a developer .env during unit tests.
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    yield
    clear_settings_cache()
