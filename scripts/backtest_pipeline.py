#!/usr/bin/env python3
"""Backtest the full paper decision pipeline on stored candles (paper only).

Loads finalized candles from Postgres `market_candles` (see backfill_candles.py) and
replays each final bar through features -> analysts -> evidence -> Jev -> Supervisor
-> Risk -> PaperBroker. Never constructs an exchange order client.

Money/spread values are required arguments (no invented defaults). By default the
LLM and Jev ports are Unavailable, so every bar is NO_TRADE (zero trades): this
exercises validation/risk plumbing only. `--use-live-llm` calls the owner-configured
LLM provider and requires `--max-llm-calls` (whole-run cap). `--decision-source rules`
with `--rule-params-file` runs the deterministic rule strategy (no LLM/Jev). Research
archive candles (e.g. `--exchange binance`) keep their exchange label; Risk's
allowlist is by symbol and the report records `data_source`.

Usage:
  .venv/bin/python scripts/backtest_pipeline.py --symbol ADAUSDT --interval 5m \\
      --days 7 --spread-bps 5 --initial-balance 100
  .venv/bin/python scripts/backtest_pipeline.py --symbol ADAUSDT --interval 5m \\
      --days 30 --exchange binance --decision-source rules \\
      --rule-params-file data/walk_forward/best.json --spread-bps 5 --initial-balance 100
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aegis.backtest.pipeline_replay import CappedLlmPort, replay_pipeline  # noqa: E402
from aegis.config.runtime_public import settings_without_secrets  # noqa: E402
from aegis.config.settings import Settings, TradingMode, clear_settings_cache  # noqa: E402
from aegis.interfaces.jev import UnavailableJevPort  # noqa: E402
from aegis.interfaces.llm import LlmPort, UnavailableLlmPort  # noqa: E402
from aegis.market_data.candle_store import CandleStore  # noqa: E402
from aegis.pipeline.decide import RuleParamsUnavailable, load_rule_params  # noqa: E402
from aegis.risk.factory import build_risk_policy_from_settings  # noqa: E402
from aegis.schemas.backtest import SplitWindows, WindowMetrics  # noqa: E402
from aegis.schemas.common import MarketType, Timeframe  # noqa: E402
from aegis.schemas.market import InstrumentRef  # noqa: E402
from aegis.strategy.rules import RuleParams  # noqa: E402
from aegis.supervisor.factory import (  # noqa: E402
    budget_config_from_settings,
    build_llm_port,
)


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a decimal: {value!r}") from exc


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aegis full-pipeline backtest (paper only)")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", required=True, choices=[t.value for t in Timeframe])
    parser.add_argument("--days", type=float, required=True)
    parser.add_argument(
        "--market-type",
        default=MarketType.SPOT.value,
        choices=[m.value for m in MarketType],
    )
    parser.add_argument(
        "--spread-bps",
        type=_decimal,
        required=True,
        help="constant assumed spread in bps (historical spread is not stored)",
    )
    parser.add_argument(
        "--initial-balance",
        type=_decimal,
        required=True,
        help="initial paper quote balance per split window",
    )
    parser.add_argument("--window-candles", type=int, default=100)
    parser.add_argument(
        "--train-frac", type=float, default=0.6, help="time fraction for train window"
    )
    parser.add_argument(
        "--validation-frac",
        type=float,
        default=0.2,
        help="time fraction for validation window (remainder is OOS)",
    )
    parser.add_argument(
        "--use-live-llm",
        action="store_true",
        help="call the owner-configured LLM provider (costs money); needs --max-llm-calls",
    )
    parser.add_argument("--max-llm-calls", type=int, default=None)
    parser.add_argument(
        "--exchange", default="toobit", help="stored candle exchange label (e.g. binance)"
    )
    parser.add_argument(
        "--data-source", default=None, help="report label, default '<exchange>-archive'"
    )
    parser.add_argument(
        "--decision-source", default="llm", choices=["llm", "rules", "rules+llm"]
    )
    parser.add_argument(
        "--rule-params-file", default=None, help="RuleParams JSON (rules / rules+llm)"
    )
    args = parser.parse_args(argv)
    if args.decision_source != "llm" and not args.rule_params_file:
        parser.error("--decision-source rules/rules+llm requires --rule-params-file")
    if args.decision_source == "rules+llm" and not args.use_live_llm:
        parser.error("--decision-source rules+llm requires --use-live-llm")
    if args.days <= 0:
        parser.error("--days must be positive")
    if args.initial_balance <= 0:
        parser.error("--initial-balance must be positive")
    if args.spread_bps < 0:
        parser.error("--spread-bps must be >= 0")
    if args.window_candles < 1:
        parser.error("--window-candles must be >= 1")
    if not (0 < args.train_frac < 1 and 0 < args.validation_frac < 1):
        parser.error("split fractions must be in (0, 1)")
    if args.train_frac + args.validation_frac >= 1:
        parser.error("--train-frac + --validation-frac must be < 1 (OOS needs data)")
    if args.use_live_llm and (args.max_llm_calls is None or args.max_llm_calls <= 0):
        parser.error("--use-live-llm requires a positive --max-llm-calls")
    return args


def _splits(start: datetime, end: datetime, train: float, val: float) -> SplitWindows:
    span = end - start
    train_end = start + span * train
    val_end = train_end + span * val
    eps = timedelta(microseconds=1)
    return SplitWindows(
        train_start=start,
        train_end=train_end,
        validation_start=train_end + eps,
        validation_end=val_end,
        oos_start=val_end + eps,
        oos_end=end,
    )


def _line(m: WindowMetrics) -> str:
    return (
        f"{m.window:<10} trades={m.trade_count} net_return={m.net_return_after_costs:.6f} "
        f"max_dd={m.max_drawdown:.6f} win_rate={m.win_rate} pf={m.profit_factor} "
        f"exposure={m.exposure:.4f} turnover={m.turnover} cost={m.cost_contribution}"
    )


async def _main(args: argparse.Namespace) -> int:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
    )
    store = CandleStore(settings.database_url)
    if not store.enabled:
        print(f"candle store disabled ({store.last_error or 'no database url'})")
        return 2

    instrument = InstrumentRef(
        exchange=args.exchange, market_type=MarketType(args.market_type), symbol=args.symbol
    )
    rule_params: RuleParams | None = None
    if args.decision_source != "llm":
        try:
            rule_params = load_rule_params(args.rule_params_file)
        except RuleParamsUnavailable as exc:
            print(f"rule params unavailable: {exc}")
            return 2
    interval = Timeframe(args.interval)
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=args.days)
    candles = store.load_range(instrument, interval, start, end)
    if store.last_error:
        print(f"store last_error={store.last_error}")
        return 1
    if not candles:
        print("no stored candles for range; run scripts/backfill_candles.py first")
        return 1

    llm_port: LlmPort = UnavailableLlmPort()
    if args.use_live_llm:
        inner = build_llm_port(settings)
        if isinstance(inner, UnavailableLlmPort):
            print("LLM provider not configured/budgeted; refusing --use-live-llm")
            return 2
        llm_port = CappedLlmPort(inner, max_calls=args.max_llm_calls)

    splits = _splits(
        candles[0].close_time, candles[-1].close_time, args.train_frac, args.validation_frac
    )
    report = await replay_pipeline(
        candles,
        settings=settings_without_secrets(settings),
        risk_policy=build_risk_policy_from_settings(settings),
        instrument=instrument,
        timeframe=interval,
        splits=splits,
        jev_port=UnavailableJevPort(),
        llm_port=llm_port,
        supervisor_budgets=budget_config_from_settings(settings),
        spread_bps=args.spread_bps,
        initial_quote_balance=args.initial_balance,
        window_candles=args.window_candles,
        dataset_version=(
            f"market_candles:{instrument.exchange}:{instrument.symbol}:{interval.value}:"
            f"{candles[0].open_time.isoformat()}..{candles[-1].open_time.isoformat()}"
        ),
        decision_source=args.decision_source,
        rule_params=rule_params,
        data_source=None
        if args.exchange == "toobit"
        else (args.data_source or f"{args.exchange}-archive"),
    )
    print(f"candles={len(candles)} counters={report.assumptions.get('counters')}")
    if isinstance(llm_port, CappedLlmPort):
        print(f"llm_calls_used={llm_port.used}/{llm_port.max_calls}")
    for m in (report.train_metrics, report.validation_metrics, report.oos_metrics):
        print(_line(m))
    print(f"uncertainty: {report.uncertainty_notes}")
    print(f"disclaimer: {report.disclaimer}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
