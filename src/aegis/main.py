"""FastAPI application factory for Aegis."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aegis import __version__
from aegis.api.health import router as health_router
from aegis.config.settings import Settings, get_settings
from aegis.interfaces.execution import NullExecutionPort
from aegis.jev.factory import build_jev_port
from aegis.logging import configure_logging, get_logger
from aegis.orders.manager import OrderManager
from aegis.paper.broker import PaperBroker
from aegis.paper.ledger import PaperLedger
from aegis.reconcile.service import Reconciler
from aegis.risk.factory import build_risk_policy_from_settings
from aegis.supervisor.factory import budget_config_from_settings, build_llm_port


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    logger = get_logger("aegis.main")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "aegis_started",
            extra={"event": "app_start", "component": "main"},
        )
        logger.info(
            "config_loaded mode=%s live_armed=%s kill_switch=%s",
            resolved.trading_mode.value,
            resolved.live_armed,
            resolved.kill_switch,
            extra={"event": "config_loaded", "component": "main"},
        )
        yield

    app = FastAPI(
        title="Aegis",
        version=__version__,
        description=(
            "Personal autonomous trading system (paper-first). Live trading disabled by default."
        ),
        lifespan=lifespan,
    )
    app.state.settings = resolved
    # Phase 4–7 ports — no tick loop; live Toobit client not constructed (SRS-SV-004).
    app.state.jev_port = build_jev_port(resolved)
    app.state.llm_port = build_llm_port(resolved)
    app.state.supervisor_budgets = budget_config_from_settings(resolved)
    # Draft risk policy: financial RP-* remain UNAPPROVED until owner signs a version.
    app.state.risk_policy = build_risk_policy_from_settings(resolved)
    # Paper ledger — simulation only.
    paper_ledger = PaperLedger()
    app.state.paper_ledger = paper_ledger
    app.state.paper_broker = PaperBroker(settings=resolved, ledger=paper_ledger)
    # Order Manager uses NullExecutionPort until owner arms live + wires build_execution_port.
    null_execution = NullExecutionPort()
    reconciler = Reconciler(null_execution)
    app.state.execution_port = null_execution
    app.state.reconciler = reconciler
    app.state.order_manager = OrderManager(
        settings=resolved,
        execution=null_execution,
        reconciler=reconciler,
    )
    app.include_router(health_router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("aegis.main:app", host="0.0.0.0", port=8000, reload=False)
