"""Paper suggestion review helpers and HTTP endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.main import create_app
from aegis.pipeline.cycle import PaperCycleResult
from aegis.pipeline.review import format_review_text, summarize_paper_cycle
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import InstrumentRef
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal
from aegis.schemas.risk import RiskDecision, RiskDecisionType
from aegis.validation.stage import ValidationResult


def _proposal(*, action: ProposalAction = ProposalAction.BUY) -> TradeProposal:
    now = datetime(2026, 10, 9, 16, 0, tzinfo=UTC)
    return TradeProposal(
        proposal_id=uuid4(),
        correlation_id="corr-review",
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT"),
        direction=TradeDirection.LONG if action == ProposalAction.BUY else TradeDirection.FLAT,
        timeframe=Timeframe.M5,
        strategy_id="aegis-default",
        strategy_version="0.1.0",
        action=action,
        entry_conditions={"note": "test"},
        expires_at=now,
        stop_loss=Decimal("0.40"),
        take_profit=Decimal("0.50"),
        sizing={"method": "fixed_notional", "notional": "2"},
        leverage=None,
        evidence_refs=[],
        analyst_results=[],
        jev_result={
            "evidence_package_id": str(uuid4()),
            "model": "none",
            "status": "unavailable",
            "answers": {},
            "usage": {},
            "latency_ms": 0,
        },
        uncertainty={"reasons": ["unit_test"], "prefer_no_trade": action == ProposalAction.NO_TRADE},
        invalidation={},
        supervisor_model_meta={},
        created_at=now,
    )


def test_summarize_buy_suggestion() -> None:
    now = datetime(2026, 10, 9, 16, 0, tzinfo=UTC)
    proposal = _proposal(action=ProposalAction.BUY)
    decision = RiskDecision(
        decision=RiskDecisionType.REJECT,
        policy_version="1.0",
        rules_evaluated=["RP-ACTION"],
        rejection_reasons=[{"rule_id": "RP-STOP", "detail": "demo"}],
        validated_order_params=None,
        decided_at=now,
        expires_at=now,
        correlation_id="corr-review",
        proposal_id=proposal.proposal_id,
    )
    result = PaperCycleResult(
        correlation_id="corr-review",
        validation=ValidationResult(
            ok=True,
            candles=(),
            issues=(),
            market_data_age_ms=100,
            market_integrity_ok=True,
            block_reasons=(),
        ),
        proposal=proposal,
        decision=decision,
        order=None,
        blocked_reason=None,
    )
    summary = summarize_paper_cycle(result)
    assert summary["proposal"]["action"] == "BUY"
    assert summary["proposal"]["stop_loss"] == "0.40"
    assert summary["risk"]["decision"] == "REJECT"
    text = format_review_text(summary)
    assert "SUGGESTION action=BUY" in text
    assert "HINT" in text


def test_paper_last_cycle_endpoint_when_soak_off() -> None:
    clear_settings_cache()
    app = create_app(
        Settings(
            trading_mode=TradingMode.PAPER,
            live_armed=False,
            require_database=False,
            paper_soak_enabled=False,
        )
    )
    body = TestClient(app).get("/ops/paper/last-cycle").json()
    assert body["available"] is False
    assert body["reason"] == "paper_soak_not_running"
