"""FastAPI application factory for Aegis."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from aegis import __version__
from aegis.api.dashboard import router as dashboard_router
from aegis.api.health import router as health_router
from aegis.api.metrics import router as metrics_router
from aegis.api.middleware import CorrelationIdMiddleware
from aegis.config.legacy_env import LegacyEnvError, find_legacy_keys
from aegis.config.runtime_public import (
    make_database_probe,
    runtime_public_from_settings,
    settings_without_secrets,
)
from aegis.config.settings import Settings, TradingMode, get_settings
from aegis.interfaces.execution import NullExecutionPort
from aegis.jev.factory import build_jev_port
from aegis.logging import configure_logging, get_logger
from aegis.market_data.candle_store import CandleStore
from aegis.market_data.gateway import MarketDataGateway
from aegis.market_data.rest_client import ToobitRestMarketDataClient
from aegis.ops.alerts import AlertEvaluator
from aegis.ops.metrics import OpsMetrics
from aegis.ops.shutdown import ShutdownGate
from aegis.orders.manager import OrderManager
from aegis.paper.broker import PaperBroker
from aegis.paper.equity import PaperEquityTracker
from aegis.paper.ledger import PaperLedger
from aegis.paper.state_store import PaperStateStore
from aegis.pipeline.activity import ActivityState
from aegis.pipeline.audit import DecisionAuditStore
from aegis.pipeline.cycle import PaperCycleDeps, run_paper_cycle
from aegis.pipeline.decide import AnyRuleParams, RuleParamsUnavailable, load_rule_params
from aegis.pipeline.journal import PaperCycleJournal
from aegis.pipeline.soak import (
    PaperSoakRunner,
    run_soak_loop,
    soak_instruments_from_settings,
    soak_timeframe_from_settings,
)
from aegis.reconcile.service import Reconciler
from aegis.risk.factory import build_risk_policy_from_settings, missing_policy_env
from aegis.supervisor.factory import (
    budget_config_from_settings,
    build_call_limiter,
    build_llm_port,
)


def _should_start_paper_soak(settings: Settings) -> bool:
    return (
        settings.paper_soak_enabled
        and settings.is_paper_or_dev
        and not settings.kill_switch
        and settings.trading_mode != TradingMode.LIVE
    )


def _load_rule_params_or_none(settings: Settings, logger: Any) -> AnyRuleParams | None:
    """Rule params for rules modes; failure => None => every cycle NO_TRADE."""
    if settings.decision_source == "llm":
        return None
    try:
        return load_rule_params(settings.rule_params_file)
    except RuleParamsUnavailable as exc:
        logger.warning(
            "rule_params_unavailable source=%s reason=%s (all cycles NO_TRADE)",
            settings.decision_source,
            str(exc),
            extra={"event": "rule_params_unavailable", "component": "main"},
        )
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    logger = get_logger("aegis.main")
    legacy_safety, legacy_other = find_legacy_keys(Settings.model_fields)
    if legacy_safety:
        # Refuse: e.g. KILL_SWITCH=true would be silently ignored (fail open).
        raise LegacyEnvError(
            "Unprefixed safety env keys are no longer read; rename to AEGIS_<NAME>: "
            + ", ".join(legacy_safety)
        )
    if legacy_other:
        logger.warning(
            "legacy_env_keys_ignored keys=%s (rename to AEGIS_<NAME>)",
            ",".join(legacy_other),
            extra={"event": "legacy_env_keys_ignored", "component": "main"},
        )

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
            "config_loaded mode=%s live_armed=%s kill_switch=%s paper_soak=%s",
            resolved.trading_mode.value,
            resolved.live_armed,
            resolved.kill_switch,
            resolved.paper_soak_enabled,
            extra={"event": "config_loaded", "component": "main"},
        )
        soak_task: asyncio.Task[None] | None = None
        rest_client: ToobitRestMarketDataClient | None = None
        try:
            if _should_start_paper_soak(resolved):
                rest_client = ToobitRestMarketDataClient()
                gateway = MarketDataGateway(rest_client)
                instruments = soak_instruments_from_settings(resolved)
                runner = PaperSoakRunner(
                    market_data=gateway,
                    deps=_app.state.paper_cycle_deps,
                    instruments=instruments,
                    timeframe=soak_timeframe_from_settings(resolved),
                    journal=_app.state.paper_journal,
                    activity=_app.state.activity,
                    candle_store=_app.state.candle_store,
                )
                _app.state.paper_soak_runner = runner
                soak_task = asyncio.create_task(
                    run_soak_loop(
                        runner,
                        poll_seconds=resolved.paper_soak_poll_seconds,
                        shutdown=shutdown_gate,
                    ),
                    name="aegis-paper-soak",
                )
                logger.info(
                    "paper_soak_started symbols=%s interval=%s poll=%s",
                    ",".join(i.symbol for i in instruments),
                    resolved.paper_soak_interval,
                    resolved.paper_soak_poll_seconds,
                    extra={"event": "paper_soak_started", "component": "main"},
                )
            else:
                _app.state.paper_soak_runner = None
            yield
        finally:
            shutdown_gate.begin_shutdown()
            ops_metrics.set_shutting_down(True)
            if soak_task is not None:
                soak_task.cancel()
                try:
                    await soak_task
                except asyncio.CancelledError:
                    pass
            if rest_client is not None:
                await rest_client.aclose()
            # Release async HTTP clients held by credentialed ports (no-op stubs skip).
            for port in (_app.state.jev_port, _app.state.llm_port):
                closer = getattr(port, "aclose", None)
                if closer is not None:
                    await closer()
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
    # Audit M5: never attach full Settings (secrets) to app.state for HTTP handlers.
    app.state.runtime = runtime_public_from_settings(resolved)
    app.state.database_probe = make_database_probe(resolved)
    app.state.ops_metrics = ops_metrics
    app.state.shutdown_gate = shutdown_gate
    app.state.alert_evaluator = alert_evaluator
    # Phase 4–7 ports + paper cycle / optional soak. No LLM-on-every-tick loop.
    # Live Toobit client not constructed (SRS-SV-004).
    # Build credentialed ports from full Settings, then redact for request-adjacent deps.
    app.state.jev_port = build_jev_port(resolved)
    app.state.llm_port = build_llm_port(resolved)
    app.state.supervisor_budgets = budget_config_from_settings(resolved)
    # Risk policy from AEGIS_RISK_POLICY_VERSION (default owner v1.0). Does not arm live.
    app.state.risk_policy = build_risk_policy_from_settings(resolved)
    # Env var NAMES only (never values) still missing for policy 1.1.
    app.state.policy_missing_env = missing_policy_env(resolved)
    if app.state.policy_missing_env:
        logger.warning(
            "risk_policy_incomplete version=%s missing=%s (new trades will be rejected)",
            app.state.risk_policy.policy_version,
            ",".join(app.state.policy_missing_env),
            extra={"event": "risk_policy_incomplete", "component": "main"},
        )
    safe_settings = settings_without_secrets(resolved)
    # Paper ledger — simulation only.
    paper_ledger = PaperLedger()
    app.state.paper_ledger = paper_ledger
    paper_broker = PaperBroker(
        settings=safe_settings,
        ledger=paper_ledger,
        initial_quote_balance=resolved.paper_initial_balance,
        # Real owner-set fee when configured, so realized R matches Risk's cost model.
        fee_bps=resolved.effective_fee_bps,
    )
    app.state.paper_broker = paper_broker
    equity_tracker = PaperEquityTracker()
    # Paper ledger + period loss state survive restarts when a DB is required
    # (Compose). Restore happens lazily before the first cycle; failure => Risk rejects.
    paper_state_store = (
        PaperStateStore(resolved.database_url, quote_asset=paper_ledger.quote_asset)
        if resolved.require_database
        else None
    )
    app.state.paper_state_store = paper_state_store
    app.state.equity_tracker = equity_tracker
    # Order Manager uses NullExecutionPort until owner arms live + wires build_execution_port.
    null_execution = NullExecutionPort()
    reconciler = Reconciler(null_execution)
    app.state.execution_port = null_execution
    app.state.reconciler = reconciler
    app.state.order_manager = OrderManager(
        settings=safe_settings,
        execution=null_execution,
        reconciler=reconciler,
    )
    # Operator dashboard state — activity is in-memory; journal uses real DB URL
    # (never store full Settings on app.state; URL is journal-only).
    activity = ActivityState()
    app.state.activity = activity
    app.state.paper_journal = PaperCycleJournal(database_url=resolved.database_url)
    decision_audit = DecisionAuditStore(database_url=resolved.database_url)
    app.state.candle_store = CandleStore(database_url=resolved.database_url)
    app.state.decision_audit = decision_audit
    # Paper decision cycle deps — callable on finalized candles only.
    paper_cycle_deps = PaperCycleDeps(
        settings=safe_settings,
        risk_policy=app.state.risk_policy,
        jev_port=app.state.jev_port,
        llm_port=app.state.llm_port,
        supervisor_budgets=app.state.supervisor_budgets,
        paper_broker=paper_broker,
        analyst_concurrency=resolved.analyst_concurrency,
        activity=activity,
        audit=decision_audit,
        llm_call_limiter=build_call_limiter(resolved),
        equity_tracker=equity_tracker,
        state_store=paper_state_store,
        decision_source=resolved.decision_source,
        rule_params=_load_rule_params_or_none(resolved, logger),
    )
    app.state.paper_cycle_deps = paper_cycle_deps
    app.state.run_paper_cycle = run_paper_cycle
    app.state.paper_soak_runner = None
    app.include_router(dashboard_router)
    app.include_router(health_router)
    app.include_router(metrics_router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("aegis.main:app", host="0.0.0.0", port=8000, reload=False)
