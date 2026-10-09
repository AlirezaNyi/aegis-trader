"""Paper decision cycle — validation → risk → paper broker; never live submit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.interfaces.execution import NullExecutionPort
from aegis.interfaces.jev import UnavailableJevPort
from aegis.interfaces.llm import UnavailableLlmPort
from aegis.main import create_app
from aegis.paper.broker import PaperBroker
from aegis.pipeline.cycle import PaperCycleDeps, run_paper_cycle
from aegis.risk.owner_v1 import owner_approved_policy_v1
from aegis.schemas.common import EvidenceStatus, LedgerKind, MarketType, Timeframe
from aegis.schemas.evidence import JevResult
from aegis.schemas.market import Candle, InstrumentRef
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal
from aegis.schemas.risk import RiskDecisionType
from aegis.supervisor.budgets import BudgetConfig


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


def _candle(
    open_time: datetime,
    close: str,
    *,
    received_at: datetime | None = None,
    is_final: bool = True,
) -> Candle:
    return Candle(
        instrument=_instrument(),
        interval=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
        open=Decimal(close),
        high=Decimal(close) + Decimal("1"),
        low=Decimal(close) - Decimal("1"),
        close=Decimal(close),
        volume=Decimal("100"),
        is_final=is_final,
        source="fixture",
        received_at=received_at or (open_time + timedelta(seconds=1)),
    )


def _series(n: int, start: datetime, *, base: int = 100) -> list[Candle]:
    return [
        _candle(start + timedelta(minutes=i), str(base + i))
        for i in range(n)
    ]


def _uptrend_series(n: int, start: datetime) -> list[Candle]:
    """Steep late ramp so technical trend=up and quant has ≥30 return samples."""
    candles: list[Candle] = []
    price = Decimal("100")
    for i in range(n):
        if i < n // 2:
            price = Decimal("100")
        else:
            price = Decimal("100") + Decimal(i - n // 2) * Decimal("3")
        candles.append(_candle(start + timedelta(minutes=i), str(price)))
    return candles


class CountingLlm:
    def __init__(self, response: dict[str, Any]) -> None:
        self.calls = 0
        self.response = response

    def complete_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        _ = prompt, schema_name, timeout_seconds
        self.calls += 1
        return self.response


def _buy_llm_payload(*, entry_price: str, now: datetime) -> dict[str, Any]:
    proposal = TradeProposal(
        proposal_id=uuid4(),
        correlation_id="ignored",
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="WRONG"),
        direction=TradeDirection.LONG,
        timeframe=Timeframe.M1,
        strategy_id="model",
        strategy_version="9.9.9",
        action=ProposalAction.BUY,
        entry_conditions={
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "entry_price": entry_price,
        },
        expires_at=now + timedelta(seconds=25),
        stop_loss=Decimal(entry_price) * Decimal("0.96"),
        take_profit=None,
        sizing={"method": "fixed_notional", "notional": "2"},
        leverage=None,
        evidence_refs=[],
        analyst_results=[],
        jev_result=JevResult(status=EvidenceStatus.UNAVAILABLE),
        uncertainty={},
        invalidation={},
        supervisor_model_meta={},
        created_at=now,
    ).model_dump(mode="json")
    proposal["_meta"] = {"total_tokens": 50, "cost": "0.01"}
    return proposal


def _deps(
    *,
    settings: Settings | None = None,
    llm: Any | None = None,
    budgets: BudgetConfig | None = None,
    initial_balance: Decimal = Decimal("10000"),
) -> PaperCycleDeps:
    resolved = settings or _settings()
    broker = PaperBroker(resolved, initial_quote_balance=initial_balance)
    return PaperCycleDeps(
        settings=resolved,
        risk_policy=owner_approved_policy_v1(),
        jev_port=UnavailableJevPort(),
        llm_port=llm if llm is not None else UnavailableLlmPort(),
        supervisor_budgets=budgets,
        paper_broker=broker,
        analyst_concurrency=5,
        strategy_id="aegis-default",
        strategy_version="0.1.0",
    )


@pytest.mark.asyncio
async def test_cycle_unavailable_llm_no_trade_no_fill() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = _series(30, start)
    now = candles[-1].received_at + timedelta(milliseconds=200)
    deps = _deps()
    result = await run_paper_cycle(
        candles,
        deps=deps,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
    )
    assert result.blocked_reason is None
    assert result.validation.ok is True
    assert result.proposal is not None
    assert result.proposal.action == ProposalAction.NO_TRADE
    assert result.decision is not None
    assert result.decision.decision == RiskDecisionType.REJECT
    assert result.order is None
    assert deps.paper_broker.ledger.fills == []


@pytest.mark.asyncio
async def test_cycle_stale_data_rejects_without_fill() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = _series(30, start)
    now = candles[-1].received_at + timedelta(seconds=60)
    deps = _deps()
    result = await run_paper_cycle(
        candles,
        deps=deps,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
    )
    assert result.validation.ok is False
    assert "stale_market_data" in result.validation.block_reasons
    assert result.decision is not None
    assert result.decision.decision == RiskDecisionType.REJECT
    rule_ids = {r["rule_id"] for r in result.decision.rejection_reasons}
    assert "RP-DATA-FRESHNESS-MS" in rule_ids or "RP-DATA-INTEGRITY" in rule_ids
    assert result.order is None
    assert deps.paper_broker.ledger.fills == []


@pytest.mark.asyncio
async def test_cycle_approve_path_paper_fill() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = _uptrend_series(45, start)
    now = candles[-1].received_at + timedelta(milliseconds=100)
    entry = str(candles[-1].close)
    llm = CountingLlm(_buy_llm_payload(entry_price=entry, now=now))
    budgets = BudgetConfig(
        token_budget=2000,
        cost_budget=Decimal("1.00"),
        latency_budget_ms=5000,
    )
    deps = _deps(llm=llm, budgets=budgets)
    result = await run_paper_cycle(
        candles,
        deps=deps,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
    )
    assert result.validation.ok is True
    assert llm.calls == 1
    assert result.proposal is not None
    assert result.proposal.action == ProposalAction.BUY
    assert result.decision is not None
    assert result.decision.decision == RiskDecisionType.APPROVE, (
        result.decision.rejection_reasons
    )
    assert result.order is not None
    assert result.order.ledger_kind == LedgerKind.PAPER
    assert len(deps.paper_broker.ledger.fills) == 1


@pytest.mark.asyncio
async def test_cycle_refuses_kill_switch_without_llm_spend() -> None:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = _series(5, start)
    now = candles[-1].received_at + timedelta(milliseconds=50)
    llm = CountingLlm(_buy_llm_payload(entry_price="100", now=now))
    deps = _deps(settings=_settings(kill_switch=True), llm=llm)
    result = await run_paper_cycle(
        candles,
        deps=deps,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
    )
    assert result.blocked_reason == "kill_switch"
    assert llm.calls == 0
    assert result.order is None
    assert deps.paper_broker.ledger.fills == []


@pytest.mark.asyncio
async def test_cycle_refuses_live_mode_without_submit() -> None:
    settings = Settings.model_construct(
        trading_mode=TradingMode.LIVE,
        live_armed=True,
        kill_switch=False,
        require_database=False,
        paper_fee_bps=Decimal("10"),
        paper_slippage_bps=Decimal("0"),
        analyst_concurrency=5,
    )
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = _series(5, start)
    now = candles[-1].received_at + timedelta(milliseconds=50)
    deps = _deps(settings=settings)  # type: ignore[arg-type]
    result = await run_paper_cycle(
        candles,
        deps=deps,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
    )
    assert result.blocked_reason == "trading_mode_live"
    assert result.order is None
    assert result.decision is None
    assert deps.paper_broker.ledger.fills == []


def test_create_app_wires_paper_cycle_and_null_execution() -> None:
    clear_settings_cache()
    app = create_app(
        Settings(
            trading_mode=TradingMode.PAPER,
            live_armed=False,
            kill_switch=False,
            require_database=False,
        )
    )
    assert isinstance(app.state.execution_port, NullExecutionPort)
    assert app.state.paper_cycle_deps is not None
    assert app.state.run_paper_cycle is run_paper_cycle
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
