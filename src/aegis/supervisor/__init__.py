"""LLM Supervisor — proposals only; never execution or credentials."""

from __future__ import annotations

from aegis.supervisor.budgets import BudgetConfig, budgets_allow_call
from aegis.supervisor.factory import budget_config_from_settings, build_llm_port
from aegis.supervisor.gates import evaluate_pre_llm_gates
from aegis.supervisor.persist import persist_jev_result, persist_trade_proposal
from aegis.supervisor.prompt import build_supervisor_prompt
from aegis.supervisor.supervise import no_trade_proposal, supervise

__all__ = [
    "BudgetConfig",
    "budget_config_from_settings",
    "budgets_allow_call",
    "build_llm_port",
    "build_supervisor_prompt",
    "evaluate_pre_llm_gates",
    "no_trade_proposal",
    "persist_jev_result",
    "persist_trade_proposal",
    "supervise",
]
