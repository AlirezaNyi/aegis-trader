# Jev limitations (Phase 4)

## Integration

* Official host only: `POST https://api.typesafe.ai/v1/systemone` via `httpx`. No `typesafe_sdk`, OpenRouter, or AI gateway.
* Missing `TYPESAFE_API_KEY` → `UnavailableJevPort` / `status=unavailable` with **no HTTP call**.
* Request `state` is a JSON string of a **redacted** EvidencePackage dump (credential-like keys removed).

## Retries and timeouts

* `AEGIS_JEV_TIMEOUT_SECONDS` is an engineering transport bound (like analyst timeouts), not a risk-policy number.
* At most **one** retry, and only for HTTP **429** / **529**, and only while remaining time is inside the transport deadline.
* No retry on timeout, 401, 422, transport errors, or malformed JSON.
* Package-level evidence freshness TTL for Jev retries is **not** owner-approved; remaining transport budget is the bound used today.

## Confidence semantics

* Choice/score `confidence` is treated as **distribution concentration**, never P(profit).
* Every `JevResult` carries `confidence_notes` stating this explicitly.

## Unverified / deferred

* Exact response nesting (`answers` vs top-level question keys) is handled defensively; pin against live docs when calibrating.
* Context size budgets (64k / 32k) are not enforced client-side yet — oversized packages may yield provider 422/`error`.
* Model alias `jev-latest` can move; pin a versioned id when thresholds depend on calibration.
* Jev never places exchange orders and never receives Toobit credentials.
