"""Shared Pydantic contracts for Aegis domain objects."""

from aegis.schemas.common import EvidenceStatus, LedgerKind, MarketType, Timeframe
from aegis.schemas.evidence import AnalystEvidence, AnalystType, EvidencePackage, JevResult
from aegis.schemas.market import Candle, InstrumentRef, NormalizedMarketEvent
from aegis.schemas.orders import Fill, Order, OrderIntent, OrderStatus, Position
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal
from aegis.schemas.risk import RiskDecision, RiskDecisionType

__all__ = [
    "AnalystEvidence",
    "AnalystType",
    "Candle",
    "EvidencePackage",
    "EvidenceStatus",
    "Fill",
    "InstrumentRef",
    "JevResult",
    "LedgerKind",
    "MarketType",
    "NormalizedMarketEvent",
    "Order",
    "OrderIntent",
    "OrderStatus",
    "Position",
    "ProposalAction",
    "RiskDecision",
    "RiskDecisionType",
    "Timeframe",
    "TradeDirection",
    "TradeProposal",
]
