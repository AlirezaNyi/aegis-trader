"""Metrics, alert dry-run, and paper review HTTP endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from aegis.ops.alerts import AlertEvaluator
from aegis.ops.metrics import OpsMetrics
from aegis.pipeline.review import summarize_soak_outcome
from aegis.pipeline.soak import PaperSoakRunner

router = APIRouter(tags=["ops"])


@router.get("/metrics")
def metrics_scrape(request: Request) -> Response:
    ops: OpsMetrics = request.app.state.ops_metrics
    return Response(content=ops.prometheus_text(), media_type="text/plain; version=0.0.4")


@router.get("/ops/alerts/dry-run")
def alerts_dry_run(request: Request) -> dict[str, Any]:
    ops: OpsMetrics = request.app.state.ops_metrics
    evaluator: AlertEvaluator = request.app.state.alert_evaluator
    return evaluator.dry_run(ops)


@router.get("/ops/paper/last-cycle")
def paper_last_cycle(request: Request) -> dict[str, Any]:
    """Owner review of the latest paper soak suggestion (simulated only)."""
    runner: PaperSoakRunner | None = getattr(request.app.state, "paper_soak_runner", None)
    if runner is None:
        return {
            "available": False,
            "reason": "paper_soak_not_running",
            "hint": (
                "Set AEGIS_PAPER_SOAK_ENABLED=true (paper mode) and restart, "
                "or run scripts/paper_soak.py --once"
            ),
        }
    if runner.last_outcome is None:
        return {
            "available": False,
            "reason": "no_cycle_yet",
            "cycles_run": runner.cycles_run,
            "symbols": runner.symbols,
            "timeframe": runner.timeframe.value,
        }
    return {
        "available": True,
        "cycles_run": runner.cycles_run,
        "symbols": runner.symbols,
        "timeframe": runner.timeframe.value,
        "outcome": summarize_soak_outcome(runner.last_outcome),
    }
