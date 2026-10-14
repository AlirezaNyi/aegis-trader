"""Walk-forward harness: folds, grid, DSR math, selection, holdout-once, equivalence."""

from __future__ import annotations

import json
import math
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.backtest.metrics import compute_window_metrics
from aegis.backtest.multi_replay import replay_rules_multi
from aegis.backtest.pipeline_replay import (
    PipelineReplayError,
    ReplayWindow,
    replay_windows,
)
from aegis.backtest.walk_forward import (
    EvalResult,
    EvalTask,
    PooledScore,
    TrialResult,
    WalkForwardConfig,
    WalkForwardError,
    add_months,
    build_folds,
    deflated_sharpe,
    evaluate_task,
    expand_grid,
    expected_max_sharpe,
    moments,
    probabilistic_sharpe,
    report_json,
    run_walk_forward,
    select_params,
)
from aegis.backtest.walk_forward_persist import persist_walk_forward
from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.interfaces.jev import UnavailableJevPort
from aegis.interfaces.llm import UnavailableLlmPort
from aegis.pipeline.decide import load_rule_params
from aegis.risk.owner_v1 import owner_approved_policy_v1
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef
from aegis.strategy.rules import RuleParams

UTC0 = datetime(2026, 1, 1, tzinfo=UTC)


def _settings() -> Settings:
    clear_settings_cache()
    return Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
        paper_fee_bps=Decimal("10"),
        paper_slippage_bps=Decimal("0"),
        trading_fee_bps=None,
        risk_policy_version="1.0",
    )


def _instrument() -> InstrumentRef:
    return InstrumentRef(exchange="binance", market_type=MarketType.SPOT, symbol="ADAUSDT")


def _candles(n: int, *, seed: int = 7, symbol: str = "ADAUSDT") -> list[Candle]:
    """Deterministic 5m random walk with a slow sinusoidal drift (trends both ways)."""
    rng = random.Random(seed)
    inst = InstrumentRef(exchange="binance", market_type=MarketType.SPOT, symbol=symbol)
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        drift = 0.0015 * math.sin(i / 40)
        o = price
        c = o * (1 + drift + rng.gauss(0, 0.002))
        hi = max(o, c) * (1 + abs(rng.gauss(0, 0.0015)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.0015)))
        open_time = UTC0 + timedelta(minutes=5 * i)
        out.append(
            Candle(
                instrument=inst,
                interval=Timeframe.M5,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=5) - timedelta(milliseconds=1),
                open=Decimal(f"{o:.4f}"),
                high=Decimal(f"{hi:.4f}"),
                low=Decimal(f"{lo:.4f}"),
                close=Decimal(f"{c:.4f}"),
                volume=Decimal(rng.randint(50, 200)),
                is_final=True,
                source="fixture",
                received_at=open_time + timedelta(days=1),
            )
        )
        price = c
    return out


def _params(k: str = "1", r: str = "2", vol: str = "0") -> RuleParams:
    return RuleParams.from_dict(
        {
            "k_atr_stop": k,
            "r_target": r,
            "rsi_min": "0",
            "rsi_max": "100",
            "atr_pct_min": "0",
            "atr_pct_max": "100",
            "volume_ratio_min": vol,
        }
    )


# ---------------------------------------------------------------------- fold plan


def test_add_months_clamps_day() -> None:
    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2025, 11, 15, tzinfo=UTC), 3) == datetime(2026, 2, 15, tzinfo=UTC)
    assert add_months(datetime(2026, 3, 1, tzinfo=UTC), -2) == datetime(2026, 1, 1, tzinfo=UTC)


def test_build_folds_boundaries_no_overlap_no_holdout_touch() -> None:
    start = datetime(2025, 9, 1, tzinfo=UTC)
    end_excl = datetime(2026, 10, 14, tzinfo=UTC)
    plan = build_folds(start, end_excl, train=3, test=1, step=1, holdout=2)
    assert plan.holdout.start == datetime(2026, 8, 14, tzinfo=UTC)
    assert plan.holdout.end < end_excl
    assert len(plan.folds) == 8
    eps = timedelta(microseconds=1)
    for i, fold in enumerate(plan.folds):
        assert fold.train.start == add_months(start, i)
        assert fold.train.end + eps == fold.test.start  # adjacent, no overlap
        assert fold.train.start < fold.train.end < fold.test.start <= fold.test.end
        assert fold.test.end < plan.holdout.start  # holdout never in a fold
        assert fold.test.end + eps == add_months(fold.test.start, 1)
    # Test windows of consecutive folds do not overlap (step == test).
    for a, b in zip(plan.folds, plan.folds[1:], strict=False):
        assert a.test.end < b.test.start


