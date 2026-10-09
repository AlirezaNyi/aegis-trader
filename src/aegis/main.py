"""FastAPI application factory for Aegis."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aegis import __version__
from aegis.api.health import router as health_router
from aegis.api.metrics import router as metrics_router
from aegis.api.middleware import CorrelationIdMiddleware
from aegis.config.settings import Settings, get_settings
from aegis.interfaces.execution import NullExecutionPort
from aegis.jev.factory import build_jev_port
from aegis.logging import configure_logging, get_logger
from aegis.ops.alerts import AlertEvaluator
from aegis.ops.metrics import OpsMetrics
from aegis.ops.shutdown import ShutdownGate
from aegis.orders.manager import OrderManager
from aegis.paper.broker import PaperBroker
from aegis.paper.ledger import PaperLedger
from aegis.pipeline.cycle import PaperCycleDeps, run_paper_cycle
from aegis.reconcile.service import Reconciler
from aegis.risk.factory import build_risk_policy_from_settings
from aegis.supervisor.factory import budget_config_from_settings, build_llm_port


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    logger = get_logger("aegis.main")

    ops_metrics = OpsMetrics()
    ops_metrics.set_kill_switch(resolved.kill_switch)
    shutdown_gate = ShutdownGate()
    alert_evaluator = AlertEvaluator()

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
        try:
            yield
        finally:
            shutdown_gate.begin_shutdown()
            ops_metrics.set_shutting_down(True)
            logger.info(
                "aegis_shutting_down",
                extra={"event": "app_shutdown", "component": "main"},
            )

    app = FastAPI(
        title="Aegis",
        version=__version__,
        description=(
            "Personal autonomous trading system (paper-first). Live trading disabled by default."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.state.settings = resolved
    app.state.ops_metrics = ops_metrics
    app.state.shutdown_gate = shutdown_gate
    app.state.alert_evaluator = alert_evaluator
    # Phase 4–7 ports + paper cycle. No LLM-on-every-tick loop.
    # Live Toobit client not constructed (SRS-SV-004).
    app.state.jev_port = build_jev_port(resolved)
    app.state.llm_port = build_llm_port(resolved)
    app.state.supervisor_budgets = budget_config_from_settings(resolved)
    # Risk policy from AEGIS_RISK_POLICY_VERSION (default owner v1.0). Does not arm live.
    app.state.risk_policy = build_risk_policy_from_settings(resolved)
    # Paper ledger — simulation only.
    paper_ledger = PaperLedger()
    app.state.paper_ledger = paper_ledger
    paper_broker = PaperBroker(settings=resolved, ledger=paper_ledger)
    app.state.paper_broker = paper_broker
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
    # Paper decision cycle deps — callable on finalized candles only.
    paper_cycle_deps = PaperCycleDeps(
        settings=resolved,
        risk_policy=app.state.risk_policy,
        jev_port=app.state.jev_port,
        llm_port=app.state.llm_port,
        supervisor_budgets=app.state.supervisor_budgets,
        paper_broker=paper_broker,
        analyst_concurrency=resolved.analyst_concurrency,
    )
    app.state.paper_cycle_deps = paper_cycle_deps
    app.state.run_paper_cycle = run_paper_cycle
    app.include_router(health_router)
    app.include_router(metrics_router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("aegis.main:app", host="0.0.0.0", port=8000, reload=False)
