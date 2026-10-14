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


def test_paper_initial_balance_optional_and_positive() -> None:
    import pytest
    from pydantic import ValidationError

    from aegis.config.settings import Settings, TradingMode

    base = {"trading_mode": TradingMode.PAPER, "require_database": False}
    assert Settings(**base, paper_initial_balance="").paper_initial_balance is None
    assert str(Settings(**base, paper_initial_balance="25").paper_initial_balance) == "25"
    with pytest.raises(ValidationError):
        Settings(**base, paper_initial_balance="0")


def test_risk_percent_settings_validation() -> None:
    import pytest
    from pydantic import ValidationError

    from aegis.config.settings import Settings, TradingMode

    base = {"trading_mode": TradingMode.PAPER, "require_database": False}
    s = Settings(
        **base,
        risk_position_pct="20",
        risk_per_trade_pct="1",
        risk_daily_loss_pct="3",
        risk_weekly_loss_pct="6",
        risk_monthly_loss_pct="10",
        risk_max_drawdown_pct="15",
        risk_min_reward_risk="1.5",
        trading_fee_bps="",
    )
    assert str(s.risk_weekly_loss_pct) == "6"
    assert s.trading_fee_bps is None
    assert s.effective_fee_bps == s.paper_fee_bps
    with pytest.raises(ValidationError):
        Settings(**base, risk_daily_loss_pct="5", risk_weekly_loss_pct="4")
    with pytest.raises(ValidationError):
        Settings(**base, risk_weekly_loss_pct="8", risk_monthly_loss_pct="7")
    with pytest.raises(ValidationError):
        Settings(**base, risk_position_pct="0")
    with pytest.raises(ValidationError):
        Settings(**base, risk_position_pct="101")
    with pytest.raises(ValidationError):
        Settings(**base, risk_position_pct="5", risk_per_trade_pct="6")
    with pytest.raises(ValidationError):
        Settings(**base, risk_min_reward_risk="0")
    from decimal import Decimal

    assert Settings(**base, trading_fee_bps="7.5").effective_fee_bps == Decimal("7.5")


# Every env var name the owner may already use in .env. Changing settings internals
# (aliases vs env_prefix) must never rename these.
_EXPECTED_ENV_NAMES = {
    "database_url": "DATABASE_URL",
    "toobit_api_key": "TOOBIT_API_KEY",
    "toobit_api_secret": "TOOBIT_API_SECRET",
    "typesafe_api_key": "TYPESAFE_API_KEY",
}


def test_every_setting_reads_its_documented_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegis.config.settings import Settings, clear_settings_cache

    clear_settings_cache()
    for name in Settings.model_fields:
        if name == "require_database":
            continue  # passed explicitly below; covered by its own tests
        env = _EXPECTED_ENV_NAMES.get(name, "AEGIS_" + name.upper())
        field = Settings.model_fields[name]
        sample = {
            "database_url": "postgresql+psycopg://u:p@h:1/d",
        }.get(name)
        if sample is None:
            # Re-use the field default rendered as text where possible.
            default = field.default
            if default is None or not isinstance(default, (str, int, float, bool)):
                continue
            sample = str(default).lower() if isinstance(default, bool) else str(
                getattr(default, "value", default)
            )
        monkeypatch.setenv(env, sample)
        settings = Settings(_env_file=None, require_database=False)
        got = getattr(settings, name)
        rendered = str(getattr(got, "value", got))
        if isinstance(got, bool):
            rendered = str(got).lower()
        assert rendered == sample, (name, env, rendered, sample)
        monkeypatch.delenv(env)


def test_non_prefixed_secret_env_names_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegis.config.settings import Settings

    monkeypatch.setenv("TOOBIT_API_KEY", "k-placeholder")
    monkeypatch.setenv("TYPESAFE_API_KEY", "t-placeholder")
    monkeypatch.setenv("AEGIS_RISK_WEEKLY_LOSS_PCT", "6")
    settings = Settings(_env_file=None, require_database=False)
    assert settings.toobit_api_key == "k-placeholder"
    assert settings.typesafe_api_key == "t-placeholder"
    assert str(settings.risk_weekly_loss_pct) == "6"


def test_legacy_unprefixed_safety_keys_detected(tmp_path: object) -> None:
    from pathlib import Path

    from aegis.config.legacy_env import find_legacy_keys
    from aegis.config.settings import Settings

    env_file = Path(str(tmp_path)) / ".env"
    env_file.write_text("# comment\nKILL_SWITCH=true\nexport LLM_MODEL=x\nTOOBIT_API_KEY=k\n")
    safety, other = find_legacy_keys(
        Settings.model_fields, environ={"live_armed": "false"}, env_file=env_file
    )
    assert safety == ["KILL_SWITCH", "LIVE_ARMED"]
    assert other == ["LLM_MODEL"]
    clean_safety, clean_other = find_legacy_keys(
        Settings.model_fields, environ={"AEGIS_KILL_SWITCH": "true"}, env_file=None
    )
    assert clean_safety == [] and clean_other == []
