"""Health and readiness endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from aegis.config.settings import Settings
from aegis.db.session import check_database
from aegis.guards.live import live_execution_permitted, trading_ready
from aegis.market_data.metrics import MarketDataMetrics

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "aegis"}


@router.get("/ready")
def ready(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    db_ok = True
    db_detail = "skipped"
    if settings.require_database:
        db_ok, db_detail = check_database(settings.database_url)

    mode_ready = trading_ready(settings)
    ready_flag = db_ok and (settings.is_paper_or_dev or mode_ready)

    metrics: MarketDataMetrics | None = getattr(request.app.state, "market_data_metrics", None)
    market_data: dict[str, object]
    if metrics is None:
        market_data = {"configured": False}
    else:
        market_data = {"configured": True, **metrics.snapshot(datetime.now(UTC))}

    return {
        "ready": ready_flag,
        "service": "aegis",
        "trading_mode": settings.trading_mode.value,
        "live_armed": settings.live_armed,
        "kill_switch": settings.kill_switch,
        "trading_ready": mode_ready,
        "live_execution_permitted": live_execution_permitted(settings),
        "database": {"ok": db_ok, "detail": db_detail},
        "market_data": market_data,
        "jev_configured": settings.jev_configured,
        "phase": 2,
        "live_submit_client": "not_constructed",
    }
