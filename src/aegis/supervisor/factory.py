"""Helpers to build supervisor budgets / LLM call eligibility from Settings."""

from __future__ import annotations

from aegis.config.settings import Settings
from aegis.interfaces.llm import LlmPort, UnavailableLlmPort
from aegis.supervisor.budgets import BudgetConfig


def budget_config_from_settings(settings: Settings) -> BudgetConfig:
    return BudgetConfig(
        token_budget=settings.llm_token_budget,
        cost_budget=settings.llm_cost_budget,
        latency_budget_ms=settings.llm_latency_budget_ms,
    )


def build_llm_port(settings: Settings) -> LlmPort:
    """Phase 4: concrete providers remain UNAPPROVED — always fail closed here."""
    _ = settings
    return UnavailableLlmPort()


def supervisor_may_call_llm(settings: Settings) -> bool:
    return settings.supervisor_may_call_llm
