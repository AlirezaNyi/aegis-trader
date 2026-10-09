"""Supervisor LLM budget gates — fail closed when budgets are unset or invalid."""

from __future__ import annotations

from dataclasses import dataclass
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
        if not self.all_configured:
            return False
        assert self.token_budget is not None
        assert self.cost_budget is not None
        assert self.latency_budget_ms is not None
        return (
            self.token_budget > 0
            and self.cost_budget >= 0
            and self.latency_budget_ms > 0
        )


def budgets_allow_call(budgets: BudgetConfig | None) -> bool:
    """Empty/None or invalid budgets mean fail closed: no real LLM call.

    ``cost_budget=0`` is allowed so free Groq/OpenRouter tiers can pass the gate
    when the adapter reports ``_meta.cost=0``.
    """
    if budgets is None:
        return False
    return budgets.all_positive
