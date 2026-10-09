"""LLM Supervisor pipeline — schema-in / schema-out, fail closed to NO_TRADE."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from aegis.interfaces.llm import LlmPort, UnavailableLlmPort
from aegis.schemas.evidence import EvidencePackage, JevResult
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal
from aegis.supervisor.budgets import BudgetConfig, budgets_allow_call
from aegis.supervisor.gates import evaluate_pre_llm_gates
from aegis.supervisor.prompt import build_supervisor_prompt

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def no_trade_proposal(
    package: EvidencePackage,
    jev_result: JevResult,
    *,
    reasons: list[str],
    strategy_id: str = "aegis-default",
    strategy_version: str = "0.0.0-phase4",
    clock: Clock | None = None,
    supervisor_model_meta: dict[str, Any] | None = None,
    uncertainty_extra: dict[str, Any] | None = None,
) -> TradeProposal:
    """Construct a consistent fail-closed NO_TRADE proposal."""
    now = (clock or _utcnow)()
    uncertainty: dict[str, Any] = {
        "reasons": list(reasons),
        "prefer_no_trade": True,
        "model_confidence_uncalibrated": True,
    }
    if uncertainty_extra:
        uncertainty.update(uncertainty_extra)
    return TradeProposal(
        proposal_id=uuid4(),
        correlation_id=package.correlation_id,
        instrument=package.instrument,
        direction=TradeDirection.FLAT,
        timeframe=package.timeframe,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        action=ProposalAction.NO_TRADE,
        entry_conditions={},
        expires_at=now + timedelta(minutes=5),
        stop_loss=None,
        take_profit=None,
        sizing={},
        leverage=None,
        evidence_refs=list(package.feature_refs),
        analyst_results=list(package.analyst_evidence),
        jev_result=jev_result,
        uncertainty=uncertainty,
        invalidation={"reasons": list(reasons), "gate": "supervisor_fail_closed"},
        supervisor_model_meta=dict(supervisor_model_meta or {}),
        created_at=package.created_at,
    )


def _usage_tokens(meta: dict[str, Any] | None) -> int | None:
    if not meta:
        return None
    for key in ("total_tokens", "tokens", "prompt_tokens"):
        value = meta.get(key)
        if isinstance(value, int):
            if key == "prompt_tokens":
                completion = meta.get("completion_tokens")
                if isinstance(completion, int):
                    return value + completion
            return value
        if isinstance(value, float):
            return int(value)
    usage = meta.get("usage")
    if isinstance(usage, dict):
        return _usage_tokens(usage)
    return None


def _overwrite_identity(
    proposal: TradeProposal,
    package: EvidencePackage,
    jev_result: JevResult,
    *,
    strategy_id: str,
    strategy_version: str,
) -> TradeProposal:
    data = proposal.model_dump(mode="python")
    data["proposal_id"] = uuid4()
    data["correlation_id"] = package.correlation_id
    data["instrument"] = package.instrument
    data["timeframe"] = package.timeframe
    data["analyst_results"] = list(package.analyst_evidence)
    data["jev_result"] = jev_result
    data["created_at"] = package.created_at
    data["strategy_id"] = strategy_id
    data["strategy_version"] = strategy_version
    uncertainty = dict(data.get("uncertainty") or {})
    uncertainty["model_confidence_uncalibrated"] = True
    data["uncertainty"] = uncertainty
    return TradeProposal.model_validate(data)


def supervise(
    package: EvidencePackage,
    jev_result: JevResult,
    llm: LlmPort,
    *,
    budgets: BudgetConfig | None = None,
    timeout_seconds: float = 15.0,
    strategy_id: str = "aegis-default",
    strategy_version: str = "0.0.0-phase4",
    clock: Clock | None = None,
) -> TradeProposal:
    """Run deterministic gates then optional structured LLM completion."""
    gate_reasons = evaluate_pre_llm_gates(package, jev_result)
    if gate_reasons:
        return no_trade_proposal(
            package,
            jev_result,
            reasons=gate_reasons,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
        )

    if not budgets_allow_call(budgets):
        return no_trade_proposal(
            package,
            jev_result,
            reasons=["supervisor_budgets_not_configured"],
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
        )

    if isinstance(llm, UnavailableLlmPort):
        return no_trade_proposal(
            package,
            jev_result,
            reasons=["llm_provider_unavailable"],
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
        )

    assert budgets is not None  # budgets_allow_call already verified
    prompt = build_supervisor_prompt(package, jev_result)
    started = time.perf_counter()
    try:
        raw = llm.complete_structured(
            prompt=prompt,
            schema_name="TradeProposal",
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — fail closed on any provider failure
        return no_trade_proposal(
            package,
            jev_result,
            reasons=["llm_call_failed", type(exc).__name__],
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
            supervisor_model_meta={"error": type(exc).__name__},
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    meta: dict[str, Any] = {}
    payload = raw
    if isinstance(raw, dict) and "_meta" in raw:
        maybe_meta = raw.get("_meta")
        if isinstance(maybe_meta, dict):
            meta = dict(maybe_meta)
        payload = {k: v for k, v in raw.items() if k != "_meta"}

    if budgets.latency_budget_ms is not None and latency_ms > budgets.latency_budget_ms:
        return no_trade_proposal(
            package,
            jev_result,
            reasons=["llm_latency_budget_exceeded"],
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
            supervisor_model_meta={"latency_ms": latency_ms, **meta},
        )

    tokens = _usage_tokens(meta)
    if budgets.token_budget is not None and tokens is None:
        return no_trade_proposal(
            package,
            jev_result,
            reasons=["llm_token_usage_missing"],
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
            supervisor_model_meta={"latency_ms": latency_ms, **meta},
        )
    if (
        tokens is not None
        and budgets.token_budget is not None
        and tokens > budgets.token_budget
    ):
        return no_trade_proposal(
            package,
            jev_result,
            reasons=["llm_token_budget_exceeded"],
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
            supervisor_model_meta={"latency_ms": latency_ms, "tokens": tokens, **meta},
        )

    cost_raw = meta.get("cost") if meta else None
    if budgets.cost_budget is not None and cost_raw is None:
        return no_trade_proposal(
            package,
            jev_result,
            reasons=["llm_cost_usage_missing"],
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
            supervisor_model_meta={"latency_ms": latency_ms, **meta},
        )
    if cost_raw is not None and budgets.cost_budget is not None:
        try:
            cost_value = Decimal(str(cost_raw))
        except Exception:  # noqa: BLE001
            return no_trade_proposal(
                package,
                jev_result,
                reasons=["llm_cost_usage_invalid"],
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                clock=clock,
                supervisor_model_meta={"latency_ms": latency_ms, **meta},
            )
        if cost_value > budgets.cost_budget:
            return no_trade_proposal(
                package,
                jev_result,
                reasons=["llm_cost_budget_exceeded"],
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                clock=clock,
                supervisor_model_meta={
                    "latency_ms": latency_ms,
                    "cost": str(cost_value),
                    **meta,
                },
            )

    try:
        proposal = TradeProposal.model_validate(payload)
    except ValidationError:
        return no_trade_proposal(
            package,
            jev_result,
            reasons=["llm_output_schema_invalid"],
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            clock=clock,
            supervisor_model_meta={"latency_ms": latency_ms, **meta},
        )

    proposal = _overwrite_identity(
        proposal,
        package,
        jev_result,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
    )
    meta_out = dict(proposal.supervisor_model_meta)
    meta_out.update({"latency_ms": latency_ms, **meta})
    return proposal.model_copy(update={"supervisor_model_meta": meta_out})
