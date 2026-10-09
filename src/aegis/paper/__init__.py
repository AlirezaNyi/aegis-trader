"""Paper broker and simulated ledger (Phase 6). Never submits to Toobit."""

from aegis.paper.broker import PaperBroker
from aegis.paper.exceptions import (
    PaperBrokerError,
    PaperFillError,
    PaperLedgerKindError,
    PaperTradingBlocked,
)
from aegis.paper.fills import compute_fee, compute_fill_price
from aegis.paper.ledger import PaperLedger
from aegis.paper.persist import load_paper_ledger, persist_paper_ledger
from aegis.paper.reconcile import (
    PaperReconcileResult,
    persist_and_reload,
    reconcile_paper_ledgers,
    recover_from_snapshot,
)

__all__ = [
    "PaperBroker",
    "PaperBrokerError",
    "PaperFillError",
    "PaperLedger",
    "PaperLedgerKindError",
    "PaperReconcileResult",
    "PaperTradingBlocked",
    "compute_fee",
    "compute_fill_price",
    "load_paper_ledger",
    "persist_and_reload",
    "persist_paper_ledger",
    "reconcile_paper_ledgers",
    "recover_from_snapshot",
]
