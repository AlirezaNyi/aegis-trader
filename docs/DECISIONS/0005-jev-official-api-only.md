# ADR 0005 — Official TypeSafe Jev API only

**Status:** Proposed  
**Date:** 2026-10-09  
**Related:** [../API_CONTRACTS.md](../API_CONTRACTS.md), [../RFC.md](../RFC.md)

## Context

Jev is TypeSafe’s System One model. Official docs specify `POST https://api.typesafe.ai/v1/systemone` and a Python `typesafe_sdk`. Third-party gateways appear in SDK examples but are not required. The PRD demands official API integration after documentation verification.

## Decision

1. Integrate Jev only via the official TypeSafe HTTP API (or official SDK aimed at that host).
2. If the key is missing or Jev is disabled, use a mock that returns `unavailable`.
3. Bound timeouts and retries so stale evidence packages are not silently re-evaluated.
4. Treat `confidence` as distribution concentration, not calibrated profit probability.
5. Do not route Aegis production traffic through AI Gateway / OpenRouter / other proxies unless a future ADR explicitly accepts that path.

## Consequences

* Clear verification story.
* Owner must provision `TYPESAFE_API_KEY` for non-mock use.
* Alias `jev-latest` can move; pin versioned model ids when thresholds depend on calibration.
