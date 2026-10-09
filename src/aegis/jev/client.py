"""Official TypeSafe System One HTTP adapter implementing JevPort."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from aegis.jev.constants import CONFIDENCE_NOTES, SYSTEMONE_URL
from aegis.jev.questions import build_systemone_questions
from aegis.jev.redact import redact_evidence_package
from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import EvidencePackage, JevResult

_RETRYABLE_STATUS = frozenset({429, 529})


def _extract_answers(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull answers from verified-shaped or defensively nested payloads."""
    if "answers" in payload and isinstance(payload["answers"], dict):
        return payload["answers"]
    result = payload.get("result")
    if isinstance(result, dict):
        nested = result.get("answers")
        if isinstance(nested, dict):
            return nested
    known = set(build_systemone_questions())
    if known.intersection(payload.keys()):
        return {k: payload[k] for k in known if k in payload}
    return None


def _normalize_answers(raw: Any) -> dict[str, Any]:
    """Normalize System One answers; preserve confidence as distribution concentration."""
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            entry = dict(value)
            # Keep confidence if present; never relabel as profit probability.
            if "confidence" in entry and "confidence_meaning" not in entry:
                entry["confidence_meaning"] = (
                    "distribution_concentration_not_profit_probability"
                )
            normalized[str(key)] = entry
        else:
            normalized[str(key)] = value
    return normalized


def _unavailable(
    package: EvidencePackage,
    *,
    model: str | None,
    latency_ms: int,
    usage: dict[str, Any] | None = None,
) -> JevResult:
    return JevResult(
        evidence_package_id=package.package_id,
        model=model,
        status=EvidenceStatus.UNAVAILABLE,
        answers={},
        usage=dict(usage or {}),
        latency_ms=latency_ms,
        confidence_notes=CONFIDENCE_NOTES,
    )


def _error(
    package: EvidencePackage,
    *,
    model: str | None,
    latency_ms: int,
    usage: dict[str, Any] | None = None,
    answers: dict[str, Any] | None = None,
) -> JevResult:
    return JevResult(
        evidence_package_id=package.package_id,
        model=model,
        status=EvidenceStatus.ERROR,
        answers=dict(answers or {}),
        usage=dict(usage or {}),
        latency_ms=latency_ms,
        confidence_notes=CONFIDENCE_NOTES,
    )


class TypesafeSystemOneClient:
    """httpx-backed System One client. Inject ``http_client`` for tests."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "jev-latest",
        timeout_seconds: float = 10.0,
        max_retries: int = 1,
        http_client: httpx.Client | None = None,
        base_url: str = SYSTEMONE_URL,
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model
        self._timeout_seconds = timeout_seconds
        # Cap at one retry (verified constraint vs evidence freshness).
        self._max_retries = min(max(max_retries, 0), 1)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._base_url = base_url

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> TypesafeSystemOneClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def evaluate(self, package: EvidencePackage) -> JevResult:
        if not self._api_key:
            return _unavailable(package, model=None, latency_ms=0)

        redacted = redact_evidence_package(package)
        state = json.dumps(redacted, separators=(",", ":"), default=str)
        body: dict[str, Any] = {
            "state": state,
            "model": self._model,
            "questions": build_systemone_questions(),
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Correlation-Id": package.correlation_id,
        }

        started = time.perf_counter()
        deadline = started + self._timeout_seconds
        attempts = 0
        max_attempts = 1 + self._max_retries

        while attempts < max_attempts:
            attempts += 1
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                latency_ms = int((time.perf_counter() - started) * 1000)
                return _unavailable(package, model=self._model, latency_ms=latency_ms)

            try:
                response = self._client.post(
                    self._base_url,
                    json=body,
                    headers=headers,
                    timeout=remaining,
                )
            except httpx.TimeoutException:
                latency_ms = int((time.perf_counter() - started) * 1000)
                return _unavailable(package, model=self._model, latency_ms=latency_ms)
            except httpx.HTTPError:
                latency_ms = int((time.perf_counter() - started) * 1000)
                return _error(package, model=self._model, latency_ms=latency_ms)

            latency_ms = int((time.perf_counter() - started) * 1000)
            status_code = response.status_code

            if status_code in _RETRYABLE_STATUS:
                # One retry only, and only while still inside the transport deadline
                # (proxy for evidence freshness window; no owner-approved TTL yet).
                if attempts < max_attempts and (deadline - time.perf_counter()) > 0:
                    continue
                return _unavailable(package, model=self._model, latency_ms=latency_ms)

            if status_code == 401:
                return _unavailable(package, model=self._model, latency_ms=latency_ms)

            if status_code == 422:
                return _error(package, model=self._model, latency_ms=latency_ms)

            if status_code >= 400:
                return _error(package, model=self._model, latency_ms=latency_ms)

            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                return _error(package, model=self._model, latency_ms=latency_ms)

            if not isinstance(payload, dict):
                return _error(package, model=self._model, latency_ms=latency_ms)

            answers_raw = _extract_answers(payload)
            if answers_raw is None:
                return _error(package, model=self._model, latency_ms=latency_ms)

            model_id = payload.get("model")
            if not isinstance(model_id, str) or not model_id.strip():
                model_id = self._model

            usage_raw = payload.get("usage", {})
            usage = usage_raw if isinstance(usage_raw, dict) else {}

            return JevResult(
                evidence_package_id=package.package_id,
                model=model_id,
                status=EvidenceStatus.OK,
                answers=_normalize_answers(answers_raw),
                usage=usage,
                latency_ms=latency_ms,
                confidence_notes=CONFIDENCE_NOTES,
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        return _unavailable(package, model=self._model, latency_ms=latency_ms)


# Alias matching the plan naming.
JevAdapter = TypesafeSystemOneClient
