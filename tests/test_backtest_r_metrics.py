"""R-multiple, expectancy and per-bar Sharpe / Sortino backtest metrics."""

from __future__ import annotations

from decimal import Decimal

from aegis.backtest.metrics import (
    RTrade,
    compute_window_metrics,
    per_bar_returns,
    r_metrics,
    sharpe_sortino,
)

D = Decimal


def test_r_metrics_mean_and_risk_weighted_expectancy() -> None:
    trades = [RTrade(pnl=D("20"), initial_risk=D("10")), RTrade(pnl=D("-5"), initial_risk=D("5"))]
    avg_r, expectancy_r = r_metrics(trades)
    assert avg_r == D("0.5")  # (2 + -1) / 2
    assert expectancy_r == D("1")  # (20 - 5) / (10 + 5)


def test_r_metrics_none_without_defined_risk() -> None:
    assert r_metrics([]) == (None, None)
    assert r_metrics([RTrade(pnl=D("1"), initial_risk=D("0"))]) == (None, None)


def test_per_bar_returns_start_from_initial_equity() -> None:
    assert per_bar_returns([D("110"), D("99")], D("100")) == [D("0.1"), D("-0.1")]
    assert per_bar_returns([D("1")], D("0")) == []


def test_sharpe_and_sortino_per_bar() -> None:
    returns = [D("0.02"), D("-0.01"), D("0.03"), D("-0.02")]
    sharpe, sortino = sharpe_sortino(returns)
    mean = D("0.005")
    variance = sum(((r - mean) ** 2 for r in returns), D(0)) / D(3)
    downside = (D("0.01") ** 2 + D("0.02") ** 2) / D(4)
    assert sharpe == mean / variance.sqrt()
    assert sortino == mean / downside.sqrt()


def test_sharpe_sortino_undefined_cases() -> None:
    assert sharpe_sortino([D("0.01")]) == (None, None)
    assert sharpe_sortino([D("0.01"), D("0.01")]) == (None, None)  # zero deviation
    sharpe, sortino = sharpe_sortino([D("0.01"), D("0.02")])
    assert sharpe is not None and sortino is None  # no downside


def test_window_metrics_carry_r_and_ratio_fields() -> None:
    metrics = compute_window_metrics(
        window="oos",
        equity_curve=[D("101"), D("99"), D("102")],
        initial_equity=D("100"),
        trade_pnls=[D("2"), D("-1")],
        costs=[D("0.1")],
        notionals=[D("10")],
        exposure_bars=1,
        total_bars=3,
        r_trades=[
            RTrade(pnl=D("2"), initial_risk=D("1")),
            RTrade(pnl=D("-1"), initial_risk=D("1")),
        ],
    )
    assert metrics.trade_count == 2
    assert metrics.win_rate == D("0.5")
    assert metrics.r_trade_count == 2
    assert metrics.avg_r == D("0.5")
    assert metrics.expectancy_r == D("0.5")
    assert metrics.sharpe_per_bar is not None
    assert metrics.sortino_per_bar is not None
    assert "not annualized" in metrics.sample_size_notes


def test_window_metrics_defaults_without_r_trades() -> None:
    metrics = compute_window_metrics(
        window="train",
        equity_curve=[],
        initial_equity=D("100"),
        trade_pnls=[],
        costs=[],
        notionals=[],
        exposure_bars=0,
        total_bars=0,
    )
    assert metrics.r_trade_count == 0
    assert metrics.avg_r is None and metrics.expectancy_r is None
    assert metrics.sharpe_per_bar is None and metrics.sortino_per_bar is None
