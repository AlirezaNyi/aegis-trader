"""Historical backtest harness (Phase 6). Paper fills only; no live submit."""

from aegis.backtest.costs import CostModel
from aegis.backtest.metrics import compute_window_metrics
from aegis.backtest.replay import CandleReplay, LookAheadError
from aegis.backtest.runner import (
    SOFTWARE_VERSION,
    BacktestRunner,
    RiskGate,
    StrategyHook,
    always_flat,
    buy_hold_one_bar_factory,
)
from aegis.schemas.backtest import BacktestRunReport, SplitWindows, WindowMetrics

__all__ = [
    "SOFTWARE_VERSION",
    "BacktestRunReport",
    "BacktestRunner",
    "CandleReplay",
    "CostModel",
    "LookAheadError",
    "RiskGate",
    "SplitWindows",
    "StrategyHook",
    "WindowMetrics",
    "always_flat",
    "buy_hold_one_bar_factory",
    "compute_window_metrics",
]
