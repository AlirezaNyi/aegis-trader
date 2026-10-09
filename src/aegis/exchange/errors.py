"""Exchange adapter errors — never treat timeout/5xx as definitive reject."""

from __future__ import annotations


class ExchangeError(RuntimeError):
    """Base error for Toobit execution adapter failures."""


class RateLimitError(ExchangeError):
    """HTTP 429 — caller should back off using reset_timestamp_ms when present."""

    def __init__(
        self,
        message: str,
        *,
        reset_timestamp_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reset_timestamp_ms = reset_timestamp_ms


class AmbiguousSubmitError(ExchangeError):
    """Submit timed out or received HTTP 5xx — order may have been accepted.

    Callers must reconcile by clientOrderId and must never blind-retry submit.
    """

    def __init__(self, message: str, *, client_order_id: str) -> None:
        super().__init__(message)
        self.client_order_id = client_order_id
