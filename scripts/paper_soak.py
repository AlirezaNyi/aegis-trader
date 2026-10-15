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

from aegis.config.runtime_public import settings_without_secrets  # noqa: E402
from aegis.config.settings import Settings, TradingMode, clear_settings_cache  # noqa: E402
from aegis.jev.factory import build_jev_port  # noqa: E402
from aegis.market_data.candle_store import CandleStore  # noqa: E402
from aegis.market_data.gateway import MarketDataGateway  # noqa: E402
from aegis.market_data.rest_client import ToobitRestMarketDataClient  # noqa: E402
from aegis.ops.shutdown import ShutdownGate  # noqa: E402
from aegis.paper.broker import PaperBroker  # noqa: E402
from aegis.pipeline.cycle import PaperCycleDeps  # noqa: E402
from aegis.pipeline.decide import (  # noqa: E402
    AnyRuleParams,
    RuleParamsUnavailable,
    load_rule_params,
)
from aegis.pipeline.review import format_review_text, summarize_soak_outcome  # noqa: E402
from aegis.pipeline.soak import (  # noqa: E402
    PaperSoakRunner,
    run_soak_loop,
    soak_instruments_from_settings,
    soak_timeframe_from_settings,
)
from aegis.risk.factory import build_risk_policy_from_settings  # noqa: E402
from aegis.supervisor.factory import (  # noqa: E402
    budget_config_from_settings,
    build_call_limiter,
    build_llm_port,
)


def _jev_line(outcome: object) -> str:
    """One-line Jev view for operators: status, model, and headline answers."""
    result = getattr(outcome, "result", None)
    proposal = None if result is None else result.proposal
    if proposal is None:
        return "JEV not_called"
    jev = proposal.jev_result
    answers = jev.answers
    alignment = answers.get("alignment", {})
    adequacy = answers.get("evidence_adequacy", {})
    entry = answers.get("long_entry_supported", {})
    return (
        f"JEV status={jev.status.value} model={jev.model} "
        f"alignment={alignment.get('choice')} "
        f"adequacy={adequacy.get('score')} "
        f"long_entry_p={entry.get('noul')} latency_ms={jev.latency_ms}"
    )


def _rule_params(settings: Settings) -> AnyRuleParams | None:
    if settings.decision_source == "llm":
        return None
    try:
        return load_rule_params(settings.rule_params_file)
    except RuleParamsUnavailable as exc:
        print(f"rule params unavailable ({exc}); every cycle will be NO_TRADE")
        return None


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
        # Ports above are built from full Settings; deps keep a secret-free copy.
        settings=settings_without_secrets(settings),
        risk_policy=build_risk_policy_from_settings(settings),
        jev_port=build_jev_port(settings),
        llm_port=build_llm_port(settings),
        supervisor_budgets=budget_config_from_settings(settings),
        paper_broker=PaperBroker(
            settings_without_secrets(settings),
            initial_quote_balance=settings.paper_initial_balance,
            fee_bps=settings.effective_fee_bps,
        ),
        analyst_concurrency=settings.analyst_concurrency,
        llm_call_limiter=build_call_limiter(settings),
        decision_source=settings.decision_source,
        rule_params=_rule_params(settings),
    )
    rest = ToobitRestMarketDataClient()
    try:
        runner = PaperSoakRunner(
            market_data=MarketDataGateway(rest),
            deps=deps,
            instruments=soak_instruments_from_settings(settings),
            timeframe=soak_timeframe_from_settings(settings),
            candle_store=CandleStore(database_url=settings.database_url),
        )
        if once:
            await runner.poll_once()
            for symbol, outcome in runner.last_outcomes_by_symbol.items():
                print(f"===== {symbol} =====")
                print(format_review_text(summarize_soak_outcome(outcome)))
                print(_jev_line(outcome))
            return 0
        gate = ShutdownGate()
        await run_soak_loop(
            runner,
            poll_seconds=settings.paper_soak_poll_seconds,
            shutdown=gate,
        )
    finally:
        await rest.aclose()
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
