"""Shared Pydantic contracts for Aegis domain objects."""

from aegis.schemas.backtest import BacktestRunReport, SplitWindows, WindowMetrics
from aegis.schemas.common import EvidenceStatus, LedgerKind, MarketType, Timeframe
from aegis.schemas.evidence import AnalystEvidence, AnalystType, EvidencePackage, JevResult
from aegis.schemas.features import FeatureSnapshot, FeatureValue, FeatureValueStatus
from aegis.schemas.market import Candle, InstrumentRef, NormalizedMarketEvent
from aegis.schemas.metadata import SymbolMetadata
from aegis.schemas.orders import Fill, Order, OrderIntent, OrderStatus, Position
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal
from aegis.schemas.risk import RiskDecision, RiskDecisionType

__all__ = [
    "AnalystEvidence",
    "AnalystType",
    "BacktestRunReport",
    "Candle",
    "EvidencePackage",
    "EvidenceStatus",
    "FeatureSnapshot",
    "FeatureValue",
    "FeatureValueStatus",
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
    "SplitWindows",
    "SymbolMetadata",
    "Timeframe",
    "TradeDirection",
    "TradeProposal",
    "WindowMetrics",
]
