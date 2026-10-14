# Jev limitations (Phase 4)

## Integration

* Official host only: `POST https://api.typesafe.ai/v1/systemone` via `httpx.AsyncClient` (`async evaluate`, `async aclose`). No `typesafe_sdk`, OpenRouter, or AI gateway.
* Missing `TYPESAFE_API_KEY` → `UnavailableJevPort` / `status=unavailable` with **no HTTP call**.
* Request `state` is a JSON string of a **redacted** EvidencePackage dump (credential-like keys removed).

## Retries and timeouts

* `AEGIS_JEV_TIMEOUT_SECONDS` is an engineering transport bound (like analyst timeouts), not a risk-policy number.
* At most **one** retry, and only for HTTP **429** / **529**, and only while remaining time is inside the transport deadline. Before the retry the client awaits `asyncio.sleep(retry_backoff_seconds)` (engineering default 0.25 s, clipped to the remaining deadline; not a risk-policy value).
* No retry on timeout, 401, 422, transport errors, or malformed JSON.
* Package-level evidence freshness TTL for Jev retries is **not** owner-approved; remaining transport budget is the bound used today.

## Confidence semantics

* Choice/score `confidence` is treated as **distribution concentration**, never P(profit).
* Every `JevResult` carries `confidence_notes` stating this explicitly.

## Unverified / deferred

* Response nesting pinned by live call on 2026-10-13: answers live under top-level `answers` (see docs/API_CONTRACTS.md §4). Re-run `scripts/jev_probe.py` after model changes.
* Context size budgets (64k / 32k) are not enforced client-side yet — oversized packages may yield provider 422/`error`.
* Model alias `jev-latest` resolved to `jev-1.13.0` on 2026-10-13 and can move; set `AEGIS_JEV_MODEL=jev-1.13.0` to pin it.
* Jev never places exchange orders and never receives Toobit credentials.
