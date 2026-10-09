"""Paper soak runner — REST poll for new finalized bars, then one paper cycle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from aegis.config.settings import Settings, TradingMode
from aegis.interfaces.market_data import MarketDataPort
from aegis.logging import get_logger
from aegis.ops.shutdown import ShutdownGate
from aegis.pipeline.cycle import PaperCycleDeps, PaperCycleResult, run_paper_cycle
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef

logger = get_logger("aegis.pipeline.soak")


class CandleFetcher(Protocol):
    def get_candles(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
    ) -> list[Candle] | tuple[Candle, ...]:
        """Return candles newest-last or unordered; runner sorts by open_time."""


@dataclass
class SoakPollOutcome:
    ran_cycle: bool
    skipped_reason: str | None = None
    result: PaperCycleResult | None = None
    final_open_time: datetime | None = None


@dataclass
class PaperSoakRunner:
    """Poll public market data; invoke ``run_paper_cycle`` once per new final bar."""

    market_data: MarketDataPort | CandleFetcher
    deps: PaperCycleDeps
    instrument: InstrumentRef
    timeframe: Timeframe
    candle_limit: int = 100
    _last_final_open: datetime | None = field(default=None, init=False)
    cycles_run: int = field(default=0, init=False)

    async def poll_once(self, now: datetime | None = None) -> SoakPollOutcome:
        """Fetch candles; run at most one paper cycle for a new finalized bar."""
        clock = now if now is not None else datetime.now(tz=UTC)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=UTC)

        if self.deps.settings.trading_mode == TradingMode.LIVE:
            return SoakPollOutcome(ran_cycle=False, skipped_reason="trading_mode_live")
        if self.deps.settings.kill_switch:
            return SoakPollOutcome(ran_cycle=False, skipped_reason="kill_switch")
        if self.deps.settings.trading_mode not in {
            TradingMode.PAPER,
            TradingMode.DEVELOPMENT,
        }:
            return SoakPollOutcome(
                ran_cycle=False,
                skipped_reason=f"unsupported_mode:{self.deps.settings.trading_mode.value}",
            )

        try:
            raw = self.market_data.get_candles(
                self.instrument,
                self.timeframe,
                limit=self.candle_limit,
            )
            candles = sorted(list(raw), key=lambda c: c.open_time)
        except Exception as exc:  # noqa: BLE001 — network/parse: skip, no order retry
            logger.warning(
                "paper_soak_fetch_failed err=%s",
                type(exc).__name__,
                extra={"event": "paper_soak_fetch_failed", "component": "soak"},
            )
            return SoakPollOutcome(
                ran_cycle=False,
                skipped_reason=f"fetch_error:{type(exc).__name__}",
            )

        if not candles:
            return SoakPollOutcome(ran_cycle=False, skipped_reason="no_candles")

        finals = [c for c in candles if c.is_final]
        if not finals:
            return SoakPollOutcome(ran_cycle=False, skipped_reason="no_final_candle")

        newest = finals[-1]
        if (
            self._last_final_open is not None
            and newest.open_time <= self._last_final_open
        ):
            return SoakPollOutcome(
                ran_cycle=False,
                skipped_reason="already_processed",
                final_open_time=newest.open_time,
            )

        result = await run_paper_cycle(
            candles,
            deps=self.deps,
            instrument=self.instrument,
            timeframe=self.timeframe,
            now=clock,
        )
        self._last_final_open = newest.open_time
        self.cycles_run += 1
        logger.info(
            "paper_soak_cycle open=%s decision=%s order=%s",
            newest.open_time.isoformat(),
            None if result.decision is None else result.decision.decision.value,
            None if result.order is None else str(result.order.order_id),
            extra={"event": "paper_soak_cycle", "component": "soak"},
        )
        return SoakPollOutcome(
            ran_cycle=True,
            result=result,
            final_open_time=newest.open_time,
        )


async def run_soak_loop(
    runner: PaperSoakRunner,
    *,
    poll_seconds: float,
    shutdown: ShutdownGate,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """Background loop until shutdown. Does not place live orders."""
    sleeper = sleep or asyncio.sleep
    while not shutdown.shutting_down:
        await runner.poll_once()
        if shutdown.shutting_down:
            break
        await sleeper(poll_seconds)


def soak_instrument_from_settings(settings: Settings) -> InstrumentRef:
    return InstrumentRef(
        market_type=MarketType.SPOT,
        symbol=settings.paper_soak_symbol.strip().upper(),
    )


def soak_timeframe_from_settings(settings: Settings) -> Timeframe:
    raw = settings.paper_soak_interval.strip().lower()
    try:
        return Timeframe(raw)
    except ValueError as exc:
        msg = f"unsupported AEGIS_PAPER_SOAK_INTERVAL={raw!r}"
        raise ValueError(msg) from exc
