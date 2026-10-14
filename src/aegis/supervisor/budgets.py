"""Supervisor LLM budget gates — fail closed when budgets are unset or invalid."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    token_budget: int | None
    cost_budget: Decimal | None
    latency_budget_ms: int | None

    @property
    def all_configured(self) -> bool:
        return (
            self.token_budget is not None
            and self.cost_budget is not None
            and self.latency_budget_ms is not None
        )

    @property
    def all_positive(self) -> bool:
        """Token and latency must be > 0; cost may be 0 for free-tier providers."""
        token = self.token_budget
        cost = self.cost_budget
        latency = self.latency_budget_ms
        # Explicit None checks (not asserts): must fail closed under ``python -O``.
        if token is None or cost is None or latency is None:
            return False
        return token > 0 and cost >= 0 and latency > 0


def budgets_allow_call(budgets: BudgetConfig | None) -> bool:
    """Empty/None or invalid budgets mean fail closed: no real LLM call.

    ``cost_budget=0`` is allowed so free Groq/OpenRouter tiers can pass the gate
    when the adapter reports ``_meta.cost=0``.
    """
    if budgets is None:
        return False
    return budgets.all_positive


class DailyCallLimiter:
    """Owner-set cap on real LLM calls per UTC calendar day (thread-safe).

    ``now`` is always passed in (cycle clock) so behaviour is deterministic in
    tests. The counter resets when the UTC date of ``now`` differs from the
    tracked day. Only successful ``try_acquire`` calls are counted; a denied
    acquire does not consume quota. The cap value is owner-set — no default.
    """

    def __init__(self, max_calls: int) -> None:
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls <= 0:
            msg = "DailyCallLimiter max_calls must be a positive int"
            raise ValueError(msg)
        self._max_calls = max_calls
        self._lock = threading.Lock()
        self._day: date | None = None
        self._used = 0

    @staticmethod
    def _utc_day(now: datetime) -> date:
        if now.tzinfo is None:
            msg = "DailyCallLimiter requires a timezone-aware datetime"
            raise ValueError(msg)
        return now.astimezone(UTC).date()

    def _roll(self, day: date) -> None:
        if self._day != day:
            self._day = day
            self._used = 0

    @property
    def max_calls(self) -> int:
        return self._max_calls

    @property
    def used(self) -> int:
        """Calls acquired on the most recently observed UTC day."""
        with self._lock:
            return self._used

    def try_acquire(self, now: datetime) -> bool:
        day = self._utc_day(now)
        with self._lock:
            self._roll(day)
            if self._used >= self._max_calls:
                return False
            self._used += 1
            return True

    def remaining(self, now: datetime) -> int:
        day = self._utc_day(now)
        with self._lock:
            if self._day != day:
                return self._max_calls
            return max(self._max_calls - self._used, 0)
