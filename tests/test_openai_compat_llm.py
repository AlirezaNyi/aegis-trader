"""OpenAI-compatible LLM adapter + factory wiring (mocked HTTP only)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.interfaces.llm import UnavailableLlmPort
from aegis.supervisor.budgets import BudgetConfig, budgets_allow_call
from aegis.supervisor.factory import build_llm_port
from aegis.supervisor.openai_compat import (
    OpenAICompatLlmPort,
    resolve_base_url,
)


def _proposal_json() -> dict[str, Any]:
    return {
        "proposal_id": "11111111-1111-1111-1111-111111111111",
        "correlation_id": "corr",
        "instrument": {"market_type": "spot", "symbol": "ADAUSDT"},
        "direction": "flat",
        "timeframe": "1m",
        "strategy_id": "aegis-default",
        "strategy_version": "0.1.0",
        "action": "NO_TRADE",
        "entry_conditions": {},
        "expires_at": "2026-10-09T15:10:00+00:00",
        "stop_loss": None,
        "take_profit": None,
        "sizing": {},
        "leverage": None,
        "evidence_refs": [],
        "analyst_results": [],
        "jev_result": {
            "evidence_package_id": "22222222-2222-2222-2222-222222222222",
            "model": "jev",
            "status": "ok",
            "answers": {},
            "usage": {},
            "latency_ms": 1,
        },
        "uncertainty": {"prefer_no_trade": True},
        "invalidation": {},
        "supervisor_model_meta": {},
        "created_at": "2026-10-09T15:00:00+00:00",
    }


def _mock_transport(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def test_resolve_base_url_defaults() -> None:
    assert resolve_base_url("groq").endswith("/openai/v1")
    assert "openrouter.ai" in resolve_base_url("openrouter")
    gemini = resolve_base_url("gemini")
    assert "generativelanguage.googleapis.com" in gemini
    assert gemini.endswith("/v1beta/openai")
    assert resolve_base_url("groq", " https://custom.example/v1/ ") == (
        "https://custom.example/v1"
    )
    with pytest.raises(ValueError):
        resolve_base_url("anthropic")


def test_build_llm_port_gemini() -> None:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
        llm_provider="gemini",
        llm_api_key="gemini-key",
        llm_model="gemini-2.5-flash",
        llm_token_budget=8000,
        llm_cost_budget=Decimal("0"),
        llm_latency_budget_ms=15000,
    )
    port = build_llm_port(settings)
    assert isinstance(port, OpenAICompatLlmPort)
    assert "generativelanguage.googleapis.com" in port._base_url  # noqa: SLF001
    port.close()


def test_cost_budget_zero_allows_call() -> None:
    budgets = BudgetConfig(
        token_budget=8000,
        cost_budget=Decimal("0"),
        latency_budget_ms=15000,
    )
    assert budgets_allow_call(budgets) is True
    assert budgets_allow_call(
        BudgetConfig(token_budget=8000, cost_budget=None, latency_budget_ms=15000)
    ) is False


def test_complete_structured_parses_usage_and_zero_cost() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers.get("Authorization") == "Bearer test-key"
        body = json.loads(request.content.decode())
        assert body["model"] == "llama-3.1-8b-instant"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(_proposal_json()),
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            },
        )

    port = OpenAICompatLlmPort(
        api_key="test-key",
        model="llama-3.1-8b-instant",
        base_url="https://api.groq.com/openai/v1",
        provider="groq",
        http_client=_mock_transport(handler),
    )
    try:
        out = port.complete_structured(
            prompt="test",
            schema_name="TradeProposal",
            timeout_seconds=5.0,
        )
    finally:
        port.close()

    assert out["action"] == "NO_TRADE"
    meta = out["_meta"]
    assert meta["total_tokens"] == 150
    assert meta["cost"] == 0
    assert meta["provider"] == "groq"
    assert "test-key" not in json.dumps(out)


def test_http_error_does_not_echo_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized secret=should-not-leak")

    port = OpenAICompatLlmPort(
        api_key="super-secret-key",
        model="m",
        base_url="https://api.groq.com/openai/v1",
        provider="groq",
        http_client=_mock_transport(handler),
    )
    try:
        with pytest.raises(RuntimeError, match="LLM HTTP 401") as exc_info:
            port.complete_structured(
                prompt="x", schema_name="TradeProposal", timeout_seconds=5.0
            )
    finally:
        port.close()
    assert "super-secret-key" not in str(exc_info.value)


def test_build_llm_port_groq_and_unknown() -> None:
    clear_settings_cache()
    groq = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
        llm_provider="groq",
        llm_api_key="k",
        llm_model="llama-3.1-8b-instant",
        llm_token_budget=8000,
        llm_cost_budget=Decimal("0"),
        llm_latency_budget_ms=15000,
    )
    port = build_llm_port(groq)
    assert isinstance(port, OpenAICompatLlmPort)
    port.close()

    no_budget = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
        llm_provider="groq",
        llm_api_key="k",
        llm_model="llama-3.1-8b-instant",
    )
    assert isinstance(build_llm_port(no_budget), UnavailableLlmPort)

    unknown = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
        llm_provider="openai",
        llm_api_key="k",
        llm_model="gpt-4o",
        llm_token_budget=8000,
        llm_cost_budget=Decimal("0"),
        llm_latency_budget_ms=15000,
    )
    assert isinstance(build_llm_port(unknown), UnavailableLlmPort)

    empty = Settings(trading_mode=TradingMode.PAPER, require_database=False)
    assert isinstance(build_llm_port(empty), UnavailableLlmPort)


def test_openrouter_extra_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["referer"] = request.headers.get("HTTP-Referer", "")
        seen["title"] = request.headers.get("X-Title", "")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(_proposal_json())}}],
                "usage": {"total_tokens": 10},
            },
        )

    port = OpenAICompatLlmPort(
        api_key="or-key",
        model="meta-llama/llama-3.3-70b-instruct:free",
        base_url="https://openrouter.ai/api/v1",
        provider="openrouter",
        http_client=_mock_transport(handler),
        extra_headers={
            "HTTP-Referer": "https://example.local",
            "X-Title": "AegisTest",
        },
    )
    try:
        port.complete_structured(
            prompt="p", schema_name="TradeProposal", timeout_seconds=5.0
        )
    finally:
        port.close()
    assert seen["referer"] == "https://example.local"
    assert seen["title"] == "AegisTest"


def test_build_llm_port_openrouter_wires_headers() -> None:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
        llm_provider="openrouter",
        llm_api_key="or-key",
        llm_model="meta-llama/llama-3.3-70b-instruct:free",
        llm_http_referer="https://example.local",
        llm_app_title="AegisTest",
        llm_token_budget=8000,
        llm_cost_budget=Decimal("0"),
        llm_latency_budget_ms=15000,
    )
    port = build_llm_port(settings)
    assert isinstance(port, OpenAICompatLlmPort)
    assert port._extra_headers.get("HTTP-Referer") == "https://example.local"  # noqa: SLF001
    assert port._extra_headers.get("X-Title") == "AegisTest"  # noqa: SLF001
    port.close()


def test_settings_allows_zero_cost_budget() -> None:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
        llm_token_budget=8000,
        llm_cost_budget=Decimal("0"),
        llm_latency_budget_ms=15000,
    )
    assert settings.llm_cost_budget == Decimal("0")
    assert settings.supervisor_budgets_configured is True

    with pytest.raises(ValueError, match="COST_BUDGET"):
        Settings(
            trading_mode=TradingMode.PAPER,
            require_database=False,
            llm_cost_budget=Decimal("-1"),
        )
