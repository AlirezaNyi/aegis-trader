"""OpenAI-compatible chat-completions adapter for Supervisor.

Supported providers: gemini (Google AI Studio), openrouter, groq.
Secrets stay in this module's instance fields. Never log API keys.
Free-tier providers report ``_meta.cost = 0`` when the upstream omits cost.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import httpx

# Engineering defaults — not risk-policy numbers. Owner may override via env.
# Gemini: official OpenAI-compat base (ai.google.dev/gemini-api/docs/openai).
DEFAULT_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
}

SUPPORTED_PROVIDERS = frozenset(DEFAULT_BASE_URLS)


def resolve_base_url(provider: str, override: str | None = None) -> str:
    """Return chat-completions base URL (no trailing slash on path root)."""
    if override is not None and override.strip():
        return override.strip().rstrip("/")
    key = provider.strip().lower()
    if key not in DEFAULT_BASE_URLS:
        allowed = sorted(SUPPORTED_PROVIDERS)
        msg = f"unsupported LLM provider {provider!r}; expected one of {allowed}"
        raise ValueError(msg)
    return DEFAULT_BASE_URLS[key]


def _parse_message_content(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        msg = f"LLM content must be str or dict, got {type(content).__name__}"
        raise ValueError(msg)
    text = content.strip()
    if not text:
        msg = "LLM returned empty content"
        raise ValueError(msg)
    # Some models wrap JSON in markdown fences.
    if text.startswith("```"):
        lines = text.split("\n")
        # drop first fence and optional trailing fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        msg = "LLM JSON content must be an object"
        raise ValueError(msg)
    return parsed


def _usage_meta(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage")
    meta: dict[str, Any] = {"provider_response_id": payload.get("id")}
    if isinstance(usage, dict):
        meta["usage"] = dict(usage)
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        total = usage.get("total_tokens")
        if isinstance(total, int):
            meta["total_tokens"] = total
        elif isinstance(prompt, int) and isinstance(completion, int):
            meta["total_tokens"] = prompt + completion
            meta["prompt_tokens"] = prompt
            meta["completion_tokens"] = completion
        elif isinstance(prompt, int):
            meta["prompt_tokens"] = prompt
        if isinstance(completion, int) and "completion_tokens" not in meta:
            meta["completion_tokens"] = completion
    # Free / unpriced endpoints: always report cost so supervisor budget gate can pass.
    cost = payload.get("usage", {})
    reported: Any = None
    if isinstance(cost, dict):
        reported = cost.get("cost")
    if reported is None:
        reported = payload.get("cost")
    if reported is None:
        meta["cost"] = 0
    else:
        meta["cost"] = reported
    return meta


class OpenAICompatLlmPort:
    """httpx-backed OpenAI-compatible structured completion port."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        provider: str,
        timeout_seconds: float = 15.0,
        http_client: httpx.Client | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model.strip()
        self._base_url = base_url.rstrip("/")
        self._provider = provider.strip().lower()
        self._timeout_seconds = timeout_seconds
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._extra_headers = dict(extra_headers or {})

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenAICompatLlmPort:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def complete_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if not self._api_key:
            msg = "LLM API key is empty"
            raise RuntimeError(msg)
        if not self._model:
            msg = "LLM model is empty"
            raise RuntimeError(msg)

        url = urljoin(self._base_url + "/", "chat/completions")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Return a single JSON object matching schema {schema_name}. "
                        "No markdown, no commentary."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post(
                url,
                headers=headers,
                json=body,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            msg = "LLM request timed out"
            raise RuntimeError(msg) from exc
        except httpx.HTTPError as exc:
            # Do not include response body / headers that might echo auth.
            msg = f"LLM transport error: {type(exc).__name__}"
            raise RuntimeError(msg) from exc

        if response.status_code >= 400:
            msg = f"LLM HTTP {response.status_code}"
            raise RuntimeError(msg)

        try:
            payload = response.json()
        except ValueError as exc:
            msg = "LLM response was not JSON"
            raise RuntimeError(msg) from exc
        if not isinstance(payload, dict):
            msg = "LLM response root must be an object"
            raise RuntimeError(msg)

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            msg = "LLM response missing choices"
            raise RuntimeError(msg)
        first = choices[0]
        if not isinstance(first, dict):
            msg = "LLM choice must be an object"
            raise RuntimeError(msg)
        message = first.get("message")
        if not isinstance(message, dict):
            msg = "LLM message missing"
            raise RuntimeError(msg)
        try:
            content_obj = _parse_message_content(message.get("content"))
        except (ValueError, json.JSONDecodeError) as exc:
            msg = "LLM content was not valid TradeProposal JSON"
            raise RuntimeError(msg) from exc

        meta = _usage_meta(payload)
        meta["provider"] = self._provider
        meta["model"] = self._model
        out = dict(content_obj)
        out["_meta"] = meta
        return out
