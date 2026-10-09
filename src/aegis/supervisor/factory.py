"""Helpers to build supervisor budgets / LLM call eligibility from Settings."""

from __future__ import annotations

from aegis.config.settings import Settings
from aegis.interfaces.llm import LlmPort, UnavailableLlmPort
from aegis.supervisor.budgets import BudgetConfig, budgets_allow_call
from aegis.supervisor.openai_compat import (
    SUPPORTED_PROVIDERS,
    OpenAICompatLlmPort,
    resolve_base_url,
)


def budget_config_from_settings(settings: Settings) -> BudgetConfig:
    return BudgetConfig(
        token_budget=settings.llm_token_budget,
        cost_budget=settings.llm_cost_budget,
        latency_budget_ms=settings.llm_latency_budget_ms,
    )


def build_llm_port(settings: Settings) -> LlmPort:
    """Build OpenAI-compatible port for owner-selected providers; else fail closed.

    Requires provider+key+model **and** positive budgets (cost may be 0) so a
    credentialed client is not held in memory when Supervisor cannot call LLM.
    """
    provider = settings.llm_provider.strip().lower()
    if not settings.llm_configured:
        return UnavailableLlmPort()
    if provider not in SUPPORTED_PROVIDERS:
        return UnavailableLlmPort()
    if not budgets_allow_call(budget_config_from_settings(settings)):
        return UnavailableLlmPort()

    try:
        base_url = resolve_base_url(provider, settings.llm_base_url or None)
    except ValueError:
        return UnavailableLlmPort()

    extra_headers: dict[str, str] = {}
    if provider == "openrouter":
        # OpenRouter asks for identifying headers; no secrets.
        referer = settings.llm_http_referer.strip()
        if referer:
            extra_headers["HTTP-Referer"] = referer
        title = settings.llm_app_title.strip() or "Aegis"
        extra_headers["X-Title"] = title

    return OpenAICompatLlmPort(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=base_url,
        provider=provider,
        timeout_seconds=settings.supervisor_timeout_seconds,
        extra_headers=extra_headers or None,
    )


def supervisor_may_call_llm(settings: Settings) -> bool:
    return settings.supervisor_may_call_llm
