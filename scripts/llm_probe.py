#!/usr/bin/env python3
"""One-off LLM Supervisor probe: one real completion through the production adapter.

Prints HTTP status, the provider's error message on failure (``error.message``
only — never headers or the API key), and whether the reply parses as a
SupervisorDecision. No Toobit credentials, no orders.

Usage (from repo root, AEGIS_LLM_* set in the environment or .env):
  .venv/bin/python scripts/llm_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import httpx  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from aegis.config.settings import Settings, TradingMode, clear_settings_cache  # noqa: E402
from aegis.supervisor.budgets import budgets_allow_call  # noqa: E402
from aegis.supervisor.decision import SupervisorDecision  # noqa: E402
from aegis.supervisor.factory import budget_config_from_settings  # noqa: E402
from aegis.supervisor.openai_compat import OpenAICompatLlmPort, resolve_base_url  # noqa: E402

_PROMPT = "\n".join(
    [
        "You are the Aegis LLM Supervisor. Produce a single SupervisorDecision JSON object.",
        "This is a connectivity test with no market data. Return exactly:",
        '{"action": "NO_TRADE", "direction": "flat", "entry_price": null, '
        '"stop_loss": null, "take_profit": null, "rationale": "probe", '
        '"invalidation": "", "confidence_note": ""}',
    ]
)


def _provider_error(body: Any) -> str | None:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            return None if message is None else str(message)[:500]
        if isinstance(err, str):
            return err[:500]
    return None


async def _run() -> int:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
    )
    budgets = budget_config_from_settings(settings)
    print("provider:", settings.llm_provider or "(empty)")
    print("model:", settings.llm_model or "(empty)")
    print(
        "budgets set:",
        {
            "token": budgets.token_budget is not None,
            "cost": budgets.cost_budget is not None,
            "latency_ms": budgets.latency_budget_ms is not None,
        },
    )
    if not budgets_allow_call(budgets):
        print("Supervisor would NOT call the LLM: set all three AEGIS_LLM_*_BUDGET values.")
    if not settings.llm_configured:
        print("LLM provider/key/model not configured; nothing sent.")
        return 2

    captured: dict[str, Any] = {}

    async def _on_response(response: httpx.Response) -> None:
        await response.aread()
        captured["status"] = response.status_code
        if response.status_code >= 400:
            try:
                captured["provider_error"] = _provider_error(response.json())
            except ValueError:
                captured["provider_error"] = "non-json error body"

    provider = settings.llm_provider.strip().lower()
    http_client = httpx.AsyncClient(
        timeout=settings.supervisor_timeout_seconds,
        event_hooks={"response": [_on_response]},
    )
    port = OpenAICompatLlmPort(
        provider=provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=resolve_base_url(provider, settings.llm_base_url or None),
        timeout_seconds=settings.supervisor_timeout_seconds,
        http_client=http_client,
    )
    try:
        out = await port.complete_structured(
            prompt=_PROMPT,
            schema_name="SupervisorDecision",
            timeout_seconds=settings.supervisor_timeout_seconds,
        )
    except RuntimeError as exc:
        print("HTTP status:", captured.get("status"))
        print("adapter error:", exc)
        if captured.get("provider_error"):
            print("provider error:", captured["provider_error"])
        return 1
    finally:
        await http_client.aclose()

    meta = out.pop("_meta", {})
    print("HTTP status:", captured.get("status"))
    print("usage meta:", json.dumps(meta, default=str))
    try:
        decision = SupervisorDecision.model_validate(out)
    except ValidationError as exc:
        print("reply is NOT a valid SupervisorDecision:", exc.errors()[:3])
        return 1
    print("reply OK:", decision.model_dump(mode="json"))
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
