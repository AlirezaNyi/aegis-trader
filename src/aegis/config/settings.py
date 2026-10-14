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

    # Env vars are AEGIS_<FIELD_NAME>; only DATABASE_URL and the three external
    # credential names keep explicit aliases (see tests/test_settings.py guard).
    model_config = SettingsConfigDict(
        env_prefix="AEGIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    trading_mode: TradingMode = Field(
        default=TradingMode.PAPER,
    )
    live_armed: bool = Field(
        default=False,
    )
    kill_switch: bool = Field(
        default=False,
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
        description=(
            "Owner-selected provider: gemini | openrouter | groq "
            "(empty → UnavailableLlmPort)."
        ),
    )
    llm_api_key: str = Field(
        default="",
    )
    llm_model: str = Field(
        default="",
    )
    llm_base_url: str = Field(
        default="",
        description="Optional OpenAI-compatible base URL override (no trailing path).",
    )
    llm_http_referer: str = Field(
        default="",
        description="Optional OpenRouter HTTP-Referer header (non-secret).",
    )
    llm_app_title: str = Field(
        default="Aegis",
        description="Optional OpenRouter X-Title header (non-secret).",
    )

    log_level: str = Field(
        default="INFO",
    )

    analyst_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
    )
    analyst_concurrency: int = Field(
        default=5,
        ge=1,
        le=5,
    )

    jev_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
    )
    jev_max_retries: int = Field(
        default=1,
        ge=0,
        le=1,
    )
    jev_model: str = Field(
        default="jev-latest",
    )

    supervisor_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
    )
    llm_token_budget: int | None = Field(
        default=None,
    )
    llm_cost_budget: Decimal | None = Field(
        default=None,
    )
    llm_latency_budget_ms: int | None = Field(
        default=None,
    )
    llm_daily_call_cap: int | None = Field(
        default=None,
        description="Owner-set max real Supervisor LLM calls per UTC day (empty = no cap).",
    )

    # When False, readiness skips DB connectivity (unit tests).
    require_database: bool = Field(
        default=True,
    )

    # Paper / backtest simulation knobs — engineering assumptions only, NOT risk policy.
    paper_fee_bps: Decimal = Field(
        default=Decimal("10"),
        ge=0,
        description="Simulated fee in basis points (paper/backtest only).",
    )
    paper_slippage_bps: Decimal = Field(
        default=Decimal("5"),
        ge=0,
        description="Simulated slippage in basis points (paper/backtest only).",
    )
    paper_initial_balance: Decimal | None = Field(
        default=None,
        gt=0,
        description=(
            "Owner-set simulated starting quote balance (USDT) for the paper ledger. "
            "Unset = 0, so approved paper orders fail for insufficient balance."
        ),
    )

    # Owner-set risk limits for policy version 1.1 ("1.1-env"). All percentages are
    # of the initial capital base (paper: AEGIS_PAPER_INITIAL_BALANCE). No defaults:
    # when policy 1.1 is selected and a required value is empty, the parameter stays
    # UNAPPROVED and the Risk Engine rejects every new trade (fail closed).
    risk_position_pct: Decimal | None = Field(
        default=None,
        description="Max notional per position, % of initial capital.",
    )
    risk_per_trade_pct: Decimal | None = Field(
        default=None,
        description="Optional: loss if the stop is hit, % of initial capital (sizes by stop).",
    )
    risk_daily_loss_pct: Decimal | None = Field(
        default=None,
        description="Daily (UTC) equity loss limit, % of initial capital.",
    )
    risk_weekly_loss_pct: Decimal | None = Field(
        default=None,
        description="Weekly (ISO week, Monday 00:00 UTC) equity loss limit, % of initial capital.",
    )
    risk_monthly_loss_pct: Decimal | None = Field(
        default=None,
        description="Monthly (calendar, UTC) equity loss limit, % of initial capital.",
    )
    risk_max_drawdown_pct: Decimal | None = Field(
        default=None,
        description="Max drawdown from peak equity, % of initial capital.",
    )
    risk_min_reward_risk: Decimal | None = Field(
        default=None,
        description="Minimum reward:risk after round-trip costs (e.g. 1.5 means 1.5R).",
    )
    trading_fee_bps: Decimal | None = Field(
        default=None,
        description=(
            "Owner-set real exchange fee per side in bps (use taker rate). When set it "
            "replaces AEGIS_PAPER_FEE_BPS for paper fills and cost-aware risk checks."
        ),
    )

    # Paper soak runner — engineering only. Default off. Never arms live.
    paper_soak_enabled: bool = Field(
        default=False,
        description="When true, poll public klines and run paper cycles on new final bars.",
    )
    paper_soak_symbol: str = Field(
        default="ADAUSDT",
        description=(
            "Spot symbol(s) for paper soak REST polls. "
            "Comma-separated list allowed (max 10), e.g. ADAUSDT,BTCUSDT."
        ),
    )
    paper_soak_interval: str = Field(
        default="1m",
        description="Kline interval for paper soak (1m|5m|15m).",
    )
    paper_soak_poll_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Seconds between paper soak REST polls.",
    )

    # Risk policy version loader — financial numbers live in owner-signed snapshots
    # (see docs/RISK_POLICY.md). Default 1.0 after owner approval; use 0.1-draft to
    # force the all-UNAPPROVED draft. Never arms live trading by itself.
    risk_policy_version: str = Field(
        default="1.0",
        description="Policy snapshot id loaded by build_risk_policy_from_settings.",
    )

    # Who proposes trades: "llm" (default), "rules" (deterministic, no LLM spend) or
    # "rules+llm" (rules propose, LLM may only veto). Risk Engine decides in all cases.
    decision_source: str = Field(
        default="llm",
        description="llm | rules | rules+llm",
    )
    # JSON file with RuleParams (e.g. produced by scripts/walk_forward.py). Required
    # for rules modes; missing/invalid => every cycle is NO_TRADE (fail closed).
    rule_params_file: str | None = Field(
        default=None,
        description="Path to RuleParams JSON for decision_source rules / rules+llm.",
    )

    # Exchange transport bounds — engineering only, NOT risk-policy numbers.
    exchange_recv_window_ms: int = Field(
        default=5000,
        ge=1,
        le=60000,
        description="Toobit SIGNED recvWindow (ms); verified max 60000.",
    )
    exchange_timeout_seconds: float = Field(
        default=10.0,
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

    @field_validator("paper_soak_interval")
    @classmethod
    def _normalize_paper_soak_interval(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = {"1m", "5m", "15m"}
        if normalized not in allowed:
            msg = (
                f"AEGIS_PAPER_SOAK_INTERVAL must be one of {sorted(allowed)}, "
                f"got {value!r}"
            )
            raise ValueError(msg)
        return normalized

    @field_validator("paper_soak_symbol")
    @classmethod
    def _normalize_paper_soak_symbol(cls, value: str) -> str:
        from aegis.pipeline.soak import parse_soak_symbols

        return ",".join(parse_soak_symbols(value))

    @field_validator(
        "llm_token_budget",
        "llm_cost_budget",
        "llm_latency_budget_ms",
        "llm_daily_call_cap",
        "paper_initial_balance",
        "risk_position_pct",
        "risk_per_trade_pct",
        "risk_daily_loss_pct",
        "risk_weekly_loss_pct",
        "risk_monthly_loss_pct",
        "risk_max_drawdown_pct",
        "risk_min_reward_risk",
        "trading_fee_bps",
        "rule_params_file",
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
        """When a budget is set, token/latency must be > 0; cost may be 0 for free tiers."""
        if self.llm_token_budget is not None and self.llm_token_budget <= 0:
            msg = "AEGIS_LLM_TOKEN_BUDGET must be > 0 when set"
            raise ValueError(msg)
        if self.llm_cost_budget is not None and self.llm_cost_budget < 0:
            msg = "AEGIS_LLM_COST_BUDGET must be >= 0 when set (0 = free-tier ceiling)"
            raise ValueError(msg)
        if self.llm_latency_budget_ms is not None and self.llm_latency_budget_ms <= 0:
            msg = "AEGIS_LLM_LATENCY_BUDGET_MS must be > 0 when set"
            raise ValueError(msg)
        if self.llm_daily_call_cap is not None and self.llm_daily_call_cap <= 0:
            msg = "AEGIS_LLM_DAILY_CALL_CAP must be > 0 when set"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_risk_percentages(self) -> Settings:
        """Owner risk percentages: 0 < x <= 100 and nested daily <= weekly <= monthly."""
        pct_fields = {
            "AEGIS_RISK_POSITION_PCT": self.risk_position_pct,
            "AEGIS_RISK_PER_TRADE_PCT": self.risk_per_trade_pct,
            "AEGIS_RISK_DAILY_LOSS_PCT": self.risk_daily_loss_pct,
            "AEGIS_RISK_WEEKLY_LOSS_PCT": self.risk_weekly_loss_pct,
            "AEGIS_RISK_MONTHLY_LOSS_PCT": self.risk_monthly_loss_pct,
            "AEGIS_RISK_MAX_DRAWDOWN_PCT": self.risk_max_drawdown_pct,
        }
        for name, value in pct_fields.items():
            if value is not None and not (Decimal("0") < value <= Decimal("100")):
                msg = f"{name} must be > 0 and <= 100 when set"
                raise ValueError(msg)
        chain = [
            ("AEGIS_RISK_DAILY_LOSS_PCT", self.risk_daily_loss_pct),
            ("AEGIS_RISK_WEEKLY_LOSS_PCT", self.risk_weekly_loss_pct),
            ("AEGIS_RISK_MONTHLY_LOSS_PCT", self.risk_monthly_loss_pct),
        ]
        present = [(n, v) for n, v in chain if v is not None]
        for (lower_name, lower), (upper_name, upper) in zip(present, present[1:], strict=False):
            if lower > upper:
                msg = f"{lower_name} must be <= {upper_name}"
                raise ValueError(msg)
        if (
            self.risk_per_trade_pct is not None
            and self.risk_position_pct is not None
            and self.risk_per_trade_pct > self.risk_position_pct
        ):
            msg = "AEGIS_RISK_PER_TRADE_PCT must be <= AEGIS_RISK_POSITION_PCT"
            raise ValueError(msg)
        if self.risk_min_reward_risk is not None and self.risk_min_reward_risk <= 0:
            msg = "AEGIS_RISK_MIN_REWARD_RISK must be > 0 when set"
            raise ValueError(msg)
        if self.trading_fee_bps is not None and self.trading_fee_bps < 0:
            msg = "AEGIS_TRADING_FEE_BPS must be >= 0 when set"
            raise ValueError(msg)
        return self

    @field_validator("decision_source", mode="before")
    @classmethod
    def _normalize_decision_source(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "llm"
        text = str(value).strip().lower()
        if text not in {"llm", "rules", "rules+llm"}:
            msg = "AEGIS_DECISION_SOURCE must be one of: llm, rules, rules+llm"
            raise ValueError(msg)
        return text

    @property
    def effective_fee_bps(self) -> Decimal:
        """Real owner-set fee when configured, else the paper simulation default."""
        return self.trading_fee_bps if self.trading_fee_bps is not None else self.paper_fee_bps

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
