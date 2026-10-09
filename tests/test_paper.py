"""Phase 6 paper broker tests."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.paper import (
    PaperBroker,
    PaperLedgerKindError,
    PaperTradingBlocked,
    compute_fee,
    recover_from_snapshot,
)
from aegis.risk.handoff import RiskHandoffDenied
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import OrderIntent, OrderStatus
from aegis.schemas.risk import RiskDecision, RiskDecisionType


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base = {
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
    return InstrumentRef(market_type=MarketType.SPOT, symbol="BTCUSDT")


def _intent(
    *,
    ledger_kind: LedgerKind = LedgerKind.PAPER,
    side: str = "BUY",
    quantity: Decimal = Decimal("1"),
    price: Decimal | None = Decimal("100"),
    client_order_id: str | None = None,
    proposal_id: object | None = None,
    correlation_id: str = "corr-paper",
) -> OrderIntent:
    from uuid import UUID

    pid = proposal_id if isinstance(proposal_id, UUID) else uuid4()
    return OrderIntent(
        intent_id=uuid4(),
        proposal_id=pid,
        risk_decision_correlation_id=correlation_id,
        client_order_id=client_order_id or f"c-{uuid4().hex[:8]}",
        ledger_kind=ledger_kind,
        instrument=_instrument(),
        side=side,
        order_type="LIMIT" if price is not None else "MARKET",
        quantity=quantity,
        price=price,
        created_at=datetime(2026, 10, 9, tzinfo=UTC),
    )


def _decision(
    kind: RiskDecisionType,
    *,
    expires_at: datetime | None = None,
    proposal_id: object | None = None,
    correlation_id: str = "corr-paper",
) -> RiskDecision:
    from uuid import UUID

    now = datetime(2026, 10, 9, tzinfo=UTC)
    pid = proposal_id if isinstance(proposal_id, UUID) else uuid4()
    return RiskDecision(
        decision=kind,
        policy_version="test-approve",
        rules_evaluated=["TEST"],
        rejection_reasons=(
            [{"rule_id": "TEST", "detail": "reject"}] if kind == RiskDecisionType.REJECT else []
        ),
        validated_order_params={"action": "BUY"} if kind == RiskDecisionType.APPROVE else None,
        decided_at=now,
        expires_at=expires_at or (now + timedelta(hours=1)),
        correlation_id=correlation_id,
        proposal_id=pid,
    )


def _paired_approve_intent(**intent_kwargs: object) -> tuple[RiskDecision, OrderIntent]:
    proposal_id = uuid4()
    intent = _intent(proposal_id=proposal_id, **intent_kwargs)  # type: ignore[arg-type]
    decision = _decision(RiskDecisionType.APPROVE, proposal_id=proposal_id)
    return decision, intent


def test_reject_cannot_create_paper_order() -> None:
    broker = PaperBroker(_settings(), initial_quote_balance=Decimal("10000"))
    proposal_id = uuid4()
    intent = _intent(proposal_id=proposal_id)
    with pytest.raises(RiskHandoffDenied):
        broker.submit_from_risk_decision(
            _decision(RiskDecisionType.REJECT, proposal_id=proposal_id),
            intent,
            now=datetime(2026, 10, 9, tzinfo=UTC),
        )
    assert broker.ledger.orders == {}
    assert broker.ledger.fills == []


def test_mismatched_proposal_id_refused() -> None:
    from aegis.paper.exceptions import PaperFillError

    broker = PaperBroker(_settings(), initial_quote_balance=Decimal("10000"))
    with pytest.raises(PaperFillError, match="proposal_id"):
        broker.submit_from_risk_decision(
            _decision(RiskDecisionType.APPROVE, proposal_id=uuid4()),
            _intent(proposal_id=uuid4()),
            now=datetime(2026, 10, 9, tzinfo=UTC),
        )


def test_approve_paper_intent_creates_order_fill_position() -> None:
    broker = PaperBroker(_settings(), initial_quote_balance=Decimal("10000"))
    decision, intent = _paired_approve_intent(price=Decimal("100"), quantity=Decimal("2"))
    now = datetime(2026, 10, 9, tzinfo=UTC)
    order = broker.submit_from_risk_decision(
        decision,
        intent,
        now=now,
    )
    assert order.ledger_kind == LedgerKind.PAPER
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal("2")
    assert len(broker.ledger.fills) == 1
    fill = broker.ledger.fills[0]
    assert fill.ledger_kind == LedgerKind.PAPER
    assert fill.fee == compute_fee(Decimal("2"), Decimal("100"), Decimal("10"))
    pos = broker.ledger.position_for("toobit", "spot", "BTCUSDT")
    assert pos is not None
    assert pos.ledger_kind == LedgerKind.PAPER
    assert pos.quantity == Decimal("2")
    # cash = 10000 - 200 - fee
    assert broker.ledger.get_balance() == Decimal("10000") - Decimal("200") - fill.fee


def test_kill_switch_blocks_new_paper_orders() -> None:
    broker = PaperBroker(_settings(kill_switch=True), initial_quote_balance=Decimal("10000"))
    decision, intent = _paired_approve_intent()
    with pytest.raises(PaperTradingBlocked):
        broker.submit_from_risk_decision(
            decision,
            intent,
            now=datetime(2026, 10, 9, tzinfo=UTC),
        )


def test_fee_applied_consistently() -> None:
    fee_bps = Decimal("25")
    broker = PaperBroker(
        _settings(paper_fee_bps=fee_bps, paper_slippage_bps=Decimal("0")),
        initial_quote_balance=Decimal("100000"),
    )
    decision, intent = _paired_approve_intent(price=Decimal("50"), quantity=Decimal("4"))
    broker.submit_from_risk_decision(
        decision,
        intent,
        now=datetime(2026, 10, 9, tzinfo=UTC),
    )
    expected = compute_fee(Decimal("4"), Decimal("50"), fee_bps)
    assert broker.ledger.fills[0].fee == expected


def test_persist_reload_reconcile_restart() -> None:
    broker = PaperBroker(_settings(), initial_quote_balance=Decimal("5000"))
    decision, intent = _paired_approve_intent(price=Decimal("100"), quantity=Decimal("1"))
    broker.submit_from_risk_decision(
        decision,
        intent,
        now=datetime(2026, 10, 9, tzinfo=UTC),
    )
    snapshot = broker.ledger.to_snapshot()
    loaded, result = recover_from_snapshot(snapshot)
    assert result.ok is True
    assert loaded.checksum() == broker.ledger.checksum()
    assert loaded.get_balance() == broker.ledger.get_balance()
    assert len(loaded.orders) == 1
    assert len(loaded.fills) == 1


def test_live_ledger_kind_refused() -> None:
    broker = PaperBroker(_settings(), initial_quote_balance=Decimal("10000"))
    proposal_id = uuid4()
    decision = _decision(RiskDecisionType.APPROVE, proposal_id=proposal_id)
    intent = _intent(ledger_kind=LedgerKind.LIVE, proposal_id=proposal_id)
    with pytest.raises(PaperLedgerKindError):
        broker.submit_from_risk_decision(
            decision,
            intent,
            now=datetime(2026, 10, 9, tzinfo=UTC),
        )


def test_paper_package_never_imports_exchange_submit() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "aegis" / "paper"
    forbidden_modules = {
        "aegis.exchange",
        "httpx",
        "requests",
        "aiohttp",
    }
    forbidden_names = {"submit_order", "place_order", "create_order"}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden_modules
                    assert not alias.name.startswith("aegis.exchange")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert mod not in forbidden_modules
                assert not mod.startswith("aegis.exchange")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_names


def test_null_execution_still_refuses_live() -> None:
    from aegis.interfaces.execution import NullExecutionPort

    port = NullExecutionPort()
    with pytest.raises(RuntimeError):
        port.submit(_intent(ledger_kind=LedgerKind.LIVE))
