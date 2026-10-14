"""Full-pipeline backtest replay — no look-ahead, window isolation, paper only."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from aegis.backtest.pipeline_replay import (
    CYCLE_CLOCK_OFFSET,
    CappedLlmPort,
    LlmCallCapExceeded,
    PipelineReplayError,
    replay_pipeline,
)
from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.interfaces.jev import UnavailableJevPort
from aegis.interfaces.llm import UnavailableLlmPort
from aegis.pipeline.cycle import PaperCycleDeps, PaperCycleResult, run_paper_cycle
from aegis.risk.owner_v1 import owner_approved_policy_v1
from aegis.schemas.backtest import SplitWindows
from aegis.schemas.common import LedgerKind, MarketType, Timeframe
from aegis.schemas.evidence import EvidencePackage, JevResult
from aegis.schemas.market import Candle, InstrumentRef, OrderBookTop
from aegis.supervisor.budgets import BudgetConfig

START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
N_BARS = 180
BUDGETS = BudgetConfig(token_budget=2000, cost_budget=Decimal("1.00"), latency_budget_ms=5000)


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base: dict[str, object] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
        "paper_fee_bps": Decimal("10"),
        "paper_slippage_bps": Decimal("0"),
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _instrument() -> InstrumentRef:
    return InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")


def _candles(n: int = N_BARS) -> list[Candle]:
    """Flat then ramp inside each 60-bar block so every window sees an uptrend."""
    out: list[Candle] = []
    for i in range(n):
        j = i % 60
        price = Decimal("100") if j < 30 else Decimal("100") + Decimal(j - 30) * Decimal("3")
        open_time = START + timedelta(minutes=i)
        out.append(
            Candle(
                instrument=_instrument(),
                interval=Timeframe.M1,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=Decimal("100"),
                is_final=True,
                source="fixture",
                # Stored received_at is long after close; replay must rewrite it.
                received_at=open_time + timedelta(days=3),
            )
        )
    return out


def _splits(candles: Sequence[Candle]) -> SplitWindows:
    eps = timedelta(microseconds=1)
    return SplitWindows(
        train_start=candles[0].close_time,
        train_end=candles[59].close_time,
        validation_start=candles[59].close_time + eps,
        validation_end=candles[119].close_time,
        oos_start=candles[119].close_time + eps,
        oos_end=candles[-1].close_time,
    )


class ScriptedLlmPort:
    """Test-only LLM: BUY at the current bar close set by the replay spy."""

    def __init__(self) -> None:
        self.entry_price: Decimal | None = None
        self.calls = 0
        self.prompts: list[str] = []

    async def complete_structured(
        self, *, prompt: str, schema_name: str, timeout_seconds: float
    ) -> dict[str, Any]:
        _ = schema_name, timeout_seconds
        self.calls += 1
        self.prompts.append(prompt)
        assert self.entry_price is not None
        entry = self.entry_price
        return {
            "action": "BUY",
            "direction": "long",
            "entry_price": str(entry),
            "stop_loss": str(entry * Decimal("0.97")),
            "take_profit": None,
            "rationale": "scripted",
            "invalidation": "scripted",
            "confidence_note": "scripted",
            "_meta": {"total_tokens": 10, "cost": "0"},
        }


class SpyJev:
    def __init__(self) -> None:
        self.as_of: list[datetime] = []

    async def evaluate(self, package: EvidencePackage) -> JevResult:
        self.as_of.append(package.as_of)
        return await UnavailableJevPort().evaluate(package)


class CycleSpy:
    def __init__(self, llm: ScriptedLlmPort | None = None) -> None:
        self.llm = llm
        self.seen: list[tuple[datetime, list[Candle]]] = []
        self.deps_ids: dict[str, set[int]] = {}

    async def __call__(
        self,
        candles: Sequence[Candle],
        *,
        deps: PaperCycleDeps,
        instrument: InstrumentRef,
        timeframe: Timeframe,
        now: datetime | None = None,
        correlation_id: str | None = None,
        spread_bps: Decimal | None = None,
        book_top: OrderBookTop | None = None,
    ) -> PaperCycleResult:
        assert now is not None
        self.seen.append((now, list(candles)))
        window = (correlation_id or "").split("-")[1]
        self.deps_ids.setdefault(window, set()).add(id(deps.paper_broker))
        if self.llm is not None:
            self.llm.entry_price = candles[-1].close
        return await run_paper_cycle(
            candles,
            deps=deps,
            instrument=instrument,
            timeframe=timeframe,
            now=now,
            correlation_id=correlation_id,
            spread_bps=spread_bps,
            book_top=book_top,
        )


async def _replay(
    *,
    settings: Settings | None = None,
    llm: Any = None,
    jev: Any = None,
    spread: Decimal | None = Decimal("5"),
    spy: CycleSpy | None = None,
    candles: list[Candle] | None = None,
    window_candles: int = 60,
) -> Any:
    data = candles if candles is not None else _candles()
    return await replay_pipeline(
        data,
        settings=settings or _settings(),
        risk_policy=owner_approved_policy_v1(),
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        splits=_splits(_candles()),
        jev_port=jev or UnavailableJevPort(),
        llm_port=llm or UnavailableLlmPort(),
        supervisor_budgets=BUDGETS,
        spread_bps=spread,
        initial_quote_balance=Decimal("1000"),
        window_candles=window_candles,
        created_at=START,
        cycle_fn=spy,
    )


@pytest.mark.asyncio
async def test_no_look_ahead_cycle_and_jev_only_see_past() -> None:
    llm = ScriptedLlmPort()
    jev = SpyJev()
    spy = CycleSpy(llm)
    await _replay(llm=llm, jev=jev, spy=spy)
    assert len(spy.seen) == N_BARS
    for now, visible in spy.seen:
        bar_close = now - CYCLE_CLOCK_OFFSET
        assert visible[-1].close_time == bar_close
        assert all(c.close_time <= bar_close for c in visible)
        # received_at rewritten so freshness reflects the bar close, not storage time.
        assert all(c.received_at == c.close_time for c in visible)
        assert len(visible) <= 60
    assert jev.as_of
    seen_nows = {now for now, _ in spy.seen}
    for as_of in jev.as_of:
        assert as_of + CYCLE_CLOCK_OFFSET in seen_nows


@pytest.mark.asyncio
async def test_unsorted_input_with_partial_bar_is_replayed_in_order() -> None:
    data = _candles()
    partial = data[-1].model_copy(
        update={"open_time": data[-1].open_time + timedelta(minutes=1), "is_final": False}
    )
    shuffled = [partial, *reversed(data)]
    spy = CycleSpy()
    await _replay(spy=spy, candles=shuffled)
    nows = [now for now, _ in spy.seen]
    assert nows == sorted(nows)
    assert len(nows) == N_BARS  # non-final bar ignored
    assert all(c.is_final for _, vis in spy.seen for c in vis)


@pytest.mark.asyncio
async def test_unavailable_llm_yields_zero_trades() -> None:
    report = await _replay()
    for m in (report.train_metrics, report.validation_metrics, report.oos_metrics):
        assert m.turnover == 0
        assert m.cost_contribution == 0
        assert m.net_return_after_costs == 0
    assert report.assumptions["counters"]["filled"] == "0"


@pytest.mark.asyncio
async def test_scripted_llm_fills_and_windows_are_isolated() -> None:
    llm = ScriptedLlmPort()
    spy = CycleSpy(llm)
    report = await _replay(llm=llm, spy=spy)
    assert llm.calls > 0
    filled = int(report.assumptions["counters"]["filled"])
    assert filled > 0
    # Each window used exactly one broker, and no broker is shared across windows.
    assert set(spy.deps_ids) == {"train", "validation", "oos"}
    brokers = [ids for ids in spy.deps_ids.values()]
    assert all(len(ids) == 1 for ids in brokers)
    assert len(set().union(*brokers)) == 3
    windows = (report.train_metrics, report.validation_metrics, report.oos_metrics)
    assert sum(m.turnover > 0 for m in windows) >= 2
    assert report.assumptions["spread_bps"] == "5"
    assert "ASSUMPTION" in report.uncertainty_notes


@pytest.mark.asyncio
async def test_spread_none_never_fills() -> None:
    llm = ScriptedLlmPort()
    spy = CycleSpy(llm)
    report = await _replay(llm=llm, spy=spy, spread=None)
    assert llm.calls > 0
    assert report.assumptions["counters"]["filled"] == "0"
    assert report.assumptions["counters"]["approved"] == "0"
    assert "unobserved" in report.uncertainty_notes


@pytest.mark.asyncio
async def test_live_mode_refused() -> None:
    with pytest.raises(PipelineReplayError):
        await _replay(
            settings=Settings.model_construct(
                trading_mode=TradingMode.LIVE, live_armed=True, kill_switch=False
            )
        )


@pytest.mark.asyncio
async def test_fills_are_paper_ledger_only() -> None:
    llm = ScriptedLlmPort()
    seen_kinds: set[LedgerKind] = set()

    class KindSpy(CycleSpy):
        async def __call__(self, candles: Sequence[Candle], **kw: Any) -> PaperCycleResult:
            res = await super().__call__(candles, **kw)
            if res.order is not None:
                seen_kinds.add(res.order.ledger_kind)
            return res

    await _replay(llm=llm, spy=KindSpy(llm))
    assert seen_kinds == {LedgerKind.PAPER}


async def test_capped_llm_port_limits_total_calls() -> None:
    llm = ScriptedLlmPort()
    llm.entry_price = Decimal("1")
    capped = CappedLlmPort(llm, max_calls=2)
    for _ in range(2):
        await capped.complete_structured(prompt="p", schema_name="s", timeout_seconds=1.0)
    with pytest.raises(LlmCallCapExceeded):
        await capped.complete_structured(prompt="p", schema_name="s", timeout_seconds=1.0)
    assert llm.calls == 2
    with pytest.raises(ValueError):
        CappedLlmPort(llm, max_calls=0)


def _ramp_then_gap_candles(*, gap_price: Decimal | None) -> list[Candle]:
    """Per 60-bar window: flat 100, ramp +3/bar, then (optionally) gap down and stay."""
    out: list[Candle] = []
    for i in range(N_BARS):
        j = i % 60
        if j < 30:
            price = Decimal("100")
        elif j < 40 or gap_price is None:
            price = Decimal("100") + Decimal(min(j, 59) - 30) * Decimal("3")
        else:
            price = gap_price
        open_time = START + timedelta(minutes=i)
        out.append(
            Candle(
                instrument=_instrument(),
                interval=Timeframe.M1,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=Decimal("100"),
                is_final=True,
                source="fixture",
                received_at=open_time + timedelta(days=3),
            )
        )
    return out


class ScriptedTargetLlmPort(ScriptedLlmPort):
    """BUY with stop 3% below and target 1% above the bar close."""

    async def complete_structured(
        self, *, prompt: str, schema_name: str, timeout_seconds: float
    ) -> dict[str, Any]:
        out = await super().complete_structured(
            prompt=prompt, schema_name=schema_name, timeout_seconds=timeout_seconds
        )
        assert self.entry_price is not None
        out["take_profit"] = str(self.entry_price * Decimal("1.01"))
        return out


@pytest.mark.asyncio
async def test_gap_through_stop_is_realized_with_r_metrics() -> None:
    llm = ScriptedLlmPort()
    report = await _replay(
        llm=llm, spy=CycleSpy(llm), candles=_ramp_then_gap_candles(gap_price=Decimal("80"))
    )
    counters = report.assumptions["counters"]
    assert int(counters["closed_stop_loss"]) >= 1
    assert int(counters["exit_fills"]) == int(counters["closed_stop_loss"])
    assert "exit_model" in report.assumptions
    for m in (report.train_metrics, report.validation_metrics, report.oos_metrics):
        assert m.trade_count >= 1
        assert m.r_trade_count >= 1
        assert m.avg_r is not None and m.avg_r < -1  # gap fills worse than the stop
        assert m.expectancy_r is not None and m.expectancy_r < 0
        assert m.sharpe_per_bar is not None and m.sortino_per_bar is not None


@pytest.mark.asyncio
async def test_take_profit_is_realized_on_ramp() -> None:
    llm = ScriptedTargetLlmPort()
    report = await _replay(
        llm=llm, spy=CycleSpy(llm), candles=_ramp_then_gap_candles(gap_price=None)
    )
    counters = report.assumptions["counters"]
    assert int(counters["closed_take_profit"]) >= 1
    assert counters["closed_stop_loss"] == "0"
    m = report.train_metrics
    assert m.avg_r is not None and m.avg_r > 0
    assert m.win_rate is not None and m.win_rate > 0


@pytest.mark.asyncio
async def test_exits_are_idempotent_when_cycle_also_applies_them() -> None:
    """Parent may also call apply_exits inside run_paper_cycle: no double exit."""
    from aegis.paper.exits import apply_exits

    llm = ScriptedLlmPort()

    class ExitingSpy(CycleSpy):
        async def __call__(self, candles: Sequence[Candle], **kw: Any) -> PaperCycleResult:
            deps = kw["deps"]
            apply_exits(deps.paper_broker, kw["instrument"], candles, now=kw["now"])
            return await super().__call__(candles, **kw)

    report = await _replay(
        llm=llm, spy=ExitingSpy(llm), candles=_ramp_then_gap_candles(gap_price=Decimal("80"))
    )
    counters = report.assumptions["counters"]
    assert counters["exit_fills"] == counters["closed_stop_loss"] == "3"
