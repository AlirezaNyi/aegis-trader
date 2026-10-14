"""Walk-forward tuning harness for the deterministic rule strategy (paper only).

Protocol (no look-ahead, holdout used once):

1. The data span ``[data_start, data_end]`` is split into a final **holdout** (last
   ``holdout`` periods) and a selection region before it.
2. Rolling folds over the selection region: ``train`` (N periods) immediately
   followed by ``test`` (M periods), advancing by ``step`` periods. A fold is kept
   only when its test window ends at or before the holdout start. Windows are
   inclusive on bar ``close_time`` and never overlap within a fold.
3. Per fold, every grid point is replayed on the train window (fresh paper ledger
   per point and symbol). :func:`select_params` sees **only train outcomes** and
   picks the best pooled ``expectancy_r`` among points with at least
   ``min_trades`` R-defined trades (pooled across symbols), tie-break by lower
   max drawdown (worst symbol), then by parameter hash (deterministic).
4. Every grid point is also replayed on the fold's test window, purely as an
   overfitting **diagnostic** (rank of the selected point out-of-sample). Test
   results never feed back into selection.
5. Stitched OOS = concatenation of each fold's test window run with that fold's
   selected params. A fold without an eligible point stays flat (no trades).
6. The holdout is evaluated **once**, at the end, with the params selected by the
   most recent fold. It must not be used for further tuning.

Statistics: per-trade Sharpe of R-multiples and the Deflated Sharpe Ratio (Bailey &
Lopez de Prado, 2014) are computed with ``float`` -- they are research statistics,
not money. Prices, PnL and R stay ``Decimal`` in the replay itself.

No exchange client is constructed. Candles may come from a research archive
(e.g. ``binance-archive``); the replay keeps that exchange label on the instrument
and records ``data_source`` -- Risk's instrument allowlist is by symbol.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import math
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import NormalDist
from typing import Any, Literal
from uuid import UUID, uuid4

from aegis.backtest.metrics import (
    RTrade,
    compute_window_metrics,
    per_bar_returns,
    r_metrics,
    sharpe_sortino,
)
from aegis.backtest.multi_replay import ParamOutcome, replay_rules_multi
from aegis.backtest.pipeline_replay import ReplayWindow
from aegis.config.settings import Settings, TradingMode
from aegis.interfaces.jev import UnavailableJevPort
from aegis.market_data.timeutil import interval_to_milliseconds
from aegis.risk.factory import build_risk_policy_from_settings
from aegis.schemas.backtest import WindowMetrics
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef
from aegis.strategy.rules import STRATEGY_ID as RULES_STRATEGY_ID
from aegis.strategy.rules import STRATEGY_VERSION as RULES_STRATEGY_VERSION
from aegis.strategy.rules import RuleParams

WALK_FORWARD_VERSION = "aegis-walk-forward-v1"
REPORT_VERSION = "walk-forward-report-v1"
EULER_GAMMA = 0.5772156649015329
_EPS = timedelta(microseconds=1)
_ZERO = Decimal("0")

PeriodUnit = Literal["months", "days"]

HOLDOUT_NOTE = (
    "HOLDOUT evaluated once with the most recent fold's params. Do NOT use these "
    "holdout results to change the grid, the objective or the params; doing so turns "
    "the holdout into in-sample data."
)
DISCLAIMER = (
    "Descriptive simulation only. Walk-forward OOS and holdout metrics are estimates "
    "with wide uncertainty; they do not guarantee future performance and are not a "
    "claim of profitability or edge."
)


class WalkForwardError(ValueError):
    """Invalid walk-forward configuration or data."""


# --------------------------------------------------------------------------- folds


def add_months(ts: datetime, months: int) -> datetime:
    """Calendar month arithmetic; the day is clamped to the target month's length."""
    total = ts.year * 12 + (ts.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    if month == 12:
        next_month_start = datetime(year + 1, 1, 1, tzinfo=ts.tzinfo)
    else:
        next_month_start = datetime(year, month + 1, 1, tzinfo=ts.tzinfo)
    last_day = (next_month_start - timedelta(days=1)).day
    return ts.replace(year=year, month=month, day=min(ts.day, last_day))


def _shift(ts: datetime, n: int, unit: PeriodUnit) -> datetime:
    return add_months(ts, n) if unit == "months" else ts + timedelta(days=n)


@dataclass(frozen=True)
class Fold:
    index: int
    train: ReplayWindow
    test: ReplayWindow


@dataclass(frozen=True)
class FoldPlan:
    folds: tuple[Fold, ...]
    holdout: ReplayWindow
    selection_end: datetime  # last instant usable by any fold (exclusive bound - eps)


def build_folds(
    data_start: datetime,
    data_end_exclusive: datetime,
    *,
    train: int,
    test: int,
    step: int,
    holdout: int,
    unit: PeriodUnit = "months",
) -> FoldPlan:
    """Rolling train->test folds plus a final holdout (all inclusive windows).

    ``data_end_exclusive`` is the first instant after the data (last bar
    ``close_time`` + 1ms for exchange klines). Raises when no fold fits.
    """
    for name, value in (("train", train), ("test", test), ("step", step), ("holdout", holdout)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise WalkForwardError(f"{name} must be a positive int")
    if data_end_exclusive <= data_start:
        raise WalkForwardError("data range is empty")
    holdout_start = _shift(data_end_exclusive, -holdout, unit)
    if holdout_start <= data_start:
        raise WalkForwardError("holdout covers the whole data range")
    folds: list[Fold] = []
    k = 0
    while True:
        train_start = _shift(data_start, k * step, unit)
        train_end = _shift(train_start, train, unit)
        test_end = _shift(train_end, test, unit)
        if test_end > holdout_start:
            break
        folds.append(
            Fold(
                index=k,
                train=ReplayWindow(f"train-{k}", train_start, train_end - _EPS),
                test=ReplayWindow(f"test-{k}", train_end, test_end - _EPS),
            )
        )
        k += 1
    if not folds:
        raise WalkForwardError("no fold fits before the holdout; reduce train/test/holdout")
    return FoldPlan(
        folds=tuple(folds),
        holdout=ReplayWindow("holdout", holdout_start, data_end_exclusive - _EPS),
        selection_end=holdout_start - _EPS,
    )


# ---------------------------------------------------------------------------- grid


def _grid_value(name: str, raw: Any) -> str:
    if isinstance(raw, bool):
        raise WalkForwardError(f"grid value for {name} must be a number or string")
    if isinstance(raw, (int, str)):
        text = str(raw)
    elif isinstance(raw, float):
        text = repr(raw)  # shortest round-trip literal as typed in JSON
    else:
        raise WalkForwardError(f"grid value for {name} must be a number or string")
    try:
        Decimal(text)
    except InvalidOperation as exc:
        raise WalkForwardError(f"grid value for {name} is not a decimal: {text!r}") from exc
    return text


def expand_grid(spec: Any) -> tuple[list[RuleParams], list[dict[str, str]]]:
    """Expand a grid spec into unique ``RuleParams`` (order preserved).

    ``spec`` is either a list of full parameter dicts, or a dict mapping each
    ``RuleParams`` field to a scalar or a list (cartesian product, keys in
    ``RuleParams`` field order). Invalid combinations (e.g. ``rsi_min >= rsi_max``)
    are skipped and returned as the second element.
    """
    names = list(RuleParams.__dataclass_fields__)
    raw_points: list[dict[str, str]] = []
    if isinstance(spec, list):
        for item in spec:
            if not isinstance(item, dict):
                raise WalkForwardError("grid list entries must be objects")
            raw_points.append({k: _grid_value(k, v) for k, v in item.items()})
    elif isinstance(spec, dict):
        unknown = sorted(set(spec) - set(names))
        missing = [n for n in names if n not in spec]
        if unknown or missing:
            raise WalkForwardError(f"grid keys invalid: missing={missing} unknown={unknown}")
        axes: list[list[str]] = []
        for n in names:
            values = spec[n] if isinstance(spec[n], list) else [spec[n]]
            if not values:
                raise WalkForwardError(f"grid axis {n} is empty")
            axes.append([_grid_value(n, v) for v in values])
        for combo in itertools.product(*axes):
            raw_points.append(dict(zip(names, combo, strict=True)))
    else:
        raise WalkForwardError("grid must be a list of objects or an object of lists")

    params: list[RuleParams] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    for point in raw_points:
        try:
            p = RuleParams.from_dict(point)
        except (ValueError, TypeError):
            skipped.append(point)
            continue
        h = p.param_hash()
        if h not in seen:
            seen.add(h)
            params.append(p)
    if not params:
        raise WalkForwardError("grid has no valid parameter sets")
    return params, skipped


# ----------------------------------------------------------------------- statistics


@dataclass(frozen=True)
class Moments:
    n: int
    mean: float
    std: float  # sample (ddof=1)
    skew: float  # population, m3 / m2^1.5
    kurtosis: float  # population, non-excess (normal = 3)


def moments(values: Sequence[float]) -> Moments | None:
    n = len(values)
    if n < 2:
        return None
    mean = math.fsum(values) / n
    m2 = math.fsum((v - mean) ** 2 for v in values) / n
    if m2 <= 0:
        return None
    m3 = math.fsum((v - mean) ** 3 for v in values) / n
    m4 = math.fsum((v - mean) ** 4 for v in values) / n
    std = math.sqrt(m2 * n / (n - 1))
    return Moments(n=n, mean=mean, std=std, skew=m3 / m2**1.5, kurtosis=m4 / m2**2)


def per_trade_sharpe(r_multiples: Sequence[float]) -> float | None:
    """Mean R / sample stdev of R (per trade, not annualized)."""
    m = moments(r_multiples)
    return None if m is None else m.mean / m.std


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """E[max SR] under the null after ``n_trials`` independent trials (BLdP 2014).

    ``SR0 = sqrt(V) * ((1 - g) * Z^-1(1 - 1/N) + g * Z^-1(1 - 1/(N e)))`` with ``g``
    the Euler-Mascheroni constant. ``N <= 1`` means no selection: ``SR0 = 0``.
    """
    if sr_variance < 0:
        raise ValueError("sr_variance must be >= 0")
    if n_trials <= 1:
        return 0.0
    z = NormalDist()
    return math.sqrt(sr_variance) * (
        (1 - EULER_GAMMA) * z.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * z.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def probabilistic_sharpe(
    sr: float, sr_benchmark: float, n_obs: int, skew: float, kurtosis: float
) -> float | None:
    """PSR: ``Z[(SR - SR*) sqrt(T - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2)]``.

    ``kurtosis`` is non-excess (normal = 3). None when undefined.
    """
    if n_obs < 2:
        return None
    denom = 1 - skew * sr + (kurtosis - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    return NormalDist().cdf((sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom))


def deflated_sharpe(
    *,
    sr: float,
    n_obs: int,
    skew: float,
    kurtosis: float,
    sr_variance: float,
    n_trials: int,
) -> tuple[float | None, float]:
    """Return ``(DSR, SR0)``: PSR against the expected max SR of ``n_trials``."""
    sr0 = expected_max_sharpe(sr_variance, n_trials)
    return probabilistic_sharpe(sr, sr0, n_obs, skew, kurtosis), sr0


# ------------------------------------------------------------------- evaluation task


@dataclass(frozen=True)
class EvalTask:
    """One replay job: all ``param_sets`` on one window of one symbol (picklable)."""

    symbol: str
    exchange: str
    market_type: str
    timeframe: str
    window: ReplayWindow
    param_sets: tuple[RuleParams, ...]
    candles: tuple[Candle, ...]
    settings: Settings
    spread_bps: Decimal
    initial_balance: Decimal
    window_candles: int
    tag: str  # "train" | "test" | "holdout"
    fold: int | None


@dataclass
class TrialResult:
    """Picklable per-parameter result of an ``EvalTask``."""

    param_hash: str
    params: dict[str, str]
    metrics: WindowMetrics
    r_multiples: list[Decimal]
    trade_pnls: list[Decimal]
    initial_risks: list[Decimal]
    bar_returns: list[Decimal]
    counters: dict[str, int]
    candidate_rejects: dict[str, int]


@dataclass
class EvalResult:
    task_symbol: str
    tag: str
    fold: int | None
    window: ReplayWindow
    trials: list[TrialResult]
    bars_in_window: int


def _trial_from(outcome: ParamOutcome) -> TrialResult:
    w = outcome.window
    return TrialResult(
        param_hash=outcome.params.param_hash(),
        params=outcome.params.to_dict(),
        metrics=w.metrics,
        r_multiples=[t.r_multiple for t in w.r_trades],
        trade_pnls=[t.pnl for t in w.r_trades],
        initial_risks=[t.initial_risk for t in w.r_trades],
        bar_returns=per_bar_returns(w.equity_curve, w.initial_equity),
        counters=dict(outcome.counters),
        candidate_rejects=dict(outcome.candidate_rejects),
    )


def evaluate_task(task: EvalTask) -> EvalResult:
    """Run one ``EvalTask`` (module-level so a process pool can pickle it)."""
    if task.settings.trading_mode == TradingMode.LIVE:
        raise WalkForwardError("walk-forward refuses trading_mode=live")
    instrument = InstrumentRef(
        exchange=task.exchange, market_type=MarketType(task.market_type), symbol=task.symbol
    )
    outcomes = asyncio.run(
        replay_rules_multi(
            task.candles,
            settings=task.settings,
            risk_policy=build_risk_policy_from_settings(task.settings),
            instrument=instrument,
            timeframe=Timeframe(task.timeframe),
            window=task.window,
            param_sets=task.param_sets,
            jev_port=UnavailableJevPort(),
            spread_bps=task.spread_bps,
            initial_quote_balance=task.initial_balance,
            window_candles=task.window_candles,
        )
    )
    bars = outcomes[0].counters["bars"] if outcomes else 0
    return EvalResult(
        task_symbol=task.symbol,
        tag=task.tag,
        fold=task.fold,
        window=task.window,
        trials=[_trial_from(o) for o in outcomes],
        bars_in_window=bars,
    )


Evaluator = Callable[[EvalTask], EvalResult]


# ------------------------------------------------------------------------ selection


@dataclass(frozen=True)
class PooledScore:
    param_hash: str
    params: dict[str, str]
    r_trade_count: int
    expectancy_r: Decimal | None
    worst_max_drawdown: Decimal


def pooled_scores(per_symbol: Mapping[str, Sequence[TrialResult]]) -> list[PooledScore]:
    """Pool one window's trials across symbols by parameter hash.

    ``expectancy_r = sum(pnl) / sum(initial risk)`` over all symbols' R-trades;
    drawdown = worst (max) per-symbol max drawdown.
    """
    by_hash: dict[str, list[TrialResult]] = {}
    order: list[str] = []
    for trials in per_symbol.values():
        for t in trials:
            if t.param_hash not in by_hash:
                by_hash[t.param_hash] = []
                order.append(t.param_hash)
            by_hash[t.param_hash].append(t)
    out: list[PooledScore] = []
    for h in order:
        trials = by_hash[h]
        rts = [
            RTrade(pnl=p, initial_risk=r)
            for t in trials
            for p, r in zip(t.trade_pnls, t.initial_risks, strict=True)
        ]
        _, expectancy = r_metrics(rts)
        out.append(
            PooledScore(
                param_hash=h,
                params=trials[0].params,
                r_trade_count=len(rts),
                expectancy_r=expectancy,
                worst_max_drawdown=max(t.metrics.max_drawdown for t in trials),
            )
        )
    return out


def select_params(scores: Sequence[PooledScore], *, min_trades: int) -> PooledScore | None:
    """Best pooled expectancy_r among points with ``>= min_trades`` R-trades.

    Tie-break: lower worst max drawdown, then lexicographic param hash. Returns None
    when no point is eligible (the strategy then stays flat in the next test window).
    Callers must pass **train-window** scores only.
    """
    eligible = [
        s for s in scores if s.r_trade_count >= min_trades and s.expectancy_r is not None
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda s: (-(s.expectancy_r or _ZERO), s.worst_max_drawdown, s.param_hash),
    )


# ------------------------------------------------------------------------ stitching


def _decimal_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def summarize_series(
    *, r_multiples: Sequence[Decimal], pnls: Sequence[Decimal], risks: Sequence[Decimal],
    bar_returns: Sequence[Decimal], initial: Decimal
) -> dict[str, Any]:
    """Stitched stats: R stats, 95% normal CI of mean R, compounded equity stats."""
    rts = [RTrade(pnl=p, initial_risk=r) for p, r in zip(pnls, risks, strict=True)]
    avg_r, expectancy = r_metrics(rts)
    floats = [float(r) for r in r_multiples]
    m = moments(floats)
    ci: list[float] | None = None
    if m is not None:
        half = 1.959963984540054 * m.std / math.sqrt(m.n)
        ci = [m.mean - half, m.mean + half]
    equity = initial
    peak = initial
    max_dd = _ZERO
    for ret in bar_returns:
        equity = equity * (Decimal("1") + ret)
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
    sharpe_bar, _ = sharpe_sortino(list(bar_returns))
    wins = sum(1 for p in pnls if p > 0)
    return {
        "r_trade_count": len(rts),
        "win_rate": None if not rts else wins / len(rts),
        "avg_r": _decimal_str(avg_r),
        "expectancy_r": _decimal_str(expectancy),
        "avg_r_ci95_normal": ci,
        "per_trade_sharpe_r": None if m is None else m.mean / m.std,
        "r_skew": None if m is None else m.skew,
        "r_kurtosis": None if m is None else m.kurtosis,
        "net_pnl": str(sum(pnls, _ZERO)),
        "compounded_return": str(equity / initial - 1) if initial > 0 else None,
        "max_drawdown": str(max_dd),
        "sharpe_per_bar": _decimal_str(sharpe_bar),
        "bars": len(bar_returns),
    }


# --------------------------------------------------------------------------- driver


@dataclass(frozen=True)
class WalkForwardConfig:
    train: int
    test: int
    step: int
    holdout: int
    min_trades: int
    spread_bps: Decimal
    initial_balance: Decimal
    unit: PeriodUnit = "months"
    window_candles: int = 100
    timeframe: Timeframe = Timeframe.M5
    market_type: MarketType = MarketType.SPOT
    data_source: str = "candle-store"

    def __post_init__(self) -> None:
        if isinstance(self.min_trades, bool) or self.min_trades < 1:
            raise WalkForwardError("min_trades must be >= 1")
        if self.spread_bps < 0:
            raise WalkForwardError("spread_bps must be >= 0")
        if self.initial_balance <= 0:
            raise WalkForwardError("initial_balance must be positive")
        if self.window_candles < 1:
            raise WalkForwardError("window_candles must be >= 1")


@dataclass
class TrialRecord:
    """Flat record for persistence: one (symbol, window, params) evaluation."""

    symbol: str
    tag: str
    fold: int | None
    window: ReplayWindow
    param_hash: str
    params: dict[str, str]
    metrics: WindowMetrics
    selected: bool
    data_hash: str


@dataclass
class WalkForwardResult:
    report: dict[str, Any]
    best_params: dict[str, Any] | None
    trial_records: list[TrialRecord] = field(default_factory=list)


def dataset_hash(candles: Sequence[Candle]) -> str:
    """SHA-256 over final bars (symbol, interval, open_time, OHLCV)."""
    h = hashlib.sha256()
    for c in candles:
        if not c.is_final:
            continue
        h.update(
            (
                f"{c.instrument.exchange}|{c.instrument.symbol}|{c.interval.value}|"
                f"{c.open_time.isoformat()}|{c.open}|{c.high}|{c.low}|{c.close}|{c.volume}\n"
            ).encode()
        )
    return h.hexdigest()


def _slice_for(
    candles: Sequence[Candle], close_times: Sequence[datetime], window: ReplayWindow, warmup: int
) -> tuple[Candle, ...]:
    """Bars with close_time <= window.end plus ``warmup`` bars before window.start."""
    lo = max(0, bisect_left(close_times, window.start) - warmup)
    hi = bisect_right(close_times, window.end)
    return tuple(candles[lo:hi])


def _window_json(w: ReplayWindow) -> dict[str, str]:
    return {"name": w.name, "start": w.start.isoformat(), "end": w.end.isoformat()}


def _metrics_brief(m: WindowMetrics) -> dict[str, Any]:
    return {
        "r_trade_count": m.r_trade_count,
        "trade_count": m.trade_count,
        "expectancy_r": _decimal_str(m.expectancy_r),
        "avg_r": _decimal_str(m.avg_r),
        "win_rate": _decimal_str(m.win_rate),
        "net_return_after_costs": str(m.net_return_after_costs),
        "max_drawdown": str(m.max_drawdown),
        "sharpe_per_bar": _decimal_str(m.sharpe_per_bar),
        "exposure": str(m.exposure),
        "cost_contribution": str(m.cost_contribution),
    }


def _run_tasks(
    tasks: Sequence[EvalTask], evaluate: Evaluator, executor: Executor | None
) -> list[EvalResult]:
    if executor is None:
        return [evaluate(t) for t in tasks]
    futures = [executor.submit(evaluate, t) for t in tasks]
    return [f.result() for f in futures]


def run_walk_forward(
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    grid: Sequence[RuleParams],
    config: WalkForwardConfig,
    *,
    settings: Settings,
    evaluate: Evaluator = evaluate_task,
    executor: Executor | None = None,
    run_id: UUID | None = None,
    now: datetime | None = None,
    grid_skipped: Sequence[Mapping[str, str]] = (),
    progress: Callable[[str], None] | None = None,
) -> WalkForwardResult:
    """Run the walk-forward protocol (see module docstring) and build the report.

    The holdout task is created and evaluated only after every fold's selection is
    final, with exactly one parameter set.
    """
    if settings.trading_mode not in {TradingMode.PAPER, TradingMode.DEVELOPMENT}:
        raise WalkForwardError("walk-forward requires paper/development trading_mode")
    if not candles_by_symbol:
        raise WalkForwardError("no symbols")
    if not grid:
        raise WalkForwardError("empty grid")
    say = progress or (lambda _msg: None)
    rid = run_id or uuid4()
    created = now or datetime.now(tz=UTC)

    finals: dict[str, list[Candle]] = {}
    close_times: dict[str, list[datetime]] = {}
    data_info: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for symbol, cs in candles_by_symbol.items():
        fs = sorted(
            (c for c in cs if c.is_final and c.interval == config.timeframe),
            key=lambda c: c.open_time,
        )
        if not fs:
            raise WalkForwardError(f"no final candles for {symbol}")
        exchanges = {c.instrument.exchange for c in fs}
        if len(exchanges) != 1:
            raise WalkForwardError(f"{symbol}: mixed exchange labels {sorted(exchanges)}")
        finals[symbol] = fs
        close_times[symbol] = [c.close_time for c in fs]
        hashes[symbol] = dataset_hash(fs)
        data_info[symbol] = {
            "exchange_label": fs[0].instrument.exchange,
            "data_source": config.data_source,
            "bars": len(fs),
            "first_open": fs[0].open_time.isoformat(),
            "last_close": fs[-1].close_time.isoformat(),
            "data_hash": hashes[symbol],
        }

    # Common span across symbols so every fold covers every symbol.
    data_start = max(fs[0].open_time for fs in finals.values())
    step_ms = interval_to_milliseconds(config.timeframe.value)
    # Exclusive end = last bar open + interval (independent of close_time precision).
    data_end_excl = min(fs[-1].open_time for fs in finals.values()) + timedelta(
        milliseconds=step_ms
    )
    plan = build_folds(
        data_start,
        data_end_excl,
        train=config.train,
        test=config.test,
        step=config.step,
        holdout=config.holdout,
        unit=config.unit,
    )
    say(f"folds={len(plan.folds)} holdout={plan.holdout.start.isoformat()}..")

    params_tuple = tuple(grid)

    def _task(symbol: str, window: ReplayWindow, ps: tuple[RuleParams, ...], tag: str,
              fold: int | None) -> EvalTask:
        fs = finals[symbol]
        return EvalTask(
            symbol=symbol,
            exchange=fs[0].instrument.exchange,
            market_type=config.market_type.value,
            timeframe=config.timeframe.value,
            window=window,
            param_sets=ps,
            candles=_slice_for(fs, close_times[symbol], window, config.window_candles),
            settings=settings,
            spread_bps=config.spread_bps,
            initial_balance=config.initial_balance,
            window_candles=config.window_candles,
            tag=tag,
            fold=fold,
        )

    # ---- Selection phase: train + diagnostic test windows (never the holdout).
    sel_tasks: list[EvalTask] = []
    for fold in plan.folds:
        if fold.test.end >= plan.holdout.start:  # defensive; build_folds guarantees it
            raise WalkForwardError("fold overlaps holdout")
        for symbol in finals:
            sel_tasks.append(_task(symbol, fold.train, params_tuple, "train", fold.index))
            sel_tasks.append(_task(symbol, fold.test, params_tuple, "test", fold.index))
    say(f"selection tasks={len(sel_tasks)} params={len(params_tuple)}")
    sel_results = _run_tasks(sel_tasks, evaluate, executor)

    train_res: dict[int, dict[str, list[TrialResult]]] = {}
    test_res: dict[int, dict[str, list[TrialResult]]] = {}
    bars_count: dict[tuple[int, str, str], int] = {}
    for res in sel_results:
        if res.fold is None:
            raise WalkForwardError("selection result is missing its fold index")
        target = train_res if res.tag == "train" else test_res
        target.setdefault(res.fold, {})[res.task_symbol] = res.trials
        bars_count[(res.fold, res.tag, res.task_symbol)] = res.bars_in_window

    trial_records: list[TrialRecord] = []
    fold_reports: list[dict[str, Any]] = []
    selections: dict[int, PooledScore | None] = {}
    oos_parts: dict[str, list[TrialResult]] = {s: [] for s in finals}
    train_trial_sharpes: list[float] = []
    for fold in plan.folds:
        train_scores = pooled_scores(train_res[fold.index])
        for symbol_trials in train_res[fold.index].values():
            for t in symbol_trials:
                srt = per_trade_sharpe([float(r) for r in t.r_multiples])
                if srt is not None:
                    train_trial_sharpes.append(srt)
        # Selection uses TRAIN scores only.
        chosen = select_params(train_scores, min_trades=config.min_trades)
        selections[fold.index] = chosen
        test_scores = pooled_scores(test_res[fold.index])
        rank_pct: float | None = None
        chosen_test: PooledScore | None = None
        if chosen is not None:
            chosen_test = next(s for s in test_scores if s.param_hash == chosen.param_hash)
            defined = [s for s in test_scores if s.expectancy_r is not None]
            if chosen_test.expectancy_r is not None and len(defined) > 1:
                below = sum(
                    1 for s in defined if (s.expectancy_r or _ZERO) < chosen_test.expectancy_r
                )
                rank_pct = below / (len(defined) - 1)
            for symbol in finals:
                t = next(
                    x for x in test_res[fold.index][symbol] if x.param_hash == chosen.param_hash
                )
                oos_parts[symbol].append(t)
        else:
            # Flat in this test window: zero returns, zero trades.
            for symbol in finals:
                n = bars_count[(fold.index, "test", symbol)]
                oos_parts[symbol].append(
                    TrialResult(
                        param_hash="flat",
                        params={},
                        metrics=compute_window_metrics(
                            window="flat",
                            equity_curve=[config.initial_balance] * n,
                            initial_equity=config.initial_balance,
                            trade_pnls=[],
                            costs=[],
                            notionals=[],
                            exposure_bars=0,
                            total_bars=n,
                        ),
                        r_multiples=[],
                        trade_pnls=[],
                        initial_risks=[],
                        bar_returns=[_ZERO] * n,
                        counters={},
                        candidate_rejects={},
                    )
                )
        for tag, res_map in (("train", train_res), ("test", test_res)):
            for symbol, trials in res_map[fold.index].items():
                for t in trials:
                    trial_records.append(
                        TrialRecord(
                            symbol=symbol,
                            tag=tag,
                            fold=fold.index,
                            window=fold.train if tag == "train" else fold.test,
                            param_hash=t.param_hash,
                            params=t.params,
                            metrics=t.metrics,
                            selected=chosen is not None and t.param_hash == chosen.param_hash,
                            data_hash=hashes[symbol],
                        )
                    )
        per_symbol_test: dict[str, Any] = {}
        per_symbol_train: dict[str, Any] = {}
        if chosen is not None:
            for symbol in finals:
                tr = next(
                    x for x in train_res[fold.index][symbol] if x.param_hash == chosen.param_hash
                )
                te = next(
                    x for x in test_res[fold.index][symbol] if x.param_hash == chosen.param_hash
                )
                per_symbol_train[symbol] = {
                    **_metrics_brief(tr.metrics),
                    "candidate_rejects": tr.candidate_rejects,
                }
                per_symbol_test[symbol] = {
                    **_metrics_brief(te.metrics),
                    "candidate_rejects": te.candidate_rejects,
                    "counters": te.counters,
                }
        fold_reports.append(
            {
                "index": fold.index,
                "train": _window_json(fold.train),
                "test": _window_json(fold.test),
                "eligible_points": sum(
                    1
                    for s in train_scores
                    if s.r_trade_count >= config.min_trades and s.expectancy_r is not None
                ),
                "max_train_r_trades": max((s.r_trade_count for s in train_scores), default=0),
                "selected": None
                if chosen is None
                else {
                    "param_hash": chosen.param_hash,
                    "params": chosen.params,
                    "train_pooled_expectancy_r": _decimal_str(chosen.expectancy_r),
                    "train_pooled_r_trades": chosen.r_trade_count,
                    "train_worst_max_drawdown": str(chosen.worst_max_drawdown),
                    "test_pooled_expectancy_r": None
                    if chosen_test is None
                    else _decimal_str(chosen_test.expectancy_r),
                    "test_pooled_r_trades": None
                    if chosen_test is None
                    else chosen_test.r_trade_count,
                    "test_rank_pct_among_grid": rank_pct,
                    "per_symbol_train": per_symbol_train,
                    "per_symbol_test": per_symbol_test,
                },
            }
        )

    # ---- Stitched OOS.
    def _stitch(parts: Sequence[TrialResult]) -> dict[str, Any]:
        return summarize_series(
            r_multiples=[r for p in parts for r in p.r_multiples],
            pnls=[x for p in parts for x in p.trade_pnls],
            risks=[x for p in parts for x in p.initial_risks],
            bar_returns=[x for p in parts for x in p.bar_returns],
            initial=config.initial_balance,
        )

    stitched_per_symbol = {s: _stitch(parts) for s, parts in oos_parts.items()}
    pooled_parts = [p for parts in oos_parts.values() for p in parts]
    pooled_r = [float(r) for p in pooled_parts for r in p.r_multiples]
    pooled_rts = [
        RTrade(pnl=a, initial_risk=b)
        for p in pooled_parts
        for a, b in zip(p.trade_pnls, p.initial_risks, strict=True)
    ]
    pooled_avg, pooled_exp = r_metrics(pooled_rts)
    pooled_m = moments(pooled_r)
    ci = None
    if pooled_m is not None:
        half = 1.959963984540054 * pooled_m.std / math.sqrt(pooled_m.n)
        ci = [pooled_m.mean - half, pooled_m.mean + half]
    stitched_pooled = {
        "r_trade_count": len(pooled_r),
        "expectancy_r": _decimal_str(pooled_exp),
        "avg_r": _decimal_str(pooled_avg),
        "avg_r_ci95_normal": ci,
        "per_trade_sharpe_r": None if pooled_m is None else pooled_m.mean / pooled_m.std,
        "net_pnl": str(sum((p for x in pooled_parts for p in x.trade_pnls), _ZERO)),
        "folds_flat_no_eligible_params": sum(1 for v in selections.values() if v is None),
    }

    # ---- DSR (on stitched pooled OOS per-trade R Sharpe).
    total_trials = len(params_tuple) * len(plan.folds)
    dsr_block: dict[str, Any] = {
        "method": (
            "Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio: DSR = PSR(SR0) = "
            "Z[(SR - SR0) sqrt(T-1) / sqrt(1 - skew*SR + (kurt-1)/4 SR^2)], SR0 = "
            "sqrt(V) ((1-g) Z^-1(1-1/N) + g Z^-1(1-1/(N e))), g = Euler-Mascheroni."
        ),
        "assumptions": (
            "SR = per-trade Sharpe of R-multiples (mean/sample stdev) of the stitched "
            "pooled OOS trades; T = number of those trades; skew/kurt (non-excess) from "
            "the same trades. V = variance of per-trade R Sharpe across all train "
            "trials (each grid point x fold x symbol with >=2 R-trades). N = total "
            "trials = grid size x folds (conservative: trials are correlated, so the "
            "effective N is smaller); a variant with N = grid size is also reported. "
            "Trials with different sample lengths are pooled into V (approximation)."
        ),
        "n_trials_total": total_trials,
        "n_trials_grid": len(params_tuple),
        "train_trials_with_sharpe": len(train_trial_sharpes),
    }
    sr_var: float | None = None
    if len(train_trial_sharpes) >= 2:
        mu = math.fsum(train_trial_sharpes) / len(train_trial_sharpes)
        sr_var = math.fsum((x - mu) ** 2 for x in train_trial_sharpes) / (
            len(train_trial_sharpes) - 1
        )
    dsr_block["sr_variance_across_trials"] = sr_var
    if pooled_m is not None and sr_var is not None:
        sr = pooled_m.mean / pooled_m.std
        dsr, sr0 = deflated_sharpe(
            sr=sr, n_obs=pooled_m.n, skew=pooled_m.skew, kurtosis=pooled_m.kurtosis,
            sr_variance=sr_var, n_trials=total_trials,
        )
        dsr_grid, sr0_grid = deflated_sharpe(
            sr=sr, n_obs=pooled_m.n, skew=pooled_m.skew, kurtosis=pooled_m.kurtosis,
            sr_variance=sr_var, n_trials=len(params_tuple),
        )
        psr0 = probabilistic_sharpe(sr, 0.0, pooled_m.n, pooled_m.skew, pooled_m.kurtosis)
        dsr_block.update(
            {
                "sr_oos_per_trade": sr,
                "t_obs": pooled_m.n,
                "skew": pooled_m.skew,
                "kurtosis": pooled_m.kurtosis,
                "sr0_expected_max": sr0,
                "dsr": dsr,
                "sr0_expected_max_n_grid": sr0_grid,
                "dsr_n_grid": dsr_grid,
                "psr_vs_zero": psr0,
            }
        )
    else:
        dsr_block["dsr"] = None
        dsr_block["note"] = "undefined: fewer than 2 OOS trades or trial Sharpes"

    # ---- Stability of chosen params across folds.
    chosen_list = [s for s in selections.values() if s is not None]
    field_values: dict[str, dict[str, int]] = {}
    for s in chosen_list:
        for k, v in s.params.items():
            field_values.setdefault(k, {})
            field_values[k][v] = field_values[k].get(v, 0) + 1
    modal = Counter(s.param_hash for s in chosen_list).most_common(1)
    rank_values = [
        f["selected"]["test_rank_pct_among_grid"]
        for f in fold_reports
        if f["selected"] is not None and f["selected"]["test_rank_pct_among_grid"] is not None
    ]
    stability = {
        "folds": len(plan.folds),
        "folds_with_selection": len(chosen_list),
        "distinct_selected": len({s.param_hash for s in chosen_list}),
        "modal_param_hash": modal[0][0] if modal else None,
        "modal_count": modal[0][1] if modal else 0,
        "per_field_value_counts": field_values,
        "test_rank_pct_values": rank_values,
        "test_rank_pct_median": _median(rank_values),
        "share_folds_selected_below_test_median": None
        if not rank_values
        else sum(1 for v in rank_values if v < 0.5) / len(rank_values),
        "rank_note": (
            "test_rank_pct = share of other grid points with lower test expectancy_r "
            "(1 = best OOS, 0 = worst). Values persistently < 0.5 suggest selection "
            "overfits the train window (a CSCV/PBO-style diagnostic, not full CSCV). "
            "Test-window results of non-selected points are diagnostics only and never "
            "feed selection."
        ),
    }

    # ---- Holdout: once, with the most recent fold's params.
    last_fold = plan.folds[-1]
    final_choice = selections[last_fold.index]
    holdout_block: dict[str, Any] = {
        "window": _window_json(plan.holdout),
        "note": HOLDOUT_NOTE,
        "params_from_fold": last_fold.index,
    }
    best_params: dict[str, Any] | None = None
    if final_choice is None:
        holdout_block["evaluated"] = False
        holdout_block["reason"] = "most recent fold had no eligible params (strategy flat)"
    else:
        final_params = next(p for p in params_tuple if p.param_hash() == final_choice.param_hash)
        say("holdout: evaluating once")
        ho_tasks = [
            _task(symbol, plan.holdout, (final_params,), "holdout", None) for symbol in finals
        ]
        ho_results = _run_tasks(ho_tasks, evaluate, executor)
        per_symbol_ho: dict[str, Any] = {}
        ho_trials: list[TrialResult] = []
        for res in ho_results:
            t = res.trials[0]
            ho_trials.append(t)
            per_symbol_ho[res.task_symbol] = {
                **_metrics_brief(t.metrics),
                "candidate_rejects": t.candidate_rejects,
                "counters": t.counters,
            }
            trial_records.append(
                TrialRecord(
                    symbol=res.task_symbol,
                    tag="holdout",
                    fold=None,
                    window=plan.holdout,
                    param_hash=t.param_hash,
                    params=t.params,
                    metrics=t.metrics,
                    selected=True,
                    data_hash=hashes[res.task_symbol],
                )
            )
        ho_rts = [
            RTrade(pnl=a, initial_risk=b)
            for t in ho_trials
            for a, b in zip(t.trade_pnls, t.initial_risks, strict=True)
        ]
        _, ho_exp = r_metrics(ho_rts)
        ho_m = moments([float(r) for t in ho_trials for r in t.r_multiples])
        holdout_block.update(
            {
                "evaluated": True,
                "param_hash": final_choice.param_hash,
                "params": final_choice.params,
                "per_symbol": per_symbol_ho,
                "pooled": {
                    "r_trade_count": len(ho_rts),
                    "expectancy_r": _decimal_str(ho_exp),
                    "avg_r_ci95_normal": None
                    if ho_m is None
                    else [
                        ho_m.mean - 1.959963984540054 * ho_m.std / math.sqrt(ho_m.n),
                        ho_m.mean + 1.959963984540054 * ho_m.std / math.sqrt(ho_m.n),
                    ],
                },
            }
        )
        best_params = {
            "params": final_choice.params,
            "param_hash": final_choice.param_hash,
            "strategy": RULES_STRATEGY_VERSION,
            "selected_by": (
                f"walk-forward {WALK_FORWARD_VERSION} run {rid}: most recent fold "
                f"{last_fold.index} train {last_fold.train.start.date()}.."
                f"{last_fold.train.end.date()}; objective pooled expectancy_r with "
                f">= {config.min_trades} R-trades, tie-break lower max drawdown"
            ),
            "oos_metrics": stitched_pooled,
            "holdout_metrics": holdout_block["pooled"],
            "note": (
                "Research output, not owner-approved risk policy and not a profitability "
                "claim. " + HOLDOUT_NOTE
            ),
        }

    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "software_version": WALK_FORWARD_VERSION,
        "run_id": str(rid),
        "created_at": created.isoformat(),
        "strategy": {"id": RULES_STRATEGY_ID, "version": RULES_STRATEGY_VERSION},
        "config": {
            "unit": config.unit,
            "train": config.train,
            "test": config.test,
            "step": config.step,
            "holdout": config.holdout,
            "min_trades": config.min_trades,
            "spread_bps": str(config.spread_bps),
            "initial_balance": str(config.initial_balance),
            "window_candles": config.window_candles,
            "timeframe": config.timeframe.value,
            "market_type": config.market_type.value,
            "risk_policy_version": settings.risk_policy_version,
            "fee_bps": str(settings.effective_fee_bps),
            "slippage_bps": str(settings.paper_slippage_bps),
            "objective": "pooled expectancy_r (sum pnl / sum initial risk) across symbols",
            "tie_break": "lower worst-symbol max drawdown, then param hash",
        },
        "data": data_info,
        "common_span": {
            "start": data_start.isoformat(),
            "end_exclusive": data_end_excl.isoformat(),
        },
        "grid": {
            "size": len(params_tuple),
            "points": [p.to_dict() for p in params_tuple],
            "skipped_invalid": [dict(x) for x in grid_skipped],
        },
        "trials": {
            "total_selection_trials": total_trials,
            "evaluations_train": len(params_tuple) * len(plan.folds) * len(finals),
            "evaluations_test_diagnostic": len(params_tuple) * len(plan.folds) * len(finals),
            "evaluations_holdout": 0 if final_choice is None else len(finals),
        },
        "folds": fold_reports,
        "oos_stitched": {"per_symbol": stitched_per_symbol, "pooled": stitched_pooled},
        "stability": stability,
        "dsr": dsr_block,
        "holdout": holdout_block,
        "assumptions": {
            "data_source": config.data_source,
            "exchange_labels": {s: d["exchange_label"] for s, d in data_info.items()},
            "risk_allowlist": "by symbol; candles keep their archive exchange label",
            "spread": (
                f"constant assumed spread {config.spread_bps} bps (synthetic book around "
                "bar close); historical order books not stored"
            ),
            "fills": "PaperBroker immediate fill at decision-bar close +/- slippage",
            "exits": "bar-resolution stop/target; stop first when both touched",
            "isolation": (
                "fresh paper ledger, equity tracker and proposal history per (window, "
                "symbol, grid point); open positions at window end are marked, not closed"
            ),
            "warmup": (
                f"up to {config.window_candles} bars before a window start are visible as "
                "past context; no decision is taken on them for that window"
            ),
            "leakage_controls": (
                "train precedes test in time; selection reads train outcomes only; "
                "holdout evaluated once after all selections; trade outcomes inside a "
                "window are resolved only by bars inside that window"
            ),
            "jev": "UnavailableJevPort (no historical Jev)",
            "llm": "not used (decision_source=rules)",
        },
        "uncertainty_notes": (
            "Fold test windows are short; per-fold stats are noisy. Normal-approximation "
            "CIs ignore autocorrelation and fat tails. DSR assumptions are listed in "
            "dsr.assumptions. Spread is assumed, not observed. Archive candles may differ "
            "from Toobit prices. Risk policy limits (e.g. drawdown / loss locks) stop "
            "trading inside a window once hit, which truncates samples (see "
            "candidate_rejects)."
        ),
        "disclaimer": DISCLAIMER,
    }
    return WalkForwardResult(report=report, best_params=best_params, trial_records=trial_records)


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def report_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=False, default=str)
