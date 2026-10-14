"""Paper broker and simulated ledger (Phase 6). Never submits to Toobit."""

from aegis.paper.broker import PaperBroker
from aegis.paper.equity import (
    LossPeriod,
    PaperEquitySnapshot,
    PaperEquityTracker,
    ProposalHistory,
    compute_paper_equity,
    instrument_mark_key,
    next_period_start,
    period_start,
)
from aegis.paper.exceptions import (
    PaperBrokerError,
    PaperFillError,
    PaperLedgerKindError,
    PaperTradingBlocked,
)
from aegis.paper.exits import ExitFill, apply_exits, evaluate_exit_candle, stop_fill_price
from aegis.paper.fills import compute_fee, compute_fill_price
from aegis.paper.ledger import PaperLedger, instrument_key_for
from aegis.paper.persist import load_paper_ledger, persist_paper_ledger
from aegis.paper.protection import ClosedTrade, ExitReason, ProtectiveLevels
from aegis.paper.reconcile import (
    PaperReconcileResult,
    persist_and_reload,
    reconcile_paper_ledgers,
    recover_from_snapshot,
)
from aegis.paper.state_store import (
    PaperStateBackend,
    PaperStateLoad,
    PaperStateStore,
    SqlPaperStateBackend,
    StoredPaperState,
)

__all__ = [
    "ClosedTrade",
    "ExitFill",
    "ExitReason",
    "LossPeriod",
    "PaperBroker",
    "PaperBrokerError",
    "PaperEquitySnapshot",
    "PaperEquityTracker",
    "PaperFillError",
    "PaperLedger",
    "PaperLedgerKindError",
    "PaperReconcileResult",
    "PaperStateBackend",
    "PaperStateLoad",
    "PaperStateStore",
    "PaperTradingBlocked",
    "ProposalHistory",
    "ProtectiveLevels",
    "SqlPaperStateBackend",
    "StoredPaperState",
    "apply_exits",
    "compute_fee",
    "compute_fill_price",
    "compute_paper_equity",
    "evaluate_exit_candle",
    "instrument_key_for",
    "instrument_mark_key",
    "load_paper_ledger",
    "next_period_start",
    "period_start",
    "persist_and_reload",
    "persist_paper_ledger",
    "reconcile_paper_ledgers",
    "recover_from_snapshot",
    "stop_fill_price",
]
