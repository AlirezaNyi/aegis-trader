"""Withdrawal denial — Aegis must never call POST /api/v1/account/withdraw."""

from __future__ import annotations

from aegis.exchange.errors import ExchangeError
from aegis.exchange.paths import WITHDRAW_PATH


class WithdrawForbiddenError(ExchangeError):
    """Raised when any code path attempts a withdrawal endpoint."""


def assert_withdraw_forbidden(path: str) -> None:
    """Raise if ``path`` targets the verified withdrawal endpoint."""
    normalized = path.split("?", 1)[0].rstrip("/")
    if normalized == WITHDRAW_PATH.rstrip("/") or normalized.endswith(WITHDRAW_PATH):
        raise WithdrawForbiddenError(
            f"Aegis must never call {WITHDRAW_PATH}; refusing path={path!r}"
        )


def is_withdraw_path(path: str) -> bool:
    normalized = path.split("?", 1)[0].rstrip("/")
    return normalized == WITHDRAW_PATH.rstrip("/") or normalized.endswith(WITHDRAW_PATH)
