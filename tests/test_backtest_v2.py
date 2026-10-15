"""rules-v2 in backtests: htf slicing, grid expansion, shared-evidence equivalence."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.backtest.htf import (
    BACKTEST_ANALYST_TIMEOUT_SECONDS,
    HTF_CONTEXT_BARS,
    HtfIndex,
    backtest_settings,
)
from aegis.backtest.multi_replay import replay_rules_multi
from aegis.backtest.pipeline_replay import (
    PipelineReplayError,
    ReplayWindow,
    replay_windows,
)
from aegis.backtest.walk_forward import (
    EvalResult,
    EvalTask,
    WalkForwardError,
    evaluate_task,
    expand_grid,
    report_json,
    run_walk_forward,
)
from aegis.interfaces.jev import UnavailableJevPort
from aegis.interfaces.llm import UnavailableLlmPort
from aegis.pipeline.decide import load_rule_params
from aegis.risk.owner_v1 import owner_approved_policy_v1
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef
from aegis.strategy import RuleParams, RuleParamsV2
from test_backtest_walk_forward import (  # type: ignore[import-not-found]
    UTC0,
    _candles,
    _instrument,
    _settings,
    _wf_config,
)


def _daily(n: int, *, start_days_before: int = 60, symbol: str = "ADAUSDT",
           slope: str = "0.5") -> list[Candle]:
    """Daily bars ending around the 5m fixture; rising closes (regime up)."""
    inst = InstrumentRef(exchange="binance", market_type=MarketType.SPOT, symbol=symbol)
    out: list[Candle] = []
    first = UTC0 - timedelta(days=start_days_before)
    for i in range(n):
        open_time = first + timedelta(days=i)
        close = Decimal("70") + Decimal(slope) * i
        out.append(
            Candle(
                instrument=inst,
                interval=Timeframe.D1,
                open_time=open_time,
                close_time=open_time + timedelta(days=1) - timedelta(milliseconds=1),
                open=close - Decimal("0.2"),
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("1000"),
                is_final=True,
                source="fixture",
                received_at=open_time + timedelta(days=1),
            )
        )
    return out


def _v2(regime: int = 20, rsi: str = "45", k: str = "1.5", r: str = "1.5",
        hold: int = 6) -> RuleParamsV2:
    return RuleParamsV2.from_dict(
        {"regime_n": str(regime), "rsi_entry_max": rsi, "k_atr_stop": k, "r_target": r,
         "max_hold_bars": str(hold)}
    )


# ------------------------------------------------------------------------ HtfIndex


def test_htf_index_never_returns_future_bars_and_caps_length() -> None:
    daily = _daily(400, start_days_before=380)
    idx = HtfIndex(daily, instrument=_instrument())
    for as_of in (UTC0 - timedelta(days=300, hours=3), UTC0, UTC0 + timedelta(hours=5)):
        vis = idx.visible(as_of)
        assert vis and all(c.close_time <= as_of for c in vis)
        assert len(vis) <= HTF_CONTEXT_BARS
        later = [c for c in daily if c.close_time <= as_of]
        assert vis == later[-len(vis):]
    # Before the first daily close: nothing visible.
    assert idx.visible(daily[0].close_time - timedelta(milliseconds=1)) == []
    # Exactly at a daily close: that bar is complete and visible.
    assert idx.visible(daily[10].close_time)[-1] == daily[10]


def test_htf_index_filters_instrument_non_final_and_window_slice_covers_visible() -> None:
    daily = _daily(300, start_days_before=280)
    other = _daily(300, start_days_before=280, symbol="BTCUSDT")
    partial = daily[-1].model_copy(update={"is_final": False,
                                           "open_time": daily[-1].open_time + timedelta(days=1)})
    idx = HtfIndex([*daily, *other, partial], instrument=_instrument())
    assert len(idx) == len(daily)
    start, end = UTC0 - timedelta(days=5), UTC0 + timedelta(days=2)
    sliced = HtfIndex(idx.window_slice(start, end), instrument=_instrument())
    t = start
    while t <= end:
        assert sliced.visible(t) == idx.visible(t)
        t += timedelta(hours=7)
    assert all(c.close_time <= end for c in idx.window_slice(start, end))


def test_backtest_settings_only_raises_analyst_timeout() -> None:
    s = _settings()
    b = backtest_settings(s)
    assert b.analyst_timeout_seconds == BACKTEST_ANALYST_TIMEOUT_SECONDS
    assert s.analyst_timeout_seconds != BACKTEST_ANALYST_TIMEOUT_SECONDS  # original untouched
    assert b.trading_mode == s.trading_mode and b.kill_switch == s.kill_switch
    assert backtest_settings(b) is b


# ----------------------------------------------------------------------- grid


def test_expand_grid_rules_v2_dict_and_list() -> None:
    spec = {"strategy": "rules-v2", "regime_n": ["20", "50"], "rsi_entry_max": ["30", "40"],
            "k_atr_stop": "1.5", "r_target": ["1.5", "2.5"], "max_hold_bars": [12, 36]}
    params, skipped = expand_grid(spec)
    assert len(params) == 16 and not skipped
    assert all(isinstance(p, RuleParamsV2) for p in params)
    assert params[0].to_dict()["strategy"] == "rules-v2"
    lst, _ = expand_grid([{"strategy": "rules-v2", **_v2().to_dict()}])
    assert lst == [_v2()]
    # Invalid v2 point (regime_n not allowed) is skipped, not fatal.
    _, skipped = expand_grid({**spec, "regime_n": ["20", "33"]})
    assert skipped and all(p["regime_n"] == "33" for p in skipped)


def test_expand_grid_rejects_unknown_or_mixed_strategy() -> None:
    with pytest.raises(WalkForwardError):
        expand_grid({"strategy": "rules-v9", "x": ["1"]})
    with pytest.raises(WalkForwardError):
        expand_grid({"strategy": "rules-v2", "regime_n": ["20"]})  # missing fields
    v1 = {k: v for k, v in RuleParams.from_dict({
        "k_atr_stop": "1", "r_target": "2", "rsi_min": "0", "rsi_max": "100",
        "atr_pct_min": "0", "atr_pct_max": "100", "volume_ratio_min": "0"}).to_dict().items()}
    with pytest.raises(WalkForwardError):
        expand_grid([v1, {"strategy": "rules-v2", **_v2().to_dict()}])


def test_actual_preregistered_grids_have_32_points() -> None:
    root = Path(__file__).resolve().parents[1] / "data" / "walk_forward"
    for name in ("grid_v2_1h_2026-10-15.json", "grid_v2_15m_2026-10-15.json"):
        path = root / name
        if not path.exists():
            pytest.skip("grid files are local research data")
        params, skipped = expand_grid(json.loads(path.read_text(encoding="utf-8")))
        assert len(params) == 32 and not skipped


# -------------------------------------------- equivalence (v2 + htf + time stop)


async def test_multi_replay_v2_equals_run_paper_cycle_per_param() -> None:
    """Shared-evidence path == run_paper_cycle for rules-v2 with daily context."""
    candles = _candles(420)
    daily = _daily(70)
    settings = _settings()
    policy = owner_approved_policy_v1()
    window = ReplayWindow("w", candles[100].close_time, candles[-1].close_time)
    grid = [_v2(), _v2(rsi="40", k="2.5", r="2.5", hold=24), _v2(regime=50, hold=3),
            _v2(rsi="55", hold=2)]
    multi = await replay_rules_multi(
        candles,
        settings=settings,
        risk_policy=policy,
        instrument=_instrument(),
        timeframe=Timeframe.M5,
        window=window,
        param_sets=grid,
        jev_port=UnavailableJevPort(),
        spread_bps=Decimal("5"),
        initial_quote_balance=Decimal("100"),
        htf_candles=daily,
    )
    total_closed = 0
    for p, out in zip(grid, multi, strict=True):
        ref = await replay_windows(
            candles,
            settings=settings,
            risk_policy=policy,
            instrument=_instrument(),
            timeframe=Timeframe.M5,
            windows=[window],
            jev_port=UnavailableJevPort(),
            llm_port=UnavailableLlmPort(),
            supervisor_budgets=None,
            spread_bps=Decimal("5"),
            initial_quote_balance=Decimal("100"),
            decision_source="rules",
            rule_params=p,
            htf_candles=daily,
        )
        assert out.window.metrics.model_dump() == ref.windows["w"].metrics.model_dump()
        assert out.counters == ref.counters
        assert out.window.equity_curve == ref.windows["w"].equity_curve
        total_closed += out.counters["closed_trades"]
    assert total_closed > 0
    assert any(o.counters["closed_time_stop"] > 0 for o in multi)


async def test_v2_without_htf_never_trades_and_mixed_grid_refused() -> None:
    candles = _candles(200)
    window = ReplayWindow("w", candles[100].close_time, candles[-1].close_time)
    common = {
        "settings": _settings(), "risk_policy": owner_approved_policy_v1(),
        "instrument": _instrument(), "timeframe": Timeframe.M5, "window": window,
        "jev_port": UnavailableJevPort(), "spread_bps": Decimal("5"),
        "initial_quote_balance": Decimal("100"),
    }
    outs = await replay_rules_multi(candles, param_sets=[_v2(rsi="100")], **common)  # type: ignore[arg-type]
    assert outs[0].counters["filled"] == 0  # htf_context missing -> NO_TRADE
    v1 = RuleParams.from_dict({"k_atr_stop": "1", "r_target": "2", "rsi_min": "0",
                               "rsi_max": "100", "atr_pct_min": "0", "atr_pct_max": "100",
                               "volume_ratio_min": "0"})
    with pytest.raises(PipelineReplayError):
        await replay_rules_multi(candles, param_sets=[v1, _v2()], **common)  # type: ignore[arg-type]


async def test_v2_regime_down_blocks_entries() -> None:
    candles = _candles(300)
    window = ReplayWindow("w", candles[100].close_time, candles[-1].close_time)
    outs = await replay_rules_multi(
        candles, settings=_settings(), risk_policy=owner_approved_policy_v1(),
        instrument=_instrument(), timeframe=Timeframe.M5, window=window,
        param_sets=[_v2(rsi="100")], jev_port=UnavailableJevPort(),
        spread_bps=Decimal("5"), initial_quote_balance=Decimal("100"),
        htf_candles=_daily(70, slope="-0.5"),
    )
    assert outs[0].counters["filled"] == 0


# ------------------------------------------------------------- walk-forward v2


def test_walk_forward_v2_requires_htf() -> None:
    with pytest.raises(WalkForwardError):
        run_walk_forward({"ADAUSDT": _candles(288 * 6)}, [_v2()], _wf_config(min_trades=1),
                         settings=_settings())


def test_walk_forward_v2_end_to_end_htf_slices_and_report(tmp_path: Path) -> None:
    candles = {"ADAUSDT": _candles(288 * 6)}
    daily = {"ADAUSDT": _daily(80)}
    grid = [_v2(rsi="55", hold=4), _v2(rsi="45", hold=12)]
    seen: list[EvalTask] = []

    def spy(task: EvalTask) -> EvalResult:
        seen.append(task)
        assert task.htf_candles
        assert max(c.close_time for c in task.htf_candles) <= task.window.end
        assert max(c.close_time for c in task.candles) <= task.window.end
        return evaluate_task(task)

    result = run_walk_forward(candles, grid, _wf_config(min_trades=1), settings=_settings(),
                              evaluate=spy, htf_candles_by_symbol=daily)
    report = json.loads(report_json(result.report))
    assert report["strategy"]["version"] == "rules-v2"
    assert report["data"]["ADAUSDT"]["htf"]["timeframe"] == "1d"
    assert "exit_reasons" in report["oos_stitched"]["pooled"]
    assert "exit_reasons" in report["oos_stitched"]["per_symbol"]["ADAUSDT"]
    assert seen
    if result.best_params is not None:
        assert result.best_params["strategy"] == "rules-v2"
        path = tmp_path / "best.json"
        path.write_text(json.dumps(result.best_params, default=str), encoding="utf-8")
        loaded = load_rule_params(str(path))
        assert isinstance(loaded, RuleParamsV2)
        assert loaded.param_hash() == result.best_params["param_hash"]
        assert "exit_reasons" in report["holdout"]["pooled"]
