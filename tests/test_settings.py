"""Settings validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aegis.config.settings import Settings, TradingMode, clear_settings_cache


def test_defaults_are_paper_and_disarmed() -> None:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
    )
    assert settings.trading_mode == TradingMode.PAPER
    assert settings.live_armed is False
    assert settings.kill_switch is False
    assert settings.is_paper_or_dev is True


def test_live_without_arm_fails() -> None:
    with pytest.raises(ValidationError, match="LIVE_ARMED"):
        Settings(
            trading_mode=TradingMode.LIVE,
            live_armed=False,
            kill_switch=False,
        )


def test_live_with_kill_switch_fails() -> None:
    with pytest.raises(ValidationError, match="KILL_SWITCH"):
        Settings(
            trading_mode=TradingMode.LIVE,
            live_armed=True,
            kill_switch=True,
        )


def test_invalid_log_level_fails() -> None:
    with pytest.raises(ValidationError, match="AEGIS_LOG_LEVEL"):
        Settings(log_level="VERBOSE")


def test_live_armed_ok() -> None:
    settings = Settings(
        trading_mode=TradingMode.LIVE,
        live_armed=True,
        kill_switch=False,
        require_database=False,
    )
    assert settings.trading_mode == TradingMode.LIVE
