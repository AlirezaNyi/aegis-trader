"""Live execution gate matrix."""

from __future__ import annotations

import pytest

from aegis.config.settings import Settings, TradingMode
from aegis.guards.live import (
    LiveExecutionNotAllowed,
    assert_live_execution_allowed,
    live_execution_permitted,
    trading_ready,
)


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_paper_not_live_permitted() -> None:
    settings = _settings()
    assert live_execution_permitted(settings) is False
    assert trading_ready(settings) is True
    with pytest.raises(LiveExecutionNotAllowed, match="trading_mode"):
        assert_live_execution_allowed(settings)


def test_live_disarmed() -> None:
    settings = Settings.model_construct(
        trading_mode=TradingMode.LIVE,
        live_armed=False,
        kill_switch=False,
    )
    assert live_execution_permitted(settings) is False
    with pytest.raises(LiveExecutionNotAllowed, match="live_armed"):
        assert_live_execution_allowed(settings)


def test_live_kill_switch() -> None:
    settings = Settings.model_construct(
        trading_mode=TradingMode.LIVE,
        live_armed=True,
        kill_switch=True,
    )
    assert live_execution_permitted(settings) is False
    assert trading_ready(settings) is False
    with pytest.raises(LiveExecutionNotAllowed, match="kill_switch"):
        assert_live_execution_allowed(settings)


def test_live_all_gates_pass() -> None:
    settings = _settings(
        trading_mode=TradingMode.LIVE,
        live_armed=True,
        kill_switch=False,
    )
    assert live_execution_permitted(settings) is True
    assert trading_ready(settings) is True
    assert_live_execution_allowed(settings)
