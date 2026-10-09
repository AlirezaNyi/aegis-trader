"""Backtest run report contracts (descriptive metrics; no profitability claims)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from aegis.schemas.common import ApiModel


class SplitWindows(ApiModel):
    """Explicit time-based train / validation / out-of-sample windows."""

    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    oos_start: datetime
    oos_end: datetime


class WindowMetrics(ApiModel):
    """Descriptive metrics for one split window."""

    window: str
    net_return_after_costs: Decimal
    max_drawdown: Decimal
    profit_factor: Decimal | None
    win_rate: Decimal | None
    avg_win: Decimal | None
    avg_loss: Decimal | None
    trade_count: int
    exposure: Decimal
    turnover: Decimal
    cost_contribution: Decimal
    by_instrument: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_timeframe: dict[str, dict[str, Any]] = Field(default_factory=dict)
    sample_size_notes: str = ""


class BacktestRunReport(ApiModel):
    """Evaluation artifact for a deterministic backtest run."""

    run_id: UUID
    strategy_id: str
    strategy_version: str
    dataset_version: str
    seed: int
    software_version: str
    splits: SplitWindows
    assumptions: dict[str, Any] = Field(default_factory=dict)
    train_metrics: WindowMetrics
    validation_metrics: WindowMetrics
    oos_metrics: WindowMetrics
    uncertainty_notes: str
    disclaimer: str = (
        "Descriptive simulation only. Results do not guarantee future performance "
        "and must not be treated as proof of profitability or edge."
    )
    created_at: datetime