def test_build_folds_days_and_errors() -> None:
    plan = build_folds(UTC0, UTC0 + timedelta(days=6), train=2, test=1, step=1, holdout=1,
                       unit="days")
    assert len(plan.folds) == 3
    assert plan.holdout.start == UTC0 + timedelta(days=5)
    with pytest.raises(WalkForwardError):
        build_folds(UTC0, UTC0 + timedelta(days=3), train=2, test=1, step=1, holdout=1,
                    unit="days")
    with pytest.raises(WalkForwardError):
        build_folds(UTC0, UTC0 + timedelta(days=6), train=0, test=1, step=1, holdout=1,
                    unit="days")


# --------------------------------------------------------------------------- grid


def test_expand_grid_cartesian_order_and_skips() -> None:
    spec = {
        "k_atr_stop": ["1", 2, 1.5],
        "r_target": ["2", "3"],
        "rsi_min": ["40", "80"],
        "rsi_max": "70",
        "atr_pct_min": "0",
        "atr_pct_max": "5",
        "volume_ratio_min": "1",
    }
    params, skipped = expand_grid(spec)
    # rsi_min=80 >= rsi_max=70 is invalid -> half of 12 skipped.
    assert len(params) == 6 and len(skipped) == 6
    assert [p.to_dict()["k_atr_stop"] for p in params] == ["1", "1", "2", "2", "1.5", "1.5"]
    assert params[0].k_atr_stop == Decimal("1")
    assert params[4].k_atr_stop == Decimal("1.5")


def test_expand_grid_list_form_dedupes_and_validates() -> None:
    a = _params().to_dict()
    b = {**a, "k_atr_stop": "1.0"}  # same canonical value -> duplicate
    params, skipped = expand_grid([a, b, {**a, "r_target": "0"}])
    assert len(params) == 1 and len(skipped) == 1
    with pytest.raises(WalkForwardError):
        expand_grid({"k_atr_stop": ["1"]})
    with pytest.raises(WalkForwardError):
        expand_grid([{**a, "k_atr_stop": True}])
    with pytest.raises(WalkForwardError):
        expand_grid("nope")


# ----------------------------------------------------------------------------- DSR


def test_dsr_matches_bailey_lopez_de_prado_example() -> None:
    """Numerical example of Bailey & Lopez de Prado (2014), 'The Deflated Sharpe Ratio'.

    N = 100 trials, V[SR_n] = 1/2 annualised, best SR = 2.5 annualised, T = 1250 daily
    observations, skew = -3, kurtosis = 10, 250 days/year. Paper: SR0 ~ 0.1132
    (non-annualised) and DSR ~ 0.9004.
    """
    sr = 2.5 / math.sqrt(250)
    var = 0.5 / 250
    sr0 = expected_max_sharpe(var, 100)
    assert sr0 == pytest.approx(0.1132, abs=5e-4)
    dsr, sr0_b = deflated_sharpe(sr=sr, n_obs=1250, skew=-3, kurtosis=10, sr_variance=var,
                                 n_trials=100)
    assert sr0_b == sr0
    assert dsr is not None
    assert dsr == pytest.approx(0.9004, abs=2e-3)


def test_dsr_edge_cases() -> None:
    assert expected_max_sharpe(0.3, 1) == 0.0
    assert expected_max_sharpe(0.0, 50) == 0.0
    with pytest.raises(ValueError):
        expected_max_sharpe(-1.0, 5)
    # More trials => higher hurdle => lower DSR.
    lo, _ = deflated_sharpe(sr=0.2, n_obs=200, skew=0, kurtosis=3, sr_variance=0.01, n_trials=10)
    hi, _ = deflated_sharpe(sr=0.2, n_obs=200, skew=0, kurtosis=3, sr_variance=0.01,
                            n_trials=1000)
    assert lo is not None and hi is not None and hi < lo
    assert probabilistic_sharpe(0.0, 0.0, 100, 0, 3) == pytest.approx(0.5)
    assert probabilistic_sharpe(0.1, 0.0, 1, 0, 3) is None


