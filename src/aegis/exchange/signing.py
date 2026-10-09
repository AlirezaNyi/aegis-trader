"""HMAC-SHA256 signing for Toobit SIGNED endpoints.

Verified (docs/API_CONTRACTS.md): signature is lowercase hex; param order must
match; v2 JSON totalParams = queryString + compact jsonBody.
Secrets must never be logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any


def build_query_string(params: Mapping[str, Any]) -> str:
    """Join params in insertion order as key=value&... (no URL-encoding here)."""
    parts: list[str] = []
    for key, value in params.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return "&".join(parts)


def sign_params(secret: str, params: Mapping[str, Any]) -> str:
    """HMAC-SHA256 of the query string; returns lowercase hex digest."""
    total = build_query_string(params)
    return hmac.new(
        secret.encode("utf-8"),
        total.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def compact_json(body: Mapping[str, Any] | list[Any]) -> str:
    """Compact JSON with separators matching Toobit v2 signing examples."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def sign_v2_json(secret: str, query_string: str, json_body: str) -> str:
    """v2 JSON: totalParams = queryString concatenated with raw JSON body."""
    total = f"{query_string}{json_body}"
    return hmac.new(
        secret.encode("utf-8"),
        total.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
