"""Paper broker error types."""

from __future__ import annotations


class PaperBrokerError(RuntimeError):
    """Base error for paper simulation failures."""


class PaperLedgerKindError(PaperBrokerError):
    """Raised when a non-paper ledger_kind is offered to the paper broker."""


class PaperTradingBlocked(PaperBrokerError):
    """Raised when kill switch or mode blocks new paper orders."""


class PaperFillError(PaperBrokerError):
    """Raised when a fill cannot be priced or applied."""
