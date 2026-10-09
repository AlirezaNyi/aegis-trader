"""Redact credential-like keys from evidence packages before Jev evaluation."""

from __future__ import annotations

from typing import Any

from aegis.schemas.evidence import EvidencePackage

# Keys (case-insensitive substring / exact) that must never leave the credential plane.
_SENSITIVE_KEY_FRAGMENTS = (
    "toobit_api_key",
    "toobit_api_secret",
    "typesafe_api_key",
    "llm_api_key",
    "api_key",
    "api_secret",
    "secret",
    "signature",
    "withdraw",
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if _is_sensitive_key(str(k)) else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    return value


def redact_evidence_package(package: EvidencePackage) -> dict[str, Any]:
    """Return a JSON-safe dict of the package with credential-like keys stripped."""
    dumped = package.model_dump(mode="json")
    redacted = _redact_value(dumped)
    if not isinstance(redacted, dict):
        msg = "redacted evidence package must be a dict"
        raise TypeError(msg)
    return redacted