def test_moments_population_skew_kurtosis() -> None:
    m = moments([1.0, 2.0, 3.0, 4.0])
    assert m is not None
    assert m.mean == pytest.approx(2.5)
    assert m.std == pytest.approx(math.sqrt(5 / 3))
    assert m.skew == pytest.approx(0.0)
    assert m.kurtosis == pytest.approx(1.64)
    assert moments([1.0]) is None
    assert moments([2.0, 2.0]) is None


# ----------------------------------------------------------------------- selection


def _score(h: str, n: int, exp: str | None, dd: str) -> PooledScore:
    return PooledScore(
        param_hash=h,
        params={},
        r_trade_count=n,
        expectancy_r=None if exp is None else Decimal(exp),
        worst_max_drawdown=Decimal(dd),
    )


def test_select_params_min_trades_and_tie_breaks() -> None:
    scores = [
        _score("a", 10, "0.9", "0.01"),  # best expectancy but too few trades
        _score("b", 40, "0.2", "0.05"),
        _score("c", 40, "0.2", "0.03"),  # same expectancy, lower drawdown
        _score("d", 40, None, "0.0"),
    ]
    chosen = select_params(scores, min_trades=30)
    assert chosen is not None and chosen.param_hash == "c"
    assert select_params(scores, min_trades=100) is None
    tie = select_params([_score("z", 40, "0.2", "0.03"), _score("y", 40, "0.2", "0.03")],
                        min_trades=1)
    assert tie is not None and tie.param_hash == "y"


# --------------------------------------------- driver with a fake evaluator (fast)


class FakeEvaluator:
    """Deterministic fake: train favours point 0, test windows favour point 1.

    Records every task so the test can audit what was evaluated and when.
    """

    def __init__(self, *, train_trades: int = 50) -> None:
        self.tasks: list[EvalTask] = []
        self.train_trades = train_trades

    def __call__(self, task: EvalTask) -> EvalResult:
        self.tasks.append(task)
        trials: list[TrialResult] = []
        for i, p in enumerate(task.param_sets):
            good = (i == 0) if task.tag == "train" else (i == 1)
            r = Decimal("0.5") if good else Decimal("-0.2")
            n = self.train_trades if task.tag == "train" else 10
            metrics = compute_window_metrics(
                window=task.window.name,
                equity_curve=[Decimal("100")] * 3,
                initial_equity=Decimal("100"),
                trade_pnls=[r] * n,
                costs=[],
                notionals=[],
                exposure_bars=0,
                total_bars=3,
            )
            trials.append(
                TrialResult(
                    param_hash=p.param_hash(),
                    params=p.to_dict(),
                    metrics=metrics,
                    r_multiples=[r] * n,
                    trade_pnls=[r] * n,
                    initial_risks=[Decimal("1")] * n,
                    bar_returns=[Decimal("0")] * 3,
                    counters={"bars": 3},
                    candidate_rejects={},
                )
            )
        return EvalResult(task_symbol=task.symbol, tag=task.tag, fold=task.fold,
                          window=task.window, trials=trials, bars_in_window=3)


def _wf_config(**kw: object) -> WalkForwardConfig:
    base: dict[str, object] = {
        "train": 2,
        "test": 1,
        "step": 1,
        "holdout": 1,
        "min_trades": 30,
        "spread_bps": Decimal("5"),
        "initial_balance": Decimal("100"),
        "unit": "days",
        "data_source": "binance-archive",
    }
    base.update(kw)
    return WalkForwardConfig(**base)  # type: ignore[arg-type]


def test_holdout_untouched_during_selection_and_used_once() -> None:
    candles = {"ADAUSDT": _candles(288 * 6), "BTCUSDT": _candles(288 * 6, symbol="BTCUSDT")}
    grid = [_params("1"), _params("2"), _params("3")]
    fake = FakeEvaluator()
    result = run_walk_forward(candles, grid, _wf_config(), settings=_settings(), evaluate=fake)
    holdout_start = UTC0 + timedelta(days=5)
    tags = [t.tag for t in fake.tasks]
    first_holdout = tags.index("holdout")
    assert all(t != "holdout" for t in tags[:first_holdout])
    assert tags[first_holdout:] == ["holdout", "holdout"]  # once per symbol, at the end
    for t in fake.tasks[:first_holdout]:
        assert t.window.end < holdout_start
        # No candle at or after the window end is handed to a selection task.
        assert max(c.close_time for c in t.candles) <= t.window.end
        assert len(t.param_sets) == len(grid)
    for t in fake.tasks[first_holdout:]:
        assert t.window.start == holdout_start
        assert len(t.param_sets) == 1
    # Selection follows TRAIN (point 0) even though test windows favour point 1.
    for fold in result.report["folds"]:
        assert fold["selected"]["param_hash"] == grid[0].param_hash()
        assert fold["selected"]["test_rank_pct_among_grid"] < 1.0
    assert result.report["holdout"]["param_hash"] == grid[0].param_hash()
    assert result.best_params is not None
    assert result.best_params["params"] == grid[0].to_dict()
    assert result.report["trials"]["total_selection_trials"] == 3 * 3


