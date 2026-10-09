"""Build paper OrderIntent from an APPROVE RiskDecision + TradeProposal."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from aegis.schemas.common import LedgerKind
from aegis.schemas.orders import OrderIntent
from aegis.schemas.proposal import ProposalAction, TradeProposal
from aegis.schemas.risk import RiskDecision, RiskDecisionType


class IntentBuildError(ValueError):
    """Approved decision cannot be mapped to a paper OrderIntent."""


def intent_from_approved(
    decision: RiskDecision,
    proposal: TradeProposal,
    *,
    now: datetime,
) -> OrderIntent:
    """Map validated APPROVE params into a paper OrderIntent.

    Quantity is ``notional / entry_price`` from sizing and entry_conditions.
    """
    if decision.decision != RiskDecisionType.APPROVE:
        raise IntentBuildError("only APPROVE decisions produce intents")
    if decision.proposal_id != proposal.proposal_id:
        raise IntentBuildError("decision.proposal_id does not match proposal")
    if decision.correlation_id != proposal.correlation_id:
        raise IntentBuildError("decision.correlation_id does not match proposal")

    params = decision.validated_order_params or {}
    entry = dict(params.get("entry_conditions") or proposal.entry_conditions)
    sizing = dict(params.get("sizing") or proposal.sizing)

    action_raw = params.get("action") or proposal.action.value
    try:
        action = ProposalAction(str(action_raw))
    except ValueError as exc:
        raise IntentBuildError(f"unsupported action={action_raw!r}") from exc
    if action not in {ProposalAction.BUY, ProposalAction.SELL}:
        raise IntentBuildError(f"action={action.value} is not a trade side")

    order_type = str(entry.get("order_type") or "LIMIT").upper()
    tif = entry.get("time_in_force")
    time_in_force = str(tif).upper() if tif is not None else None

    try:
        entry_price = Decimal(str(entry["entry_price"]))
        notional = Decimal(str(sizing["notional"]))
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise IntentBuildError(
            "entry_price and sizing.notional are required for paper intent"
        ) from exc
    if entry_price <= 0 or notional <= 0:
        raise IntentBuildError("entry_price and notional must be positive")

    quantity = notional / entry_price
    return OrderIntent(
        intent_id=uuid4(),
        proposal_id=proposal.proposal_id,
        risk_decision_correlation_id=decision.correlation_id,
        client_order_id=f"paper-{uuid4().hex[:16]}",
        ledger_kind=LedgerKind.PAPER,
        instrument=proposal.instrument,
        side=action.value,
        order_type=order_type,
        quantity=quantity,
        price=entry_price,
        time_in_force=time_in_force,
        created_at=now,
    )
