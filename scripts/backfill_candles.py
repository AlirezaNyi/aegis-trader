#!/usr/bin/env python3
"""Backfill finalized public Toobit klines into Postgres `market_candles`.

Public REST only (no API keys). Idempotent: existing (exchange, market_type,
symbol, interval, open_time) rows are left untouched. Prints counts only.

Usage (from repo root, after `alembic upgrade head`):
  .venv/bin/python scripts/backfill_candles.py --symbol ADAUSDT --interval 5m --days 7
  .venv/bin/python scripts/backfill_candles.py --symbol BTC-SWAP-USDT \\
      --market-type futures --interval 15m --days 30
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aegis.config.settings import Settings  # noqa: E402
from aegis.market_data.backfill import backfill_instrument  # noqa: E402
from aegis.market_data.candle_store import CandleStore  # noqa: E402
from aegis.market_data.rest_client import ToobitRestMarketDataClient  # noqa: E402
from aegis.schemas.common import MarketType, Timeframe  # noqa: E402
from aegis.schemas.market import InstrumentRef  # noqa: E402


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--symbol", action="append", required=True, help="repeatable")
    parser.add_argument(
        "--interval", required=True, choices=[t.value for t in Timeframe]
    )
    parser.add_argument("--days", type=float, required=True)
    parser.add_argument(
        "--market-type",
        default=MarketType.SPOT.value,
        choices=[m.value for m in MarketType],
    )
    parser.add_argument(
        "--delay", type=float, default=0.25, help="seconds between requests"
    )
    args = parser.parse_args(argv)
    if args.days <= 0:
        parser.error("--days must be positive")
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = Settings(require_database=False)
    store = CandleStore(settings.database_url)
    if not store.enabled:
        print(f"candle store disabled ({store.last_error or 'no database url'})")
        return 2

    interval = Timeframe(args.interval)
    market_type = MarketType(args.market_type)
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=args.days)
    exit_code = 0
    with ToobitRestMarketDataClient() as client:
        for symbol in args.symbol:
            instrument = InstrumentRef(market_type=market_type, symbol=symbol)
            result = backfill_instrument(
                client,
                store,
                instrument,
                interval,
                start=start,
                end=end,
                delay_seconds=args.delay,
            )
            print(
                f"{result.symbol} {result.interval} windows={result.windows} "
                f"empty={result.empty_windows} fetched={result.fetched} "
                f"final={result.final} inserted={result.inserted} "
                f"errors={len(result.errors)}"
            )
            if result.errors or store.last_error:
                exit_code = 1
    if store.last_error:
        print(f"store last_error={store.last_error}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
