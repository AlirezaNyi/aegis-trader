"""Paper soak runner — debounce finalized bars; no live submit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.interfaces.execution import NullExecutionPort
from aegis.interfaces.jev import UnavailableJevPort
from aegis.interfaces.llm import UnavailableLlmPort
from aegis.main import create_app
from aegis.paper.broker import PaperBroker
from aegis.pipeline.cycle import PaperCycleDeps
from aegis.pipeline.soak import PaperSoakRunner
from aegis.risk.owner_v1 import owner_approved_policy_v1
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base: dict[str, object] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
        "paper_soak_enabled": False,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _instrument() -> InstrumentRef:
    return InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT")


def _candle(
    open_time: datetime,
    close: str = "0.50",
    *,
    is_final: bool = True,
    received_at: datetime | None = None,
) -> Candle:
    return Candle(
        instrument=_instrument(),
        interval=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
        open=Decimal(close),
        high=Decimal(close) + Decimal("0.01"),
        low=Decimal(close) - Decimal("0.01"),
        close=Decimal(close),
        volume=Decimal("1000"),
        is_final=is_final,
        source="fixture",
        received_at=received_at or (open_time + timedelta(seconds=1)),
    )


class FakeMarketData:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.calls = 0

    def get_candles(
        self,
        instrument: InstrumentRef,
        interval: Timeframe,
        *,
        limit: int = 100,
    ) -> list[Candle]:
        _ = instrument, interval, limit
        self.calls += 1
        return list(self.candles)


def _deps(settings: Settings | None = None) -> PaperCycleDeps:
    resolved = settings or _settings()
    return PaperCycleDeps(
        settings=resolved,
        risk_policy=owner_approved_policy_v1(),
        jev_port=UnavailableJevPort(),
        llm_port=UnavailableLlmPort(),
        supervisor_budgets=None,
        paper_broker=PaperBroker(resolved, initial_quote_balance=Decimal("10")),
    )


@pytest.mark.asyncio
async def test_soak_debounce_same_final_bar() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = [_candle(start + timedelta(minutes=i)) for i in range(25)]
    now = candles[-1].received_at + timedelta(milliseconds=100)
    md = FakeMarketData(candles)
    runner = PaperSoakRunner(
        market_data=md,  # type: ignore[arg-type]
        deps=_deps(),
        instrument=_instrument(),
        timeframe=Timeframe.M1,
    )
    first = await runner.poll_once(now=now)
    second = await runner.poll_once(now=now)
    assert first.ran_cycle is True
    assert first.result is not None
    assert first.result.order is None  # unavailable LLM → NO_TRADE
    assert second.ran_cycle is False
    assert second.skipped_reason == "already_processed"
    assert runner.cycles_run == 1
    assert md.calls == 2


@pytest.mark.asyncio
async def test_soak_new_final_bar_runs_again() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles_a = [_candle(start + timedelta(minutes=i)) for i in range(25)]
    now_a = candles_a[-1].received_at + timedelta(milliseconds=50)
    md: FakeMarketData = FakeMarketData(candles_a)
    runner = PaperSoakRunner(
        market_data=md,  # type: ignore[arg-type]
        deps=_deps(),
        instrument=_instrument(),
        timeframe=Timeframe.M1,
    )
    assert (await runner.poll_once(now=now_a)).ran_cycle is True

    candles_b = candles_a + [_candle(start + timedelta(minutes=25))]
    now_b = candles_b[-1].received_at + timedelta(milliseconds=50)
    md.candles = candles_b
    second = await runner.poll_once(now=now_b)
    assert second.ran_cycle is True
    assert runner.cycles_run == 2


@pytest.mark.asyncio
async def test_soak_refuses_live_mode() -> None:
    settings = Settings.model_construct(
        trading_mode=TradingMode.LIVE,
        live_armed=True,
        kill_switch=False,
        require_database=False,
        paper_fee_bps=Decimal("10"),
        paper_slippage_bps=Decimal("0"),
        analyst_concurrency=5,
        paper_soak_enabled=True,
        paper_soak_symbol="ADAUSDT",
        paper_soak_interval="1m",
        paper_soak_poll_seconds=30.0,
    )
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    md = FakeMarketData([_candle(start)])
    runner = PaperSoakRunner(
        market_data=md,  # type: ignore[arg-type]
        deps=_deps(settings),  # type: ignore[arg-type]
        instrument=_instrument(),
        timeframe=Timeframe.M1,
    )
    outcome = await runner.poll_once(now=start + timedelta(seconds=2))
    assert outcome.ran_cycle is False
    assert outcome.skipped_reason == "trading_mode_live"
    assert md.calls == 0


def test_create_app_soak_off_keeps_null_execution() -> None:
    clear_settings_cache()
    app = create_app(
        Settings(
            trading_mode=TradingMode.PAPER,
            live_armed=False,
            kill_switch=False,
            require_database=False,
            paper_soak_enabled=False,
        )
    )
    assert isinstance(app.state.execution_port, NullExecutionPort)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert app.state.paper_soak_runner is None


def test_create_app_soak_on_still_null_execution() -> None:
    """Soak may start a public MD task; signed live port must stay NullExecutionPort."""
    clear_settings_cache()
    app = create_app(
        Settings(
            trading_mode=TradingMode.PAPER,
            live_armed=False,
            kill_switch=False,
            require_database=False,
            paper_soak_enabled=True,
            paper_soak_poll_seconds=3600.0,
        )
    )
    assert isinstance(app.state.execution_port, NullExecutionPort)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert app.state.paper_soak_runner is not None
    assert isinstance(app.state.execution_port, NullExecutionPort)
