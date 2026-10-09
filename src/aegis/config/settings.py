"""Typed application settings with paper-first defaults."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    DEVELOPMENT = "development"
    PAPER = "paper"
    LIVE = "live"


def _empty_str_to_none(value: Any) -> Any:
    """Treat blank env values as unset optional budgets."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


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

    jev_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices("AEGIS_JEV_TIMEOUT_SECONDS", "jev_timeout_seconds"),
        gt=0,
    )
    jev_max_retries: int = Field(
        default=1,
        validation_alias=AliasChoices("AEGIS_JEV_MAX_RETRIES", "jev_max_retries"),
        ge=0,
        le=1,
    )
    jev_model: str = Field(
        default="jev-latest",
        validation_alias=AliasChoices("AEGIS_JEV_MODEL", "jev_model"),
    )

    supervisor_timeout_seconds: float = Field(
        default=15.0,
        validation_alias=AliasChoices(
            "AEGIS_SUPERVISOR_TIMEOUT_SECONDS", "supervisor_timeout_seconds"
        ),
        gt=0,
    )
    llm_token_budget: int | None = Field(
        default=None,
        validation_alias=AliasChoices("AEGIS_LLM_TOKEN_BUDGET", "llm_token_budget"),
    )
    llm_cost_budget: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("AEGIS_LLM_COST_BUDGET", "llm_cost_budget"),
    )
    llm_latency_budget_ms: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AEGIS_LLM_LATENCY_BUDGET_MS", "llm_latency_budget_ms"
        ),
    )

    # When False, readiness skips DB connectivity (unit tests).
    require_database: bool = Field(
        default=True,
        validation_alias=AliasChoices("AEGIS_REQUIRE_DATABASE", "require_database"),
    )

    # Paper / backtest simulation knobs — engineering assumptions only, NOT risk policy.
    paper_fee_bps: Decimal = Field(
        default=Decimal("10"),
        validation_alias=AliasChoices("AEGIS_PAPER_FEE_BPS", "paper_fee_bps"),
        ge=0,
        description="Simulated fee in basis points (paper/backtest only).",
    )
    paper_slippage_bps: Decimal = Field(
        default=Decimal("5"),
        validation_alias=AliasChoices("AEGIS_PAPER_SLIPPAGE_BPS", "paper_slippage_bps"),
        ge=0,
        description="Simulated slippage in basis points (paper/backtest only).",
    )

    # Exchange transport bounds — engineering only, NOT risk-policy numbers.
    exchange_recv_window_ms: int = Field(
        default=5000,
        validation_alias=AliasChoices(
            "AEGIS_EXCHANGE_RECV_WINDOW_MS", "exchange_recv_window_ms"
        ),
        ge=1,
        le=60000,
        description="Toobit SIGNED recvWindow (ms); verified max 60000.",
    )
    exchange_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "AEGIS_EXCHANGE_TIMEOUT_SECONDS", "exchange_timeout_seconds"
        ),
        gt=0,
        description="HTTP timeout for live exchange adapter (engineering bound).",
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

    @field_validator(
        "llm_token_budget",
        "llm_cost_budget",
        "llm_latency_budget_ms",
        mode="before",
    )
    @classmethod
    def _optional_budget_empty_as_none(cls, value: Any) -> Any:
        return _empty_str_to_none(value)

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

    @model_validator(mode="after")
    def _validate_optional_budgets_positive(self) -> Settings:
        """When a budget is set, it must be strictly positive (empty remains fail-closed)."""
        if self.llm_token_budget is not None and self.llm_token_budget <= 0:
            msg = "AEGIS_LLM_TOKEN_BUDGET must be > 0 when set"
            raise ValueError(msg)
        if self.llm_cost_budget is not None and self.llm_cost_budget <= 0:
            msg = "AEGIS_LLM_COST_BUDGET must be > 0 when set"
            raise ValueError(msg)
        if self.llm_latency_budget_ms is not None and self.llm_latency_budget_ms <= 0:
            msg = "AEGIS_LLM_LATENCY_BUDGET_MS must be > 0 when set"
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

    @property
    def llm_configured(self) -> bool:
        return bool(
            self.llm_provider.strip()
            and self.llm_api_key.strip()
            and self.llm_model.strip()
        )

    @property
    def supervisor_budgets_configured(self) -> bool:
        return (
            self.llm_token_budget is not None
            and self.llm_cost_budget is not None
            and self.llm_latency_budget_ms is not None
        )

    @property
    def supervisor_may_call_llm(self) -> bool:
        return self.llm_configured and self.supervisor_budgets_configured


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
