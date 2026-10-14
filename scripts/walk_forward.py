#!/usr/bin/env python3
"""Walk-forward tuning of the deterministic rule strategy on stored candles (paper only).

Loads finalized candles from Postgres ``market_candles`` and runs the walk-forward
protocol in ``aegis.backtest.walk_forward``: rolling train->test folds, selection on
train only, stitched OOS, Deflated Sharpe Ratio, and a final holdout evaluated
**once**. Never constructs an exchange client and never submits orders.

Money/spread/min-trades values are required arguments (no invented defaults). The
fold lengths (train=3, test=1, step=1, holdout=2 months) are engineering defaults.

Usage:
  .venv/bin/python scripts/walk_forward.py --symbol ADAUSDT --symbol BTCUSDT \\
      --exchange binance --grid grid.json --spread-bps 5 --initial-balance 100 \\
      --min-trades 30 --workers 6 --out data/wf/report.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aegis.backtest.walk_forward import (  # noqa: E402
    HOLDOUT_NOTE,
    WalkForwardConfig,
    WalkForwardError,
    expand_grid,
    report_json,
    run_walk_forward,
)
from aegis.backtest.walk_forward_persist import persist_walk_forward  # noqa: E402
from aegis.config.runtime_public import settings_without_secrets  # noqa: E402
from aegis.config.settings import Settings, TradingMode, clear_settings_cache  # noqa: E402
from aegis.market_data.candle_store import CandleStore  # noqa: E402
from aegis.schemas.common import MarketType, Timeframe  # noqa: E402
from aegis.schemas.market import Candle, InstrumentRef  # noqa: E402

_POLICY_11_ARGS: tuple[tuple[str, str], ...] = (
    ("risk_position_pct", "--risk-position-pct"),
    ("risk_daily_loss_pct", "--risk-daily-loss-pct"),
    ("risk_weekly_loss_pct", "--risk-weekly-loss-pct"),
    ("risk_monthly_loss_pct", "--risk-monthly-loss-pct"),
    ("risk_max_drawdown_pct", "--risk-max-drawdown-pct"),
    ("risk_min_reward_risk", "--risk-min-reward-risk"),
)


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a decimal: {value!r}") from exc


def _date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aegis walk-forward rule tuning (paper only)")
    p.add_argument("--symbol", action="append", required=True, help="repeatable")
    p.add_argument("--exchange", required=True, help="stored candle exchange label")
    p.add_argument("--data-source", default=None, help="label, default '<exchange>-archive'")
    p.add_argument("--market-type", default=MarketType.SPOT.value,
                   choices=[m.value for m in MarketType])
    p.add_argument("--interval", default=Timeframe.M5.value, choices=[t.value for t in Timeframe])
    p.add_argument("--grid", required=True, type=Path,
                   help="JSON: list of param objects, or object of per-field lists")
    p.add_argument("--spread-bps", type=_decimal, required=True,
                   help="constant ASSUMED spread (historical spread not stored)")
    p.add_argument("--initial-balance", type=_decimal, required=True,
                   help="initial paper quote balance per window and symbol")
    p.add_argument("--min-trades", type=int, required=True,
                   help="min pooled R-trades in a train window for a point to be eligible")
    p.add_argument("--train-months", type=int, default=3)
    p.add_argument("--test-months", type=int, default=1)
    p.add_argument("--step-months", type=int, default=1)
    p.add_argument("--holdout-months", type=int, default=2)
    p.add_argument("--period-unit", choices=["months", "days"], default="months",
                   help="unit of the *-months values (days only for smoke tests)")
    p.add_argument("--window-candles", type=int, default=100)
    p.add_argument("--start", type=_date, default=None, help="optional ISO start (UTC)")
    p.add_argument("--end", type=_date, default=None, help="optional ISO end, exclusive (UTC)")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--out", type=Path, required=True, help="report JSON path")
    p.add_argument("--best-params-out", type=Path, default=None,
                   help="RuleParams JSON for AEGIS_RULE_PARAMS_FILE "
                   "(default <out>.best_params.json)")
    p.add_argument("--md-out", type=Path, default=None, help="optional markdown summary path")
    p.add_argument("--no-persist", action="store_true", help="skip backtest_runs persistence")
    p.add_argument("--policy", choices=["1.0", "1.1"], default="1.0",
                   help="risk policy: owner 1.0 snapshot, or 1.1 from the explicit CLI %% below")
    for _attr, flag in _POLICY_11_ARGS:
        p.add_argument(flag, type=_decimal, default=None, help="policy 1.1 only (%% / ratio)")
    p.add_argument("--risk-per-trade-pct", type=_decimal, default=None,
                   help="policy 1.1 optional")
    args = p.parse_args(argv)
    if args.initial_balance <= 0:
        p.error("--initial-balance must be positive")
    if args.spread_bps < 0:
        p.error("--spread-bps must be >= 0")
    if args.min_trades < 1:
        p.error("--min-trades must be >= 1")
    if args.workers < 1:
        p.error("--workers must be >= 1")
    for name in ("train_months", "test_months", "step_months", "holdout_months"):
        if getattr(args, name) < 1:
            p.error(f"--{name.replace('_', '-')} must be >= 1")
    if args.policy == "1.1":
        missing = [flag for attr, flag in _POLICY_11_ARGS if getattr(args, attr) is None]
        if missing:
            p.error(f"--policy 1.1 requires explicit {', '.join(missing)}")
    return args


def _settings(args: argparse.Namespace) -> Settings:
    clear_settings_cache()
    overrides: dict[str, Any] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
        "risk_policy_version": args.policy,
    }
    if args.policy == "1.1":
        # Explicit CLI values override env so the run is reproducible from its args.
        overrides["paper_initial_balance"] = args.initial_balance
        for attr, _flag in _POLICY_11_ARGS:
            overrides[attr] = getattr(args, attr)
        overrides["risk_per_trade_pct"] = args.risk_per_trade_pct
    return Settings(**overrides)


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    """Compact markdown tables from the JSON report (no interpretation)."""
    lines: list[str] = []
    cfg = report["config"]
    lines.append(f"Run `{report['run_id']}` ({report['software_version']}), "
                 f"created {report['created_at']}.")
    lines.append("")
    lines.append("| Setting | Value |")
    lines.append("| --- | --- |")
    for k in ("unit", "train", "test", "step", "holdout", "min_trades", "spread_bps",
              "initial_balance", "fee_bps", "slippage_bps", "risk_policy_version",
              "timeframe", "window_candles"):
        lines.append(f"| {k} | {cfg[k]} |")
    lines.append(f"| grid size | {report['grid']['size']} |")
    lines.append(f"| total selection trials (grid x folds) | "
                 f"{report['trials']['total_selection_trials']} |")
    lines.append("")
    lines.append("### Data")
    lines.append("")
    lines.append("| Symbol | Source | Bars | First open | Last close | Data hash (16) |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for sym, d in report["data"].items():
        lines.append(f"| {sym} | {d['data_source']} ({d['exchange_label']}) | {d['bars']} | "
                     f"{d['first_open']} | {d['last_close']} | `{d['data_hash'][:16]}` |")
    lines.append("")
    lines.append("### Folds (selection on train only)")
    lines.append("")
    lines.append("| Fold | Train | Test | Eligible | Selected hash | Train expR (n) | "
                 "Test expR (n) | Test rank pct |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for f in report["folds"]:
        sel = f["selected"]
        tr = f"{f['train']['start'][:10]}..{f['train']['end'][:10]}"
        te = f"{f['test']['start'][:10]}..{f['test']['end'][:10]}"
        if sel is None:
            lines.append(f"| {f['index']} | {tr} | {te} | {f['eligible_points']} | none (flat) "
                         f"| max n={f['max_train_r_trades']} | - | - |")
        else:
            lines.append(
                f"| {f['index']} | {tr} | {te} | {f['eligible_points']} | "
                f"`{sel['param_hash'][:10]}` | {_fmt(sel['train_pooled_expectancy_r'])} "
                f"({sel['train_pooled_r_trades']}) | {_fmt(sel['test_pooled_expectancy_r'])} "
                f"({sel['test_pooled_r_trades']}) | {_fmt(sel['test_rank_pct_among_grid'], 2)} |"
            )
    lines.append("")
    lines.append("### Selected params per fold")
    lines.append("")
    keys = [k for k in (report["grid"]["points"][0].keys())]
    lines.append("| Fold | " + " | ".join(keys) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in keys) + " |")
    for f in report["folds"]:
        sel = f["selected"]
        vals = ["-"] * len(keys) if sel is None else [sel["params"][k] for k in keys]
        lines.append(f"| {f['index']} | " + " | ".join(vals) + " |")
    lines.append("")
    lines.append("### Stitched OOS (fold test windows, selected params)")
    lines.append("")
    lines.append("| Scope | R-trades | expectancy_r | avg_r 95% CI (normal) | win rate | "
                 "net PnL | compounded return | max DD |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for sym, s in report["oos_stitched"]["per_symbol"].items():
        ci = s["avg_r_ci95_normal"]
        ci_s = "n/a" if ci is None else f"[{ci[0]:.3f}, {ci[1]:.3f}]"
        lines.append(f"| {sym} | {s['r_trade_count']} | {_fmt(s['expectancy_r'])} | {ci_s} | "
                     f"{_fmt(s['win_rate'], 3)} | {_fmt(s['net_pnl'])} | "
                     f"{_fmt(s['compounded_return'])} | {_fmt(s['max_drawdown'])} |")
    pooled = report["oos_stitched"]["pooled"]
    ci = pooled["avg_r_ci95_normal"]
    ci_s = "n/a" if ci is None else f"[{ci[0]:.3f}, {ci[1]:.3f}]"
    lines.append(f"| pooled | {pooled['r_trade_count']} | {_fmt(pooled['expectancy_r'])} | "
                 f"{ci_s} | - | {_fmt(pooled['net_pnl'])} | - | - |")
    lines.append("")
    dsr = report["dsr"]
    lines.append("### Deflated Sharpe Ratio")
    lines.append("")
    lines.append("| Item | Value |")
    lines.append("| --- | --- |")
    for k in ("sr_oos_per_trade", "t_obs", "skew", "kurtosis", "sr_variance_across_trials",
              "n_trials_total", "sr0_expected_max", "dsr", "n_trials_grid",
              "sr0_expected_max_n_grid", "dsr_n_grid", "psr_vs_zero"):
        lines.append(f"| {k} | {_fmt(dsr.get(k))} |")
    lines.append("")
    st = report["stability"]
    lines.append("### Stability")
    lines.append("")
    lines.append(f"- folds with a selection: {st['folds_with_selection']}/{st['folds']}; "
                 f"distinct selected points: {st['distinct_selected']}; modal point chosen "
                 f"{st['modal_count']} times.")
    below = _fmt(st["share_folds_selected_below_test_median"], 2)
    lines.append(f"- test rank pct median: {_fmt(st['test_rank_pct_median'], 2)}; share of "
                 f"folds below test median: {below}.")
    lines.append("")
    ho = report["holdout"]
    lines.append("### Holdout (evaluated once)")
    lines.append("")
    lines.append(f"Window {ho['window']['start']} .. {ho['window']['end']}; params from fold "
                 f"{ho['params_from_fold']}.")
    lines.append("")
    if ho.get("evaluated"):
        lines.append("| Symbol | R-trades | expectancy_r | win rate | net return | max DD |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for sym, m in ho["per_symbol"].items():
            lines.append(f"| {sym} | {m['r_trade_count']} | {_fmt(m['expectancy_r'])} | "
                         f"{_fmt(m['win_rate'], 3)} | {_fmt(m['net_return_after_costs'])} | "
                         f"{_fmt(m['max_drawdown'])} |")
        pm = ho["pooled"]
        ci = pm["avg_r_ci95_normal"]
        ci_s = "n/a" if ci is None else f"[{ci[0]:.3f}, {ci[1]:.3f}]"
        lines.append(f"| pooled | {pm['r_trade_count']} | {_fmt(pm['expectancy_r'])} "
                     f"(avg R CI {ci_s}) | - | - | - |")
    else:
        lines.append(f"Not evaluated: {ho.get('reason')}")
    lines.append("")
    lines.append(f"> {HOLDOUT_NOTE}")
    lines.append("")
    return "\n".join(lines)


def _load(store: CandleStore, args: argparse.Namespace) -> dict[str, list[Candle]]:
    out: dict[str, list[Candle]] = {}
    interval = Timeframe(args.interval)
    for symbol in args.symbol:
        inst = InstrumentRef(
            exchange=args.exchange, market_type=MarketType(args.market_type), symbol=symbol
        )
        bounds = store.time_bounds(inst, interval)
        if bounds is None:
            raise WalkForwardError(f"no stored candles for {args.exchange}:{symbol}")
        start = args.start or bounds[0]
        end = args.end or (bounds[1] + timedelta(minutes=1))
        candles = store.load_range(inst, interval, start, end)
        if store.last_error or not candles:
            raise WalkForwardError(f"load failed for {symbol}: {store.last_error or 'empty'}")
        out[symbol] = candles
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = _settings(args)
    store = CandleStore(settings.database_url)
    if not store.enabled:
        print(f"candle store disabled ({store.last_error or 'no database url'})")
        return 2
    try:
        spec = json.loads(args.grid.read_text(encoding="utf-8"))
        grid, skipped = expand_grid(spec)
    except (OSError, ValueError) as exc:
        print(f"grid error: {exc}")
        return 2
    t0 = time.monotonic()
    try:
        candles = _load(store, args)
    except WalkForwardError as exc:
        print(str(exc))
        return 1
    print(f"loaded {', '.join(f'{s}={len(c)}' for s, c in candles.items())} "
          f"in {time.monotonic() - t0:.1f}s; grid={len(grid)} skipped={len(skipped)}")

    config = WalkForwardConfig(
        train=args.train_months,
        test=args.test_months,
        step=args.step_months,
        holdout=args.holdout_months,
        min_trades=args.min_trades,
        spread_bps=args.spread_bps,
        initial_balance=args.initial_balance,
        unit=args.period_unit,
        window_candles=args.window_candles,
        timeframe=Timeframe(args.interval),
        market_type=MarketType(args.market_type),
        data_source=args.data_source or f"{args.exchange}-archive",
    )
    public = settings_without_secrets(settings)
    progress: Callable[[str], None] = lambda msg: print(  # noqa: E731
        f"[{time.monotonic() - t0:7.1f}s] {msg}", flush=True
    )
    t1 = time.monotonic()
    try:
        if args.workers > 1:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                result = run_walk_forward(
                    candles, grid, config, settings=public, executor=pool,
                    grid_skipped=skipped, progress=progress,
                )
        else:
            result = run_walk_forward(
                candles, grid, config, settings=public, grid_skipped=skipped, progress=progress
            )
    except WalkForwardError as exc:
        print(f"walk-forward error: {exc}")
        return 1
    elapsed = time.monotonic() - t1
    result.report["runtime"] = {"seconds": round(elapsed, 1), "workers": args.workers}

    if args.no_persist:
        result.report["persistence"] = {"ok": False, "error": "disabled", "rows": 0}
    else:
        result.report["persistence"] = persist_walk_forward(settings.database_url, result)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report_json(result.report), encoding="utf-8")
    print(f"report: {args.out}")
    best_path = args.best_params_out or args.out.with_suffix(".best_params.json")
    if result.best_params is not None:
        best_path.write_text(json.dumps(result.best_params, indent=2, default=str),
                             encoding="utf-8")
        print(f"best params (AEGIS_RULE_PARAMS_FILE format): {best_path}")
    else:
        print("no final selection: best params file not written")
    if args.md_out is not None:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(render_markdown(result.report), encoding="utf-8")
        print(f"markdown: {args.md_out}")

    pooled = result.report["oos_stitched"]["pooled"]
    print(f"elapsed={elapsed:.1f}s persistence={result.report['persistence']}")
    print(f"stitched OOS pooled: r_trades={pooled['r_trade_count']} "
          f"expectancy_r={pooled['expectancy_r']} avg_r_ci95={pooled['avg_r_ci95_normal']}")
    print(f"DSR={result.report['dsr'].get('dsr')} (N={result.report['dsr']['n_trials_total']})")
    print(f"NOTE: {HOLDOUT_NOTE}")
    print(f"disclaimer: {result.report['disclaimer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
