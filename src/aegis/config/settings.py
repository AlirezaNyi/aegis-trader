"""Typed application settings with paper-first defaults."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    DEVELOPMENT = "development"
    PAPER = "paper"
    LIVE = "live"


class Settings(BaseSettings):
    """Runtime configuration. Secrets must come from the environment, never from source."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    trading_mode: TradingMode = Field(
        default=TradingMode.PAPER,
        validation_alias=AliasChoices("AEGIS_TRADING_MODE", "trading_mode"),
    )
    live_armed: bool = Field(
        default=False,
        validation_alias=AliasChoices("AEGIS_LIVE_ARMED", "live_armed"),
    )
    kill_switch: bool = Field(
        default=False,
        validation_alias=AliasChoices("AEGIS_KILL_SWITCH", "kill_switch"),
    )

    database_url: str = Field(
        default="postgresql+psycopg://aegis:aegis@localhost:5432/aegis",
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )

    toobit_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("TOOBIT_API_KEY", "toobit_api_key"),
    )
    toobit_api_secret: str = Field(
        default="",
        validation_alias=AliasChoices("TOOBIT_API_SECRET", "toobit_api_secret"),
    )

    typesafe_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("TYPESAFE_API_KEY", "typesafe_api_key"),
    )

    llm_provider: str = Field(
        default="",
        validation_alias=AliasChoices("AEGIS_LLM_PROVIDER", "llm_provider"),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("AEGIS_LLM_API_KEY", "llm_api_key"),
    )
    llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("AEGIS_LLM_MODEL", "llm_model"),
    )

    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("AEGIS_LOG_LEVEL", "log_level"),
    )

    analyst_timeout_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices(
            "AEGIS_ANALYST_TIMEOUT_SECONDS", "analyst_timeout_seconds"
        ),
        gt=0,
    )
    analyst_concurrency: int = Field(
        default=5,
        validation_alias=AliasChoices("AEGIS_ANALYST_CONCURRENCY", "analyst_concurrency"),
        ge=1,
        le=5,
    )

    # When False, readiness skips DB connectivity (unit tests).
    require_database: bool = Field(
        default=True,
        validation_alias=AliasChoices("AEGIS_REQUIRE_DATABASE", "require_database"),
    )

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            msg = f"AEGIS_LOG_LEVEL must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return normalized

    @model_validator(mode="after")
    def _validate_live_startup(self) -> Settings:
        if self.trading_mode == TradingMode.LIVE and not self.live_armed:
            msg = (
                "AEGIS_TRADING_MODE=live requires AEGIS_LIVE_ARMED=true. "
                "Paper trading is the default; live trading must be explicitly armed."
            )
            raise ValueError(msg)
        if self.trading_mode == TradingMode.LIVE and self.kill_switch:
            msg = "AEGIS_TRADING_MODE=live cannot start with AEGIS_KILL_SWITCH=true."
            raise ValueError(msg)
        return self

    @property
    def is_paper_or_dev(self) -> bool:
        return self.trading_mode in {TradingMode.PAPER, TradingMode.DEVELOPMENT}

    @property
    def jev_configured(self) -> bool:
        return bool(self.typesafe_api_key.strip())

    @property
    def toobit_credentials_present(self) -> bool:
        return bool(self.toobit_api_key.strip() and self.toobit_api_secret.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
