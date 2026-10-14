#!/usr/bin/env python3
"""One-off TypeSafe System One (Jev) probe: send one real request, print response SHAPE.

Purpose: pin the real response nesting before trusting the adapter's defensive
parsing (see src/aegis/jev/LIMITATIONS.md). Uses the production adapter with an
httpx response hook. Prints key names and value types only — never the API key,
request headers, or the evidence state.

No Toobit credentials, no orders. Costs one System One request.

Usage (from repo root, TYPESAFE_API_KEY set in the environment or .env):
  .venv/bin/python scripts/jev_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import httpx  # noqa: E402

from aegis.config.settings import Settings, TradingMode, clear_settings_cache  # noqa: E402
from aegis.evidence.builder import empty_package_shell  # noqa: E402
from aegis.jev.client import TypesafeSystemOneClient  # noqa: E402
from aegis.schemas.common import MarketType, Timeframe  # noqa: E402
from aegis.schemas.market import InstrumentRef  # noqa: E402


def _shape(value: Any, depth: int = 0) -> Any:
    """Structure with types instead of string values; bounded depth."""
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {str(k): _shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(value[0], depth + 1)] if value else []
    return type(value).__name__


async def _run() -> int:
    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
    )
    if not settings.jev_configured:
        print("TYPESAFE_API_KEY is not set; nothing sent.")
        return 2

    captured: dict[str, Any] = {}

    async def _on_response(response: httpx.Response) -> None:
        await response.aread()
        captured["status"] = response.status_code
        try:
            body = response.json()
        except ValueError:
            captured["shape"] = "non-json body"
            return
        captured["shape"] = _shape(body)
        # 422 bodies are server-side schema errors about OUR request; safe to show.
        if response.status_code == 422 and isinstance(body, dict):
            captured["validation"] = body.get("detail")

    http_client = httpx.AsyncClient(
        timeout=settings.jev_timeout_seconds,
        event_hooks={"response": [_on_response]},
    )
    package = empty_package_shell(
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT"),
        market_type=MarketType.SPOT,
        timeframe=Timeframe.M5,
        as_of=datetime.now(tz=UTC),
        correlation_id="jev-probe",
    )
    client = TypesafeSystemOneClient(
        api_key=settings.typesafe_api_key,
        model=settings.jev_model,
        timeout_seconds=settings.jev_timeout_seconds,
        max_retries=0,
        http_client=http_client,
    )
    try:
        result = await client.evaluate(package)
    finally:
        await http_client.aclose()

    print("HTTP status:", captured.get("status"))
    print("Response shape:")
    print(json.dumps(captured.get("shape"), indent=2))
    if "validation" in captured:
        print("Validation errors (422):")
        print(json.dumps(captured["validation"], indent=2, default=str))
    print("Adapter result:")
    print(
        json.dumps(
            {
                "status": result.status.value,
                "model": result.model,
                "answer_keys": sorted(result.answers),
                "usage": result.usage,
                "latency_ms": result.latency_ms,
            },
            indent=2,
            default=str,
        )
    )
    return 0 if result.status.value == "ok" else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
