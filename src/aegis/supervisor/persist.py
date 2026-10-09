"""Persistence helpers for Jev results and trade proposals."""

from __future__ import annotations

from sqlalchemy.orm import Session

from aegis.db.models import JevResultRow, TradeProposalRow
from aegis.jev.persist import persist_jev_result
from aegis.schemas.evidence import JevResult
from aegis.schemas.proposal import TradeProposal

__all__ = ["persist_jev_result", "persist_trade_proposal"]


def persist_trade_proposal(session: Session, proposal: TradeProposal) -> TradeProposalRow:
    row = TradeProposalRow(
        id=proposal.proposal_id,
        correlation_id=proposal.correlation_id,
        exchange=proposal.instrument.exchange,
        market_type=proposal.instrument.market_type.value,
        symbol=proposal.instrument.symbol,
        timeframe=proposal.timeframe.value,
        strategy_id=proposal.strategy_id,
        strategy_version=proposal.strategy_version,
        action=proposal.action.value,
        direction=proposal.direction.value if proposal.direction is not None else None,
        entry_conditions=dict(proposal.entry_conditions),
        expires_at=proposal.expires_at,
        stop_loss=str(proposal.stop_loss) if proposal.stop_loss is not None else None,
        take_profit=str(proposal.take_profit) if proposal.take_profit is not None else None,
        sizing=dict(proposal.sizing),
        leverage=str(proposal.leverage) if proposal.leverage is not None else None,
        evidence_refs=list(proposal.evidence_refs),
        analyst_results=[item.model_dump(mode="json") for item in proposal.analyst_results],
        jev_result=proposal.jev_result.model_dump(mode="json"),
        uncertainty=dict(proposal.uncertainty),
        invalidation=dict(proposal.invalidation),
        supervisor_model_meta=dict(proposal.supervisor_model_meta),
        created_at=proposal.created_at,
    )
    session.add(row)
    return row


# Re-export for callers that prefer a single persist module.
def persist_jev(session: Session, result: JevResult) -> JevResultRow:
    return persist_jev_result(session, result)
