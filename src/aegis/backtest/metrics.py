"""Descriptive backtest metrics (no profitability claims)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from aegis.schemas.backtest import WindowMetrics

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class RTrade:
    """Closed trade with a defined initial risk (``(entry - stop) * qty > 0``)."""

    pnl: Decimal
    initial_risk: Decimal

    @property
    def r_multiple(self) -> Decimal:
        return self.pnl / self.initial_risk


def r_metrics(r_trades: Sequence[RTrade]) -> tuple[Decimal | None, Decimal | None]:
    """Return ``(avg_r, expectancy_r)``; None when there are no R-defined trades.

    ``avg_r`` is the arithmetic mean R-multiple. ``expectancy_r`` is the
    risk-weighted expectancy ``sum(pnl) / sum(initial_risk)`` (net PnL earned per
    unit of risk taken); the two differ when per-trade risk varies.
    """
    valid = [t for t in r_trades if t.initial_risk > 0]
    if not valid:
        return None, None
    avg_r = sum((t.r_multiple for t in valid), _ZERO) / Decimal(len(valid))
    total_risk = sum((t.initial_risk for t in valid), _ZERO)
    expectancy_r = sum((t.pnl for t in valid), _ZERO) / total_risk
    return avg_r, expectancy_r


def per_bar_returns(equity_curve: Sequence[Decimal], initial_equity: Decimal) -> list[Decimal]:
    """Simple returns between consecutive marks, starting from ``initial_equity``."""
    returns: list[Decimal] = []
    previous = initial_equity
    for value in equity_curve:
        if previous <= 0:
            return []
        returns.append(value / previous - _ONE)
        previous = value
    return returns


def sharpe_sortino(returns: Sequence[Decimal]) -> tuple[Decimal | None, Decimal | None]:
    """Per-bar Sharpe (sample stdev) and Sortino (downside deviation, target 0).

    Risk-free rate 0, not annualized. None when fewer than two returns or when the
    relevant deviation is zero.
    """
    n = len(returns)
    if n < 2:
        return None, None
    mean = sum(returns, _ZERO) / Decimal(n)
    variance = sum(((r - mean) ** 2 for r in returns), _ZERO) / Decimal(n - 1)
    sharpe = mean / variance.sqrt() if variance > 0 else None
    downside = sum(((r if r < 0 else _ZERO) ** 2 for r in returns), _ZERO) / Decimal(n)
    sortino = mean / downside.sqrt() if downside > 0 else None
    return sharpe, sortino


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
    r_trades: Sequence[RTrade] | None = None,
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

    valid_r = [t for t in (r_trades or []) if t.initial_risk > 0]
    avg_r, expectancy_r = r_metrics(valid_r)
    sharpe, sortino = sharpe_sortino(per_bar_returns(equity_curve, initial_equity))

    sample_notes = (
        f"trade_count={trade_count}; r_trade_count={len(valid_r)}; small samples are "
        "statistically weak; "
        f"profit_factor={'null (no losses or no trades)' if profit_factor is None else 'defined'}; "
        "sharpe/sortino are per-bar, risk-free 0, not annualized."
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
        r_trade_count=len(valid_r),
        avg_r=avg_r,
        expectancy_r=expectancy_r,
        sharpe_per_bar=sharpe,
        sortino_per_bar=sortino,
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
