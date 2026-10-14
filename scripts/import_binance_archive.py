#!/usr/bin/env python3
"""Import Binance public spot kline archives into `market_candles` (RESEARCH ONLY).

Rows are stored with exchange="binance", source="binance-archive" and never as
Toobit data. Public downloads from https://data.binance.vision (no credentials);
each zip is SHA-256 verified against its `.CHECKSUM` before parsing or caching.
Idempotent: existing (exchange, market_type, symbol, interval, open_time) rows are
left untouched. Prints counts only.

Usage (from repo root, after `alembic upgrade head`):
  .venv/bin/python scripts/import_binance_archive.py --symbol ADAUSDT --months 13
  .venv/bin/python scripts/import_binance_archive.py --symbol BTCUSDT --symbol ETHUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aegis.config.settings import Settings  # noqa: E402
from aegis.market_data.binance_archive import (  # noqa: E402
    MAX_UPSERT_BATCH,
    ArchiveFetcher,
    ImportResult,
    binance_instrument,
    import_symbol,
    validate_symbol,
)
from aegis.market_data.candle_store import CandleStore  # noqa: E402
from aegis.schemas.common import Timeframe  # noqa: E402

DEFAULT_CACHE_DIR = _ROOT / "data" / "binance_cache"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--symbol", action="append", required=True, help="repeatable")
    parser.add_argument(
        "--interval", default=Timeframe.M5.value, choices=[t.value for t in Timeframe]
    )
    parser.add_argument(
        "--months", type=int, default=13, help="complete months before the current one"
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument(
        "--delay", type=float, default=0.05, help="seconds between downloads"
    )
    args = parser.parse_args(argv)
    if args.months < 0:
        parser.error("--months must be >= 0")
    if not 1 <= args.batch_size <= MAX_UPSERT_BATCH:
        parser.error(f"--batch-size must be 1..{MAX_UPSERT_BATCH}")
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    try:
        args.symbol = [validate_symbol(s.strip().upper()) for s in args.symbol]
    except ValueError as exc:
        parser.error(str(exc))
    return args


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "-"


def _line(result: ImportResult) -> str:
    gaps = result.gaps
    return (
        f"{result.symbol} {result.interval} files={result.files} "
        f"checksum_ok={result.checksum_ok} checksum_failed={result.checksum_failed} "
        f"missing={result.missing} cached={result.from_cache} rows={result.rows} "
        f"header_rows={result.header_rows} malformed={result.malformed} "
        f"not_final={result.not_final} final={result.final} inserted={result.inserted} "
        f"bars={gaps.bars if gaps else 0} gaps={gaps.missing_bars if gaps else 0} "
        f"gap_runs={gaps.gap_runs if gaps else 0} "
        f"largest_gap_bars={gaps.largest_gap_bars if gaps else 0} "
        f"missing_vs_window={result.missing_vs_window} "
        f"window={_iso(result.window_start)}..{_iso(result.window_end)} "
        f"first={_iso(gaps.first_open if gaps else None)} "
        f"last={_iso(gaps.last_open if gaps else None)} errors={len(result.errors)}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = Settings(require_database=False)
    store = CandleStore(settings.database_url)
    if not store.enabled:
        print(f"candle store disabled ({store.last_error or 'no database url'})")
        return 2
    interval = Timeframe(args.interval)
    # Preflight: fail before downloading anything if Postgres is unreachable.
    store.time_bounds(binance_instrument(args.symbol[0]), interval)
    if store.last_error:
        print(f"store last_error={store.last_error}")
        return 2

    exit_code = asyncio.run(_run(args, store, interval))
    if store.last_error:
        print(f"store last_error={store.last_error}")
    return exit_code


async def _run(args: argparse.Namespace, store: CandleStore, interval: Timeframe) -> int:
    now = datetime.now(tz=UTC)
    exit_code = 0
    async with ArchiveFetcher(cache_dir=args.cache_dir, delay_seconds=args.delay) as fetcher:
        for symbol in args.symbol:
            result = await import_symbol(
                fetcher,
                store,
                symbol,
                interval,
                now=now,
                months=args.months,
                batch_size=args.batch_size,
            )
            print(_line(result), flush=True)
            for error in result.errors:
                print(f"  error {error}")
            if result.errors or store.last_error:
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
