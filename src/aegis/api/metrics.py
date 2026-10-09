"""Metrics and alert dry-run HTTP endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from aegis.ops.alerts import AlertEvaluator
from aegis.ops.metrics import OpsMetrics

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
