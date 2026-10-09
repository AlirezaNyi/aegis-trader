"""Deterministic pre-LLM gates. Prefer NO_TRADE when critical evidence is weak."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import AnalystEvidence, AnalystType, EvidencePackage, JevResult

_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "system:",
    "you must buy",
    "you must sell",
    "disregard prior",
    "override safety",
)

_CRITICAL_ANALYSTS = frozenset({AnalystType.TECHNICAL, AnalystType.QUANTITATIVE})


def _by_type(package: EvidencePackage) -> dict[AnalystType, AnalystEvidence]:
    return {item.analyst_type: item for item in package.analyst_evidence}


def _technical_direction(payload: dict[str, Any]) -> str | None:
    trend = payload.get("trend")
    if isinstance(trend, str):
        normalized = trend.strip().lower()
        if normalized in {"up", "down", "flat"}:
            return normalized
    return None


def _quantitative_direction(payload: dict[str, Any]) -> str | None:
    direction = payload.get("direction")
    if isinstance(direction, str):
        normalized = direction.strip().lower()
        if normalized in {"up", "down", "flat", "long", "short"}:
            if normalized == "long":
                return "up"
            if normalized == "short":
                return "down"
            return normalized

    for key in ("mean_return_sign", "mean_simple_return", "mean_return"):
        raw = payload.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered in {"positive", "+", "up", "long"}:
                return "up"
            if lowered in {"negative", "-", "down", "short"}:
                return "down"
            if lowered in {"zero", "flat", "0"}:
                return "flat"
            try:
                value = Decimal(raw)
            except (InvalidOperation, ValueError):
                continue
        elif isinstance(raw, (int, float, Decimal)):
            value = Decimal(str(raw))
        else:
            continue
        if value > 0:
            return "up"
        if value < 0:
            return "down"
        return "flat"
    return None


def _shared_analyst_direction(
    technical: AnalystEvidence | None,
    quantitative: AnalystEvidence | None,
) -> str | None:
    tech_dir = (
        _technical_direction(technical.payload)
        if technical is not None and technical.status == EvidenceStatus.OK
        else None
    )
    quant_dir = (
        _quantitative_direction(quantitative.payload)
        if quantitative is not None and quantitative.status == EvidenceStatus.OK
        else None
    )
    if tech_dir is None or quant_dir is None:
        return None
    if tech_dir == "flat" or quant_dir == "flat":
        return None
    if tech_dir == quant_dir:
        return tech_dir
    return None


def _alignment_choice(answers: dict[str, Any]) -> str | None:
    alignment = answers.get("alignment")
    if isinstance(alignment, dict):
        for key in ("choice", "answer", "value", "selected"):
            value = alignment.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        # Highest-probability option if present.
        probs = alignment.get("probabilities") or alignment.get("probs")
        if isinstance(probs, dict) and probs:
            best = max(probs.items(), key=lambda item: float(item[1]))
            return str(best[0]).strip().lower()
    if isinstance(alignment, str) and alignment.strip():
        return alignment.strip().lower()
    return None


def _evidence_text_blob(evidence: AnalystEvidence) -> str:
    """Flatten notes, sources, and common payload text fields for injection scan."""
    parts: list[str] = []
    if evidence.notes:
        parts.append(evidence.notes)
    for source in evidence.sources:
        parts.append(str(source))
    payload = evidence.payload
    for key in ("items", "headlines", "text", "summary", "raw", "body", "title"):
        value = payload.get(key)
        if value is not None:
            parts.append(str(value))
    # Also scan string payload values (covers unexpected free-text fields).
    for value in payload.values():
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts).lower()


def _has_injection_markers(text: str) -> bool:
    return any(marker in text for marker in _INJECTION_MARKERS)


def evaluate_pre_llm_gates(
    package: EvidencePackage,
    jev_result: JevResult,
) -> list[str]:
    """Return reason codes; non-empty means prefer/force NO_TRADE."""
    reasons: list[str] = []
    by_type = _by_type(package)

    for analyst_type in _CRITICAL_ANALYSTS:
        evidence = by_type.get(analyst_type)
        if evidence is None:
            reasons.append(f"missing_{analyst_type.value}")
            continue
        if evidence.status in {
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.ERROR,
        }:
            reasons.append(f"{analyst_type.value}_{evidence.status.value}")

    technical = by_type.get(AnalystType.TECHNICAL)
    quantitative = by_type.get(AnalystType.QUANTITATIVE)
    if (
        technical is not None
        and technical.status == EvidenceStatus.OK
        and quantitative is not None
        and quantitative.status == EvidenceStatus.OK
    ):
        tech_dir = _technical_direction(technical.payload)
        quant_dir = _quantitative_direction(quantitative.payload)
        if (
            tech_dir is not None
            and quant_dir is not None
            and tech_dir != "flat"
            and quant_dir != "flat"
            and tech_dir != quant_dir
        ):
            reasons.append("hard_direction_contradiction_technical_quantitative")

    if jev_result.status == EvidenceStatus.OK:
        alignment = _alignment_choice(jev_result.answers)
        shared = _shared_analyst_direction(technical, quantitative)
        if alignment is not None and shared is not None:
            if shared == "up" and alignment == "aligned_short":
                reasons.append("jev_alignment_contradicts_analyst_direction")
            elif shared == "down" and alignment == "aligned_long":
                reasons.append("jev_alignment_contradicts_analyst_direction")

    for evidence in package.analyst_evidence:
        blob = _evidence_text_blob(evidence)
        if _has_injection_markers(blob):
            reasons.append(
                f"prompt_injection_markers_in_{evidence.analyst_type.value}"
            )

    return reasons
