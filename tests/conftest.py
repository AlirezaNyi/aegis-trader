"""Shared fixtures. Isolate settings from developer .env / shell env."""

from __future__ import annotations

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
    "AEGIS_PAPER_SOAK_ENABLED",
    "AEGIS_PAPER_SOAK_SYMBOL",
    "AEGIS_PAPER_SOAK_INTERVAL",
    "AEGIS_PAPER_SOAK_POLL_SECONDS",
    "DATABASE_URL",
    "TOOBIT_API_KEY",
    "TOOBIT_API_SECRET",
    "TYPESAFE_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    clear_settings_cache()
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # Prevent accidental loading of a developer .env during unit tests.
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    yield
    clear_settings_cache()