def test_no_eligible_params_means_flat_and_no_holdout() -> None:
    candles = {"ADAUSDT": _candles(288 * 6)}
    fake = FakeEvaluator(train_trades=5)
    result = run_walk_forward(candles, [_params("1"), _params("2")], _wf_config(),
                              settings=_settings(), evaluate=fake)
    assert all(f["selected"] is None for f in result.report["folds"])
    assert result.report["holdout"]["evaluated"] is False
    assert all(t.tag != "holdout" for t in fake.tasks)
    assert result.best_params is None
    assert result.report["oos_stitched"]["pooled"]["r_trade_count"] == 0
    assert result.report["oos_stitched"]["pooled"]["folds_flat_no_eligible_params"] == 3


def test_walk_forward_refuses_live_settings() -> None:
    live = _settings().model_copy(update={"trading_mode": TradingMode.LIVE})
    with pytest.raises(WalkForwardError):
        run_walk_forward({"ADAUSDT": _candles(288 * 6)}, [_params()], _wf_config(),
                         settings=live, evaluate=FakeEvaluator())


# --------------------------------------- equivalence of the shared-evidence path


async def test_multi_replay_equals_run_paper_cycle_per_param() -> None:
    """Shared-evidence multi replay == run_paper_cycle replay, metric for metric."""
    candles = _candles(420)
    settings = _settings()
    policy = owner_approved_policy_v1()
    window = ReplayWindow("w", candles[100].close_time, candles[-1].close_time)
    grid = [_params("1"), _params("3", "1.5"), _params("2", vol="1.2")]
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
        )
        assert out.window.metrics.model_dump() == ref.windows["w"].metrics.model_dump()
        assert out.counters == ref.counters
        assert out.window.equity_curve == ref.windows["w"].equity_curve
        total_closed += out.counters["closed_trades"]
    assert total_closed > 0  # fixture exercises entries and protective exits
    assert any(o.counters["closed_take_profit"] > 0 for o in multi)
    assert any(o.counters["closed_stop_loss"] > 0 for o in multi)


async def test_multi_replay_refuses_live_and_empty_grid() -> None:
    candles = _candles(150)
    window = ReplayWindow("w", candles[100].close_time, candles[-1].close_time)
    kwargs = {
        "risk_policy": owner_approved_policy_v1(),
        "instrument": _instrument(),
        "timeframe": Timeframe.M5,
        "window": window,
        "jev_port": UnavailableJevPort(),
        "spread_bps": Decimal("5"),
        "initial_quote_balance": Decimal("100"),
    }
    live = _settings().model_copy(update={"trading_mode": TradingMode.LIVE})
    with pytest.raises(PipelineReplayError):
        await replay_rules_multi(candles, settings=live, param_sets=[_params()], **kwargs)  # type: ignore[arg-type]
    with pytest.raises(PipelineReplayError):
        await replay_rules_multi(candles, settings=_settings(), param_sets=[], **kwargs)  # type: ignore[arg-type]


async def test_replay_windows_rejects_overlap_and_rules_without_params() -> None:
    candles = _candles(150)
    common = {
        "settings": _settings(),
        "risk_policy": owner_approved_policy_v1(),
        "instrument": _instrument(),
        "timeframe": Timeframe.M5,
        "jev_port": UnavailableJevPort(),
        "llm_port": UnavailableLlmPort(),
        "supervisor_budgets": None,
        "spread_bps": Decimal("5"),
        "initial_quote_balance": Decimal("100"),
    }
    a = ReplayWindow("a", candles[0].close_time, candles[100].close_time)
    b = ReplayWindow("b", candles[100].close_time, candles[-1].close_time)
    with pytest.raises(PipelineReplayError):
        await replay_windows(candles, windows=[a, b], decision_source="rules",
                             rule_params=_params(), **common)  # type: ignore[arg-type]
    with pytest.raises(PipelineReplayError):
        await replay_windows(candles, windows=[a], decision_source="rules",
                             rule_params=None, **common)  # type: ignore[arg-type]


