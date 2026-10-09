"""Schema round-trip tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from aegis.schemas import (
    AnalystEvidence,
    AnalystType,
    Candle,
    EvidencePackage,
    EvidenceStatus,
    InstrumentRef,
    JevResult,
    LedgerKind,
    MarketType,
    OrderIntent,
    ProposalAction,
    RiskDecision,
    RiskDecisionType,
    Timeframe,
    TradeProposal,
)


def test_trade_proposal_round_trip() -> None:
    now = datetime.now(UTC)
    instrument = InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")
    jev = JevResult(status=EvidenceStatus.UNAVAILABLE)
    proposal = TradeProposal(
        proposal_id=uuid4(),
        correlation_id="corr-1",
        instrument=instrument,
        timeframe=Timeframe.M5,
        strategy_id="demo",
        strategy_version="0.0.1",
        action=ProposalAction.NO_TRADE,
        expires_at=now,
        evidence_refs=[],
        analyst_results=[
            AnalystEvidence(
                analyst_type=AnalystType.TECHNICAL,
                status=EvidenceStatus.UNAVAILABLE,
                evidence_time=now,
            )
        ],
        jev_result=jev,
        created_at=now,
    )
    restored = TradeProposal.model_validate(proposal.model_dump(mode="json"))
    assert restored.action == ProposalAction.NO_TRADE
    assert restored.instrument.symbol == "ETHUSDT"


def test_risk_decision_reject() -> None:
    now = datetime.now(UTC)
    decision = RiskDecision(
        decision=RiskDecisionType.REJECT,
        policy_version="0.1-draft",
        rules_evaluated=["RP-KILL-SWITCH"],
        rejection_reasons=[{"rule_id": "RP-KILL-SWITCH", "detail": "active"}],
        decided_at=now,
        expires_at=now,
        correlation_id="corr-2",
        proposal_id=uuid4(),
    )
    assert decision.decision == RiskDecisionType.REJECT


def test_candle_and_order_intent() -> None:
    now = datetime.now(UTC)
    instrument = InstrumentRef(market_type=MarketType.FUTURES, symbol="BTC-SWAP-USDT")
    candle = Candle(
        instrument=instrument,
        interval=Timeframe.M1,
        open_time=now,
        close_time=now,
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("0.5"),
        close=Decimal("1.5"),
        volume=Decimal("10"),
        is_final=True,
        source="fixture",
        received_at=now,
    )
    assert candle.is_final is True

    intent = OrderIntent(
        intent_id=uuid4(),
        proposal_id=uuid4(),
        risk_decision_correlation_id="corr-3",
        client_order_id="client-1",
        ledger_kind=LedgerKind.PAPER,
        instrument=instrument,
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("1"),
        price=Decimal("100"),
        created_at=now,
    )
    assert intent.ledger_kind == LedgerKind.PAPER


def test_evidence_package() -> None:
    now = datetime.now(UTC)
    package = EvidencePackage(
        package_id=uuid4(),
        correlation_id="corr-4",
        created_at=now,
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT"),
        market_type=MarketType.SPOT,
        timeframe=Timeframe.M15,
        as_of=now,
    )
    assert package.schema_version == "1"
