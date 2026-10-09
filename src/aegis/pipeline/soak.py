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

# Engineering cap — not risk policy. Keeps free LLM budgets from exploding.
_MAX_SOAK_SYMBOLS = 10


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
    symbol: str | None = None


@dataclass
class PaperSoakRunner:
    """Poll public market data; one paper cycle per symbol per new final bar."""

    market_data: MarketDataPort | CandleFetcher
    deps: PaperCycleDeps
    instruments: tuple[InstrumentRef, ...]
    timeframe: Timeframe
    candle_limit: int = 100
    _last_final_open: dict[str, datetime] = field(default_factory=dict, init=False)
    cycles_run: int = field(default=0, init=False)
    last_outcome: SoakPollOutcome | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if not self.instruments:
            msg = "PaperSoakRunner requires at least one instrument"
            raise ValueError(msg)

    @property
    def instrument(self) -> InstrumentRef:
        """Primary symbol (first in list) — backward-compatible accessor."""
        return self.instruments[0]

    @property
    def symbols(self) -> list[str]:
        return [i.symbol for i in self.instruments]

    async def poll_once(self, now: datetime | None = None) -> SoakPollOutcome:
        """Fetch candles for each symbol; run a cycle when a new final bar appears."""
        clock = now if now is not None else datetime.now(tz=UTC)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=UTC)

        if self.deps.settings.trading_mode == TradingMode.LIVE:
            outcome = SoakPollOutcome(
                ran_cycle=False, skipped_reason="trading_mode_live"
            )
            self.last_outcome = outcome
            return outcome
        if self.deps.settings.kill_switch:
            outcome = SoakPollOutcome(ran_cycle=False, skipped_reason="kill_switch")
            self.last_outcome = outcome
            return outcome
        if self.deps.settings.trading_mode not in {
            TradingMode.PAPER,
            TradingMode.DEVELOPMENT,
        }:
            outcome = SoakPollOutcome(
                ran_cycle=False,
                skipped_reason=f"unsupported_mode:{self.deps.settings.trading_mode.value}",
            )
            self.last_outcome = outcome
            return outcome

        last_skip: SoakPollOutcome | None = None
        last_run: SoakPollOutcome | None = None
        for instrument in self.instruments:
            outcome = await self._poll_one_symbol(instrument, clock)
            if outcome.ran_cycle:
                last_run = outcome
            else:
                last_skip = outcome

        chosen = last_run if last_run is not None else last_skip
        if chosen is None:
            chosen = SoakPollOutcome(ran_cycle=False, skipped_reason="no_instruments")
        self.last_outcome = chosen
        return chosen

    async def _poll_one_symbol(
        self,
        instrument: InstrumentRef,
        clock: datetime,
    ) -> SoakPollOutcome:
        symbol = instrument.symbol
        try:
            raw = self.market_data.get_candles(
                instrument,
                self.timeframe,
                limit=self.candle_limit,
            )
            candles = sorted(list(raw), key=lambda c: c.open_time)
        except Exception as exc:  # noqa: BLE001 — network/parse: skip, no order retry
            logger.warning(
                "paper_soak_fetch_failed symbol=%s err=%s",
                symbol,
                type(exc).__name__,
                extra={"event": "paper_soak_fetch_failed", "component": "soak"},
            )
            return SoakPollOutcome(
                ran_cycle=False,
                skipped_reason=f"fetch_error:{type(exc).__name__}",
                symbol=symbol,
            )

        if not candles:
            return SoakPollOutcome(
                ran_cycle=False, skipped_reason="no_candles", symbol=symbol
            )

        finals = [c for c in candles if c.is_final]
        if not finals:
            return SoakPollOutcome(
                ran_cycle=False,
                skipped_reason="no_final_candle",
                symbol=symbol,
            )

        newest = finals[-1]
        prev = self._last_final_open.get(symbol)
        if prev is not None and newest.open_time <= prev:
            return SoakPollOutcome(
                ran_cycle=False,
                skipped_reason="already_processed",
                final_open_time=newest.open_time,
                symbol=symbol,
            )

        result = await run_paper_cycle(
            candles,
            deps=self.deps,
            instrument=instrument,
            timeframe=self.timeframe,
            now=clock,
        )
        self._last_final_open[symbol] = newest.open_time
        self.cycles_run += 1
        action = None if result.proposal is None else result.proposal.action.value
        logger.info(
            "paper_soak_cycle symbol=%s open=%s suggestion=%s decision=%s order=%s",
            symbol,
            newest.open_time.isoformat(),
            action,
            None if result.decision is None else result.decision.decision.value,
            None if result.order is None else str(result.order.order_id),
            extra={"event": "paper_soak_cycle", "component": "soak"},
        )
        return SoakPollOutcome(
            ran_cycle=True,
            result=result,
            final_open_time=newest.open_time,
            symbol=symbol,
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


def parse_soak_symbols(raw: str) -> list[str]:
    """Parse comma/semicolon-separated soak symbols (uppercase, deduped)."""
    parts = [p.strip().upper() for p in raw.replace(";", ",").split(",")]
    symbols: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        if not part.isalnum():
            msg = f"invalid soak symbol {part!r} (use e.g. ADAUSDT)"
            raise ValueError(msg)
        if part in seen:
            continue
        seen.add(part)
        symbols.append(part)
    if not symbols:
        msg = "AEGIS_PAPER_SOAK_SYMBOL must be non-empty"
        raise ValueError(msg)
    if len(symbols) > _MAX_SOAK_SYMBOLS:
        msg = (
            f"AEGIS_PAPER_SOAK_SYMBOL allows at most {_MAX_SOAK_SYMBOLS} symbols "
            f"(got {len(symbols)}); reduce the list"
        )
        raise ValueError(msg)
    return symbols


def soak_instruments_from_settings(settings: Settings) -> tuple[InstrumentRef, ...]:
    symbols = parse_soak_symbols(settings.paper_soak_symbol)
    return tuple(
        InstrumentRef(market_type=MarketType.SPOT, symbol=symbol) for symbol in symbols
    )


def soak_instrument_from_settings(settings: Settings) -> InstrumentRef:
    """First soak symbol (compat helper)."""
    return soak_instruments_from_settings(settings)[0]


def soak_timeframe_from_settings(settings: Settings) -> Timeframe:
    raw = settings.paper_soak_interval.strip().lower()
    try:
        return Timeframe(raw)
    except ValueError as exc:
        msg = f"unsupported AEGIS_PAPER_SOAK_INTERVAL={raw!r}"
        raise ValueError(msg) from exc