# ------------------------------------------- end-to-end with the real evaluator


def test_walk_forward_end_to_end_report_shape(tmp_path: Path) -> None:
    candles = {"ADAUSDT": _candles(288 * 6)}
    grid = [_params("1"), _params("3", "1.5")]
    seen: list[EvalTask] = []

    def spy(task: EvalTask) -> EvalResult:
        seen.append(task)
        # No candle past the evaluated window reaches the replay.
        assert max(c.close_time for c in task.candles) <= task.window.end
        return evaluate_task(task)

    result = run_walk_forward(candles, grid, _wf_config(min_trades=1), settings=_settings(),
                              evaluate=spy)
    report = json.loads(report_json(result.report))
    for key in ("report_version", "run_id", "config", "data", "grid", "trials", "folds",
                "oos_stitched", "stability", "dsr", "holdout", "assumptions",
                "uncertainty_notes", "disclaimer"):
        assert key in report
    assert report["data"]["ADAUSDT"]["data_source"] == "binance-archive"
    assert report["data"]["ADAUSDT"]["exchange_label"] == "binance"
    assert len(report["data"]["ADAUSDT"]["data_hash"]) == 64
    assert report["grid"]["size"] == 2
    assert len(report["folds"]) == 3
    assert report["holdout"]["note"].startswith("HOLDOUT evaluated once")
    assert "dsr" in report["dsr"]
    assert sum(1 for t in seen if t.tag == "holdout") == (1 if report["holdout"]["evaluated"]
                                                         else 0)
    # Trial records: grid x folds x {train,test} (+ holdout).
    n_holdout = 1 if report["holdout"]["evaluated"] else 0
    assert len(result.trial_records) == 2 * 3 * 2 + n_holdout
    if result.best_params is not None:
        path = tmp_path / "best.json"
        path.write_text(json.dumps(result.best_params, default=str), encoding="utf-8")
        loaded = load_rule_params(str(path))
        assert loaded.param_hash() == result.best_params["param_hash"]


def test_persistence_fails_open() -> None:
    candles = {"ADAUSDT": _candles(288 * 6)}
    result = run_walk_forward(candles, [_params()], _wf_config(min_trades=1),
                              settings=_settings(), evaluate=FakeEvaluator())
    assert persist_walk_forward(None, result) == {"ok": False, "error": "no_database_url",
                                                  "rows": 0}
    out = persist_walk_forward("notadialect://user:pw@localhost/db", result)
    assert out["ok"] is False and out["rows"] == 0
    assert "pw" not in str(out)


def test_config_validation() -> None:
    with pytest.raises(WalkForwardError):
        _wf_config(min_trades=0)
    with pytest.raises(WalkForwardError):
        _wf_config(initial_balance=Decimal("0"))
    cfg = _wf_config()
    assert replace(cfg, window_candles=50).window_candles == 50


async def test_replay_pipeline_rules_passthrough_and_data_source_label() -> None:
    from aegis.backtest.pipeline_replay import replay_pipeline
    from aegis.schemas.backtest import SplitWindows

    candles = _candles(400)
    eps = timedelta(microseconds=1)
    splits = SplitWindows(
        train_start=candles[100].close_time,
        train_end=candles[200].close_time,
        validation_start=candles[200].close_time + eps,
        validation_end=candles[300].close_time,
        oos_start=candles[300].close_time + eps,
        oos_end=candles[-1].close_time,
    )
    params = _params("1")
    report = await replay_pipeline(
        candles,
        settings=_settings(),
        risk_policy=owner_approved_policy_v1(),
        instrument=_instrument(),
        timeframe=Timeframe.M5,
        splits=splits,
        jev_port=UnavailableJevPort(),
        llm_port=UnavailableLlmPort(),
        supervisor_budgets=None,
        spread_bps=Decimal("5"),
        initial_quote_balance=Decimal("100"),
        decision_source="rules",
        rule_params=params,
        data_source="binance-archive",
    )
    a = report.assumptions
    assert a["decision_source"] == "rules"
    assert a["rule_params_hash"] == params.param_hash()
    assert a["data_source"] == "binance-archive"
    assert a["data_exchange_label"] == "binance"
    assert int(a["counters"]["filled"]) > 0  # rules trade without any LLM / Jev
