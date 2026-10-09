"""Paper equity tracker and RiskContext population tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from aegis.paper.equity import (
    PaperEquityTracker,
    ProposalHistory,
    compute_paper_equity,
    instrument_mark_key,
)
from aegis.paper.ledger import PaperLedger
from aegis.pipeline.context import build_paper_risk_context
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import Position
from aegis.validation.stage import ValidationResult


def _now() -> datetime:
    return datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def _instrument() -> InstrumentRef:
    return InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT")


def test_compute_equity_cash_plus_marked_position() -> None:
    ledger = PaperLedger()
    ledger.set_balance(Decimal("8"))
    inst = _instrument()
    ledger.upsert_position(
        Position(
            position_id=uuid4(),
            ledger_kind=LedgerKind.PAPER,
            instrument=inst,
            market_type=MarketType.SPOT,
            quantity=Decimal("10"),
            entry_price=Decimal("0.50"),
            unrealized_pnl=None,
            updated_at=_now(),
        )
    )
    key = instrument_mark_key(inst)
    equity = compute_paper_equity(ledger, marks={key: Decimal("0.40")})
    assert equity == Decimal("12")  # 8 + 10*0.40


def test_daily_loss_and_drawdown_and_cooldown() -> None:
    tracker = PaperEquityTracker()
    t0 = _now()
    snap0 = tracker.observe(Decimal("10"), now=t0, daily_loss_limit=Decimal("2.2"))
    assert snap0.daily_loss == Decimal("0")
    assert snap0.drawdown == Decimal("0")
    assert snap0.cooldown_active is False

    snap1 = tracker.observe(
        Decimal("7"),
        now=t0 + timedelta(hours=1),
        daily_loss_limit=Decimal("2.2"),
    )
    assert snap1.daily_loss == Decimal("3")
    assert snap1.drawdown == Decimal("3")
    assert snap1.cooldown_active is True


def test_build_context_uses_tracker_not_zeros() -> None:
    ledger = PaperLedger()
    ledger.set_balance(Decimal("10"))
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("10"), now=_now())
    tracker.observe(
        Decimal("7"),
        now=_now() + timedelta(minutes=5),
        daily_loss_limit=Decimal("2.2"),
    )

    ledger.set_balance(Decimal("7"))
    validation = ValidationResult(
        ok=True,
        candles=(),
        issues=(),
        market_data_age_ms=100,
        market_integrity_ok=True,
        block_reasons=(),
    )
    # Fresh tracker that already saw peak 10
    tracker2 = PaperEquityTracker()
    tracker2.observe(Decimal("10"), now=_now())
    ctx = build_paper_risk_context(
        now=_now() + timedelta(minutes=1),
        validation=validation,
        ledger=ledger,
        instrument=_instrument(),
        proposal=None,
        equity_tracker=tracker2,
        daily_loss_limit=Decimal("2.2"),
    )
    assert ctx.daily_loss == Decimal("3")
    assert ctx.drawdown == Decimal("3")
    assert ctx.cooldown_active is True


def test_proposal_history_duplicate() -> None:
    history = ProposalHistory()
    pid = uuid4()
    assert history.is_duplicate(pid) is False
    history.record(pid)
    assert history.is_duplicate(pid) is True
    assert pid in history.recent_ids
