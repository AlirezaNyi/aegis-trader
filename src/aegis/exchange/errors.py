"""Exchange adapter errors — never treat timeout/5xx as definitive reject."""

from __future__ import annotations

# Toobit Spot error codes (https://api-docs.toobit.com/api/spot-error-codes,
# read 2026-10-13). For a *submit*, these leave execution status unknown or say an
# order with this clientOrderId already exists, so they are AMBIGUOUS (reconcile),
# never a definitive reject:
#   -1000 UNKNOWN, -1001 DISCONNECTED, -1006 UNEXPECTED_RESP ("Execution status
#   unknown"), -1007 TIMEOUT ("execution status unknown"), -1141 ORDER_DUPLICATED,
#   -1146 ORDER_CREATION_TIMEOUT.
AMBIGUOUS_SUBMIT_ERROR_CODES: frozenset[int] = frozenset(
    {-1000, -1001, -1006, -1007, -1141, -1146}
)


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


class RateLimitedSubmitError(AmbiguousSubmitError):
    """HTTP 429 on an order *submit*.

    Toobit does not document that a 429 guarantees the order was not accepted, so a
    rate-limited submit is AMBIGUOUS (UNKNOWN + reconcile), never a definitive
    reject. ``reset_timestamp_ms`` comes from ``X-Api-Limit-Reset-Timestamp``.
    """

    def __init__(
        self,
        message: str,
        *,
        client_order_id: str,
        reset_timestamp_ms: int | None = None,
    ) -> None:
        super().__init__(message, client_order_id=client_order_id)
        self.reset_timestamp_ms = reset_timestamp_ms


class ExchangeRejectError(ExchangeError):
    """Definitive reject: the exchange (or a local pre-submit guard) did not accept
    the order. Safe to mark REJECTED; never retried automatically.

    ``msg`` is the exchange-provided short message (never contains secrets); it is
    truncated by the adapter before construction.
    """

    def __init__(
        self,
        message: str,
        *,
        client_order_id: str | None = None,
        http_status: int | None = None,
        code: int | None = None,
        msg: str | None = None,
    ) -> None:
        super().__init__(message)
        self.client_order_id = client_order_id
        self.http_status = http_status
        self.code = code
        self.msg = msg


class OrderPrecisionError(ExchangeRejectError):
    """Local pre-submit precision/filter guard refused the order (nothing sent)."""


class OrderTestRejectError(ExchangeRejectError):
    """Spot ``orderTest`` (validation-only) refused the order.

    ``orderTest`` never reaches the matching engine, so this is always definitive
    regardless of HTTP status or body shape.
    """


def is_definitive_submit_reject(exc: ExchangeRejectError) -> bool:
    """True only when a submit reject proves no order can exist on the exchange.

    Definitive:
    * local pre-submit guards (``OrderPrecisionError``, ``OrderTestRejectError``,
      or a reject with neither ``http_status`` nor ``code`` — nothing was sent);
    * a parseable Toobit error ``code`` not in ``AMBIGUOUS_SUBMIT_ERROR_CODES``
      under a non-429, non-5xx HTTP status.

    Everything else (HTTP 429, 5xx, 4xx without a parseable code, execution-unknown
    codes) is AMBIGUOUS: mark UNKNOWN and reconcile, never blind-retry.
    """
    if isinstance(exc, (OrderPrecisionError, OrderTestRejectError)):
        return True
    status = exc.http_status
    if exc.code is None:
        return status is None
    if exc.code in AMBIGUOUS_SUBMIT_ERROR_CODES:
        return False
    return not (status is not None and (status == 429 or status >= 500))
