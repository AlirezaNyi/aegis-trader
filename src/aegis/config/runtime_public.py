"""Non-secret runtime flags exposed to HTTP handlers (audit M5 / T-11)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aegis.config.settings import Settings, TradingMode
from aegis.guards.live import live_execution_permitted, trading_ready

DatabaseProbe = Callable[[], tuple[bool, str]]


@dataclass(frozen=True, slots=True)
class RuntimePublicState:
    """Flags safe to attach to ``app.state`` for readiness handlers.

    Must never carry API keys, signatures, database passwords, or full Settings.
    """

    trading_mode: TradingMode
    live_armed: bool
    kill_switch: bool
    require_database: bool
    jev_configured: bool
    is_paper_or_dev: bool
    trading_ready: bool
    live_execution_permitted: bool


def runtime_public_from_settings(settings: Settings) -> RuntimePublicState:
    return RuntimePublicState(
        trading_mode=settings.trading_mode,
        live_armed=settings.live_armed,
        kill_switch=settings.kill_switch,
        require_database=settings.require_database,
        jev_configured=settings.jev_configured,
        is_paper_or_dev=settings.is_paper_or_dev,
        trading_ready=trading_ready(settings),
        live_execution_permitted=live_execution_permitted(settings),
    )


def settings_without_secrets(settings: Settings) -> Settings:
    """Copy settings with credential fields cleared for objects kept on ``app.state``.

    Exchange/Jev/LLM ports must be built from the original Settings before this
    redaction. Risk, paper, and cycle deps only need gates and engineering knobs.
    """
    return settings.model_copy(
        update={
            "toobit_api_key": "",
            "toobit_api_secret": "",
            "typesafe_api_key": "",
            "llm_api_key": "",
            # Avoid retaining DB password on request-adjacent objects.
            "database_url": "postgresql+psycopg://redacted:redacted@localhost:5432/aegis",
        }
    )


def make_database_probe(settings: Settings) -> DatabaseProbe:
    """Close over DATABASE_URL without storing it on ``app.state`` as a field."""
    require = settings.require_database
    url = settings.database_url

    def _probe() -> tuple[bool, str]:
        if not require:
            return True, "skipped"
        from aegis.db.session import check_database

        return check_database(url)

    return _probe
