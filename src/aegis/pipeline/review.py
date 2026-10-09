"""Human-readable paper-cycle review summaries (no secrets)."""

from __future__ import annotations

from typing import Any

from aegis.pipeline.cycle import PaperCycleResult
from aegis.pipeline.soak import SoakPollOutcome


def summarize_paper_cycle(
    result: PaperCycleResult,
    *,
    detail: bool = False,
) -> dict[str, Any]:
    """Build a review payload for owner inspection of bot suggestions."""
    proposal = result.proposal
    decision = result.decision
    order = result.order

    proposal_block: dict[str, Any] | None = None
    if proposal is not None:
        direction = None if proposal.direction is None else proposal.direction.value
        action = (
            proposal.action.value
            if hasattr(proposal.action, "value")
            else str(proposal.action)
        )
        proposal_block = {
            "action": action,
            "direction": direction,
            "symbol": proposal.instrument.symbol,
            "market_type": proposal.instrument.market_type.value,
            "timeframe": proposal.timeframe.value,
            "strategy_id": proposal.strategy_id,
            "strategy_version": proposal.strategy_version,
            "stop_loss": None if proposal.stop_loss is None else str(proposal.stop_loss),
            "take_profit": None if proposal.take_profit is None else str(proposal.take_profit),
            "leverage": None if proposal.leverage is None else str(proposal.leverage),
            "sizing": dict(proposal.sizing),
            "entry_conditions": dict(proposal.entry_conditions),
            "uncertainty": dict(proposal.uncertainty),
            "invalidation": dict(proposal.invalidation),
            "expires_at": proposal.expires_at.isoformat(),
            "supervisor_model_meta": {
                k: v
                for k, v in dict(proposal.supervisor_model_meta).items()
                if k.lower() not in {"api_key", "authorization", "secret"}
            },
        }

    decision_block: dict[str, Any] | None = None
    if decision is not None:
        decision_block = {
            "decision": decision.decision.value,
            "policy_version": decision.policy_version,
            "rejection_reasons": list(decision.rejection_reasons),
            "validated_order_params": decision.validated_order_params,
            "rules_evaluated": list(decision.rules_evaluated),
            "decided_at": decision.decided_at.isoformat(),
            "expires_at": decision.expires_at.isoformat(),
        }

    order_block: dict[str, Any] | None = None
    if order is not None:
        order_block = {
            "order_id": str(order.order_id),
            "client_order_id": order.client_order_id,
            "status": order.status.value if hasattr(order.status, "value") else str(order.status),
            "ledger_kind": (
                order.ledger_kind.value
                if hasattr(order.ledger_kind, "value")
                else str(order.ledger_kind)
            ),
        }

    body: dict[str, Any] = {
        "correlation_id": result.correlation_id,
        "validation_ok": result.validation.ok,
        "validation_block_reasons": list(result.validation.block_reasons),
        "proposal": proposal_block,
        "risk": decision_block,
        "paper_order": order_block,
        "blocked_reason": result.blocked_reason,
        "owner_hint": _owner_hint(result),
    }
    if detail:
        body["analysts"] = _summarize_analysts(proposal)
        body["jev"] = _summarize_jev(proposal)
    return body


def _summarize_analysts(proposal: Any) -> list[dict[str, Any]]:
    if proposal is None:
        return []
    rows: list[dict[str, Any]] = []
    for ev in list(getattr(proposal, "analyst_results", []) or []):
        payload = dict(getattr(ev, "payload", {}) or {})
        # Keep descriptive labels only; drop bulky series if present.
        compact = {
            k: v
            for k, v in payload.items()
            if k.lower() not in {"api_key", "authorization", "secret"}
            and not isinstance(v, list)
        }
        rows.append(
            {
                "analyst_type": (
                    ev.analyst_type.value
                    if hasattr(ev.analyst_type, "value")
                    else str(ev.analyst_type)
                ),
                "status": (
                    ev.status.value if hasattr(ev.status, "value") else str(ev.status)
                ),
                "notes": getattr(ev, "notes", None),
                "payload": compact,
                "not_an_order_signal": True,
            }
        )
    return rows


