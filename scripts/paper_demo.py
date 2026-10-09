#!/usr/bin/env python3
"""Manual paper demo: synthetic proposal → Risk Engine → PaperBroker fill.

No exchange credentials. No live arming. Uses owner policy v1.0 limits
(max notional 2 USDT, ADAUSDT, LIMIT/GTC, long only).

Usage (from repo root):
  .venv/bin/python scripts/paper_demo.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

# Allow running without editable install path quirks.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aegis.config.settings import Settings, TradingMode  # noqa: E402
from aegis.paper.broker import PaperBroker  # noqa: E402
from aegis.risk.context import RiskContext  # noqa: E402
from aegis.risk.engine import evaluate_risk  # noqa: E402
from aegis.risk.owner_v1 import owner_approved_policy_v1  # noqa: E402
from aegis.schemas.common import EvidenceStatus, LedgerKind, MarketType, Timeframe  # noqa: E402
from aegis.schemas.evidence import JevResult  # noqa: E402
from aegis.schemas.market import InstrumentRef  # noqa: E402
from aegis.schemas.orders import OrderIntent  # noqa: E402
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal  # noqa: E402
from aegis.schemas.risk import RiskDecisionType  # noqa: E402

# Within owner v1.0: max order notional 2 USDT, stop ≤ 5%.
_SYMBOL = "ADAUSDT"
_ENTRY = Decimal("0.50")
_STOP = Decimal("0.48")  # 4% below entry
_QTY = Decimal("4")  # notional = 2.00 USDT
_NOTIONAL = _ENTRY * _QTY
_INITIAL_CASH = Decimal("10")  # owner risk capital


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _proposal(now: datetime) -> TradeProposal:
    return TradeProposal(
        proposal_id=uuid4(),
        correlation_id=f"paper-demo-{uuid4().hex[:8]}",
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol=_SYMBOL),
        direction=TradeDirection.LONG,
        timeframe=Timeframe.M15,
        strategy_id="aegis-default",
        strategy_version="0.1.0",
        action=ProposalAction.BUY,
        entry_conditions={
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "entry_price": str(_ENTRY),
        },
        expires_at=now + timedelta(seconds=30),
        stop_loss=_STOP,
        take_profit=Decimal("0.55"),
        sizing={"method": "fixed_notional", "notional": str(_NOTIONAL)},
        leverage=None,
        evidence_refs=["demo:synthetic"],
        analyst_results=[],
        jev_result=JevResult(status=EvidenceStatus.UNAVAILABLE),
        uncertainty={"note": "synthetic paper demo — not live market evidence"},
        invalidation={},
        supervisor_model_meta={"source": "scripts/paper_demo.py"},
        created_at=now,
    )


def _healthy_context(now: datetime) -> RiskContext:
    return RiskContext(
        now=now,
        market_data_age_ms=100,
        market_integrity_ok=True,
        account_available=True,
        balance_reconciled=True,
        open_positions_count=0,
        pending_orders_count=0,
        aggregate_notional=Decimal("0"),
        instrument_notional=Decimal("0"),
        proposed_notional=_NOTIONAL,
        daily_loss=Decimal("0"),
        drawdown=Decimal("0"),
        spread=Decimal("5"),
        liquidity_ok=True,
        fee_estimate=Decimal("5"),
        funding_ok=True,
        slippage_model_bps=Decimal("5"),
        symbol_precision_ok=True,
        min_size_ok=True,
        reconciliation_ok=True,
        recent_proposal_ids=[],
        duplicate_detected=False,
        cooldown_active=False,
        account_state_refs=["acct:paper-demo"],
        market_state_refs=[f"md:{_SYMBOL}:synthetic"],
    )


def _print_json(label: str, payload: object) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, default=str))


def main() -> int:
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
        risk_policy_version="1.0",
    )
    policy = owner_approved_policy_v1()
    now = _now()
    proposal = _proposal(now)
    context = _healthy_context(now)

    print("Aegis paper demo (no exchange, no live)")
    print(f"policy={policy.policy_version}  mode={settings.trading_mode.value}")
    print(f"symbol={_SYMBOL}  qty={_QTY}  entry={_ENTRY}  notional={_NOTIONAL} USDT")

    decision = evaluate_risk(proposal, context, settings, policy=policy)
    _print_json(
        "RiskDecision",
        {
            "decision": decision.decision.value,
            "policy_version": decision.policy_version,
            "proposal_id": str(decision.proposal_id),
            "correlation_id": decision.correlation_id,
            "rejection_reasons": decision.rejection_reasons,
            "validated_order_params": decision.validated_order_params,
        },
    )

    if decision.decision != RiskDecisionType.APPROVE:
        print("\nStopped: Risk did not APPROVE (no paper fill).")
        return 1

    intent = OrderIntent(
        intent_id=uuid4(),
        proposal_id=proposal.proposal_id,
        risk_decision_correlation_id=decision.correlation_id,
        client_order_id=f"paper-demo-{uuid4().hex[:12]}",
        ledger_kind=LedgerKind.PAPER,
        instrument=proposal.instrument,
        side="BUY",
        order_type="LIMIT",
        quantity=_QTY,
        price=_ENTRY,
        time_in_force="GTC",
        created_at=now,
    )

    broker = PaperBroker(settings, initial_quote_balance=_INITIAL_CASH)
    cash_before = broker.ledger.get_balance()
    order = broker.submit_from_risk_decision(
        decision,
        intent,
        mid_price=_ENTRY,
        now=now,
    )
    fill = broker.ledger.fills[-1]
    pos = broker.ledger.position_for("toobit", "spot", _SYMBOL)

    _print_json(
        "PaperFill",
        {
            "ledger_kind": order.ledger_kind.value,
            "status": order.status.value,
            "client_order_id": order.client_order_id,
            "quantity": str(order.filled_quantity),
            "fill_price": str(fill.price),
            "fee": str(fill.fee),
            "cash_before": str(cash_before),
            "cash_after": str(broker.ledger.get_balance()),
            "position_qty": str(pos.quantity) if pos else None,
            "position_entry": str(pos.entry_price) if pos and pos.entry_price else None,
            "exchange_order_id": order.exchange_order_id,
        },
    )
    print("\nOK — paper path completed. Live client was not used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
