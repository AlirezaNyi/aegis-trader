"""Turn evidence into a TradeProposal from the configured decision source.

* ``llm``       — LLM Supervisor (existing behaviour).
* ``rules``     — deterministic RuleParams strategy; no LLM spend.
* ``rules+llm`` — rules propose; the LLM is called only for rule trade candidates
  (BUY/SELL) and may only veto: any disagreement is NO_TRADE, and rule levels are
  kept when the LLM agrees.

Pre-LLM gates apply in every mode. The Risk Engine remains the only APPROVE
authority. Rules identity goes into ``supervisor_model_meta``; ``strategy_id``
stays the allow-listed deps value.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aegis.interfaces.llm import LlmPort
from aegis.logging import get_logger
from aegis.schemas.evidence import EvidencePackage, JevResult
from aegis.schemas.proposal import ProposalAction, TradeProposal
from aegis.strategy.rules import STRATEGY_VERSION as RULES_VERSION
from aegis.strategy.rules import RuleParams, decide
from aegis.supervisor.budgets import BudgetConfig, DailyCallLimiter
from aegis.supervisor.decision import ProposalDefaults, proposal_from_decision
from aegis.supervisor.gates import evaluate_pre_llm_gates
from aegis.supervisor.supervise import no_trade_proposal, supervise

logger = get_logger("aegis.pipeline.decide")

DECISION_SOURCES = frozenset({"llm", "rules", "rules+llm"})


class RuleParamsUnavailable(RuntimeError):
    """Rule parameters could not be loaded; callers must fail closed."""


def load_rule_params(path: str | None) -> RuleParams:
    if not path:
        raise RuleParamsUnavailable("rule_params_file not set")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuleParamsUnavailable(f"rule params unreadable: {type(exc).__name__}") from exc
    if isinstance(data, dict) and isinstance(data.get("params"), dict):
        data = data["params"]  # walk-forward output wraps params with metadata
    if not isinstance(data, dict):
        raise RuleParamsUnavailable("rule params must be a JSON object")
    try:
        return RuleParams.from_dict(data)
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise RuleParamsUnavailable(f"rule params invalid: {type(exc).__name__}") from exc


def _with_meta(proposal: TradeProposal, extra: dict[str, Any]) -> TradeProposal:
    meta = dict(proposal.supervisor_model_meta)
    meta.update(extra)
    return proposal.model_copy(update={"supervisor_model_meta": meta})


async def make_proposal(
    package: EvidencePackage,
    jev_result: JevResult,
    *,
    decision_source: str,
    rule_params: RuleParams | None,
    llm: LlmPort,
    budgets: BudgetConfig | None,
    timeout_seconds: float,
    strategy_id: str,
    strategy_version: str,
    clock: Callable[[], datetime],
    defaults: ProposalDefaults,
    call_limiter: DailyCallLimiter | None,
) -> TradeProposal:
    async def _llm() -> TradeProposal:
        return await supervise(
            package,
            jev_result,
            llm,
            budgets=budgets,
            timeout_seconds=timeout_seconds,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
            defaults=defaults,
            call_limiter=call_limiter,
        )

    if decision_source == "llm":
        return await _llm()

    def _no_trade(reasons: list[str]) -> TradeProposal:
        return no_trade_proposal(
            package,
            jev_result,
            reasons=reasons,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
            supervisor_model_meta={"decision_source": decision_source},
        )

    if decision_source not in DECISION_SOURCES:
        return _no_trade(["decision_source_invalid"])
    if rule_params is None:
        return _no_trade(["rule_params_unavailable"])
    gate_reasons = evaluate_pre_llm_gates(package, jev_result)
    if gate_reasons:
        return _no_trade(gate_reasons)

    decision = decide(package, rule_params)
    rule_meta = {
        "decision_source": decision_source,
        "rule_strategy": RULES_VERSION,
        "rule_params_hash": rule_params.param_hash(),
    }
    rule_proposal = _with_meta(
        proposal_from_decision(
            decision,
            package,
            jev_result,
            defaults=defaults,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            now=clock(),
        ),
        rule_meta,
    )
    if rule_proposal.action not in {ProposalAction.BUY, ProposalAction.SELL}:
        # Machine-readable reason ("no_trade:<condition>") for review/journal.
        unc = dict(rule_proposal.uncertainty)
        unc.setdefault("reasons", [decision.rationale or "rules_no_trade"])
        return rule_proposal.model_copy(update={"uncertainty": unc})
    if decision_source == "rules":
        return rule_proposal

    # rules+llm: LLM may only veto a rule candidate; it never adds or replaces a
    # trade and never supplies levels. Any disagreement => NO_TRADE.
    llm_proposal = await _llm()
    if llm_proposal.action == rule_proposal.action:
        return _with_meta(rule_proposal, {"llm_veto": False})
    vetoed = no_trade_proposal(
        package,
        jev_result,
        reasons=["llm_veto"],
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        clock=clock,
        supervisor_model_meta={
            **rule_meta,
            "llm_veto": True,
            "llm_action": llm_proposal.action.value,
            "llm_reasons": list(llm_proposal.uncertainty.get("reasons") or []),
        },
    )
    return vetoed