def _summarize_jev(proposal: Any) -> dict[str, Any] | None:
    if proposal is None:
        return None
    jev = getattr(proposal, "jev_result", None)
    if jev is None:
        return None
    answers = dict(getattr(jev, "answers", {}) or {})
    return {
        "status": jev.status.value if hasattr(jev.status, "value") else str(jev.status),
        "answers": {
            k: v
            for k, v in answers.items()
            if k.lower() not in {"api_key", "authorization", "secret"}
        },
        "confidence_notes": getattr(jev, "confidence_notes", None),
        "latency_ms": getattr(jev, "latency_ms", None),
        "model": getattr(jev, "model", None),
    }


def summarize_soak_outcome(outcome: SoakPollOutcome) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ran_cycle": outcome.ran_cycle,
        "skipped_reason": outcome.skipped_reason,
        "symbol": outcome.symbol,
        "final_open_time": (
            None
            if outcome.final_open_time is None
            else outcome.final_open_time.isoformat()
        ),
    }
    if outcome.result is not None:
        body["cycle"] = summarize_paper_cycle(outcome.result)
    else:
        body["cycle"] = None
    return body


def format_review_text(summary: dict[str, Any]) -> str:
    """CLI-friendly multi-line review (no secrets)."""
    lines: list[str] = []
    if "ran_cycle" in summary:
        lines.append(
            f"ran_cycle={summary.get('ran_cycle')} "
            f"symbol={summary.get('symbol')} "
            f"skipped={summary.get('skipped_reason')} "
            f"final_open={summary.get('final_open_time')}"
        )
        cycle = summary.get("cycle")
        if not isinstance(cycle, dict):
            return "\n".join(lines)
    else:
        cycle = summary

    lines.append(f"correlation_id={cycle.get('correlation_id')}")
    lines.append(
        f"validation_ok={cycle.get('validation_ok')} "
        f"blocks={cycle.get('validation_block_reasons')}"
    )
    prop = cycle.get("proposal")
    if isinstance(prop, dict):
        lines.append(
            "SUGGESTION "
            f"action={prop.get('action')} direction={prop.get('direction')} "
            f"symbol={prop.get('symbol')} tf={prop.get('timeframe')} "
            f"stop={prop.get('stop_loss')} tp={prop.get('take_profit')} "
            f"sizing={prop.get('sizing')}"
        )
        unc = prop.get("uncertainty") or {}
        if isinstance(unc, dict) and unc.get("reasons"):
            lines.append(f"suggestion_reasons={unc.get('reasons')}")
    else:
        lines.append("SUGGESTION none")

    risk = cycle.get("risk")
    if isinstance(risk, dict):
        lines.append(
            f"RISK decision={risk.get('decision')} "
            f"policy={risk.get('policy_version')} "
            f"reject={risk.get('rejection_reasons')}"
        )
    else:
        lines.append("RISK none")

    order = cycle.get("paper_order")
    if isinstance(order, dict):
        lines.append(
            f"PAPER_ORDER id={order.get('order_id')} "
            f"status={order.get('status')} ledger={order.get('ledger_kind')}"
        )
    else:
        lines.append(f"PAPER_ORDER none blocked={cycle.get('blocked_reason')}")

    hint = cycle.get("owner_hint")
    if hint:
        lines.append(f"HINT {hint}")
    return "\n".join(lines)


def _owner_hint(result: PaperCycleResult) -> str:
    if result.proposal is None:
        return "No proposal — check validation / mode gates."
    action = result.proposal.action.value
    if action == "NO_TRADE":
        reasons = result.proposal.uncertainty.get("reasons")
        return (
            f"Bot suggests NO_TRADE (reasons={reasons}). "
            "Not a buy/sell candidate; Risk REJECT on RP-ACTION is expected."
        )
    if result.decision is None:
        return f"Bot suggests {action} but Risk did not run."
    if result.decision.decision.value == "APPROVE":
        if result.order is not None:
            return (
                f"Bot suggests {action}; Risk APPROVED; paper fill recorded "
                "(simulated — not sent to Toobit)."
            )
        return (
            f"Bot suggests {action}; Risk APPROVED but paper order missing "
            f"(blocked={result.blocked_reason})."
        )
    return (
        f"Bot suggests {action}; Risk REJECTED "
        f"({result.decision.rejection_reasons}). Review policy vs suggestion."
    )
