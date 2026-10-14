#!/usr/bin/env python3
"""Compare stored Toobit vs Binance-archive closes over their overlap (RESEARCH ONLY).

Reads `market_candles` only (no network). For each symbol: matched bars, absolute
close difference in bps ((toobit - binance) / binance), and Pearson correlation of
1-bar close-to-close returns on consecutive matched bars. Prints numbers only.

Usage (from repo root):
  .venv/bin/python scripts/compare_binance_toobit.py
  .venv/bin/python scripts/compare_binance_toobit.py --symbol ADAUSDT --interval 5m
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aegis.config.settings import Settings  # noqa: E402
from aegis.market_data.binance_archive import binance_instrument, validate_symbol  # noqa: E402
from aegis.market_data.candle_store import CandleStore  # noqa: E402
from aegis.market_data.cross_venue import CloseBasisReport, compare_closes  # noqa: E402
from aegis.market_data.timeutil import interval_to_milliseconds  # noqa: E402
from aegis.schemas.common import MarketType, Timeframe  # noqa: E402
from aegis.schemas.market import InstrumentRef  # noqa: E402

DEFAULT_SYMBOLS = ("ADAUSDT", "BTCUSDT", "BNBUSDT", "TRXUSDT", "ETHUSDT")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--symbol", action="append", help="repeatable; default: 5 symbols")
    parser.add_argument(
        "--interval", default=Timeframe.M5.value, choices=[t.value for t in Timeframe]
    )
    args = parser.parse_args(argv)
    symbols = args.symbol or list(DEFAULT_SYMBOLS)
    try:
        args.symbol = [validate_symbol(s.strip().upper()) for s in symbols]
    except ValueError as exc:
        parser.error(str(exc))
    return args


def _num(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _line(symbol: str, interval: str, report: CloseBasisReport) -> str:
    first = report.first_open.isoformat() if report.first_open else "-"
    last = report.last_open.isoformat() if report.last_open else "-"
    return (
        f"{symbol} {interval} overlap={first}..{last} toobit_bars={report.primary_bars} "
        f"binance_bars={report.reference_bars} matched={report.matched} "
        f"abs_bps_mean={_num(report.mean_abs_bps)} "
        f"abs_bps_median={_num(report.median_abs_bps)} "
        f"abs_bps_p95={_num(report.p95_abs_bps)} abs_bps_max={_num(report.max_abs_bps)} "
        f"signed_bps_mean={_num(report.mean_signed_bps)} "
        f"return_pairs={report.return_pairs} "
        f"return_corr={_num(report.return_correlation, 5)}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = Settings(require_database=False)
    store = CandleStore(settings.database_url)
    if not store.enabled:
        print(f"candle store disabled ({store.last_error or 'no database url'})")
        return 2
    interval = Timeframe(args.interval)
    step = timedelta(milliseconds=interval_to_milliseconds(interval.value))
    exit_code = 0
    for symbol in args.symbol:
        toobit = InstrumentRef(market_type=MarketType.SPOT, symbol=symbol)
        binance = binance_instrument(symbol)
        toobit_bounds = store.time_bounds(toobit, interval)
        binance_bounds = store.time_bounds(binance, interval)
        if store.last_error:
            print(f"store last_error={store.last_error}")
            return 1
        if toobit_bounds is None or binance_bounds is None:
            print(
                f"{symbol} {interval.value} toobit_has_rows={int(toobit_bounds is not None)} "
                f"binance_has_rows={int(binance_bounds is not None)} matched=0"
            )
            exit_code = 1
            continue
        start = max(toobit_bounds[0], binance_bounds[0])
        end = min(toobit_bounds[1], binance_bounds[1]) + step
        if end <= start:
            print(f"{symbol} {interval.value} matched=0")
            exit_code = 1
            continue
        report = compare_closes(
            store.load_range(toobit, interval, start, end),
            store.load_range(binance, interval, start, end),
            interval,
        )
        print(_line(symbol, interval.value, report))
    if store.last_error:
        print(f"store last_error={store.last_error}")
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
