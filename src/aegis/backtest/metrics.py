"""Descriptive backtest metrics (no profitability claims)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from aegis.schemas.backtest import WindowMetrics

_ZERO = Decimal("0")
_ONE = Decimal("1")


def compute_window_metrics(
    *,
    window: str,
    equity_curve: list[Decimal],
    initial_equity: Decimal,
    trade_pnls: list[Decimal],
    costs: list[Decimal],
    notionals: list[Decimal],
    exposure_bars: int,
    total_bars: int,
    by_instrument: dict[str, dict[str, Any]] | None = None,
    by_timeframe: dict[str, dict[str, Any]] | None = None,
) -> WindowMetrics:
    final = equity_curve[-1] if equity_curve else initial_equity
    if initial_equity == 0:
        net_return = _ZERO
    else:
        net_return = (final - initial_equity) / initial_equity

    max_dd = _max_drawdown(equity_curve if equity_curve else [initial_equity])
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    trade_count = len(trade_pnls)

    gross_profit = sum(wins, _ZERO)
    gross_loss = abs(sum(losses, _ZERO))
    profit_factor: Decimal | None
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = None  # undefined / infinite — leave null with note
    else:
        profit_factor = None

    win_rate: Decimal | None = (
        Decimal(len(wins)) / Decimal(trade_count) if trade_count else None
    )
    avg_win = (sum(wins, _ZERO) / Decimal(len(wins))) if wins else None
    avg_loss = (sum(losses, _ZERO) / Decimal(len(losses))) if losses else None

    turnover = sum(notionals, _ZERO)
    cost_contribution = sum(costs, _ZERO)
    exposure = (
        Decimal(exposure_bars) / Decimal(total_bars) if total_bars > 0 else _ZERO
    )

    sample_notes = (
        f"trade_count={trade_count}; small samples are statistically weak; "
        f"profit_factor={'null (no losses or no trades)' if profit_factor is None else 'defined'}."
    )

    return WindowMetrics(
        window=window,
        net_return_after_costs=net_return,
        max_drawdown=max_dd,
        profit_factor=profit_factor,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        trade_count=trade_count,
        exposure=exposure,
        turnover=turnover,
        cost_contribution=cost_contribution,
        by_instrument=by_instrument or {},
        by_timeframe=by_timeframe or {},
        sample_size_notes=sample_notes,
    )


def _max_drawdown(equity_curve: list[Decimal]) -> Decimal:
    if not equity_curve:
        return _ZERO
    peak = equity_curve[0]
    max_dd = _ZERO
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd
