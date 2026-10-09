#!/usr/bin/env python3
"""CLI paper soak: poll public Toobit klines and run paper cycles.

No live arming. No signed trade client. Uses NullExecutionPort path via PaperBroker.

Usage (from repo root):
  AEGIS_PAPER_SOAK_ENABLED=true .venv/bin/python scripts/paper_soak.py
  .venv/bin/python scripts/paper_soak.py --once
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aegis.config.settings import Settings, TradingMode, clear_settings_cache  # noqa: E402
from aegis.jev.factory import build_jev_port  # noqa: E402
from aegis.market_data.gateway import MarketDataGateway  # noqa: E402
from aegis.market_data.rest_client import ToobitRestMarketDataClient  # noqa: E402
from aegis.ops.shutdown import ShutdownGate  # noqa: E402
from aegis.paper.broker import PaperBroker  # noqa: E402
from aegis.pipeline.cycle import PaperCycleDeps  # noqa: E402
from aegis.pipeline.review import format_review_text, summarize_soak_outcome  # noqa: E402
from aegis.pipeline.soak import (  # noqa: E402
    PaperSoakRunner,
    run_soak_loop,
    soak_instruments_from_settings,
    soak_timeframe_from_settings,
)
from aegis.risk.factory import build_risk_policy_from_settings  # noqa: E402
from aegis.supervisor.factory import budget_config_from_settings, build_llm_port  # noqa: E402


async def _main(once: bool) -> int:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
        paper_soak_enabled=True,
    )
    deps = PaperCycleDeps(
        settings=settings,
        risk_policy=build_risk_policy_from_settings(settings),
        jev_port=build_jev_port(settings),
        llm_port=build_llm_port(settings),
        supervisor_budgets=budget_config_from_settings(settings),
        paper_broker=PaperBroker(settings),
        analyst_concurrency=settings.analyst_concurrency,
    )
    rest = ToobitRestMarketDataClient()
    try:
        runner = PaperSoakRunner(
            market_data=MarketDataGateway(rest),
            deps=deps,
            instruments=soak_instruments_from_settings(settings),
            timeframe=soak_timeframe_from_settings(settings),
        )
        if once:
            outcome = await runner.poll_once()
            print(format_review_text(summarize_soak_outcome(outcome)))
            return 0
        gate = ShutdownGate()
        await run_soak_loop(
            runner,
            poll_seconds=settings.paper_soak_poll_seconds,
            shutdown=gate,
        )
    finally:
        rest.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aegis paper soak (public MD only)")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Single poll then exit (default: loop until interrupted)",
    )
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_main(once=args.once)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
