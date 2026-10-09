# ADR 0004 — Single Toobit adapter; Spot v1 / Futures v2

**Status:** Proposed  
**Date:** 2026-10-09  
**Related:** [../API_CONTRACTS.md](../API_CONTRACTS.md), [../ARCHITECTURE.md](../ARCHITECTURE.md)

## Context

Official Toobit docs document Spot trading under `/api/v1/spot/*` (including `orderTest`) and Futures regular orders under `/api/v2/futures/*` with JSON signing and `side` + `positionSide`. Code examples still show v1 Futures with `BUY_OPEN`-style sides. The PRD pipeline names one Toobit adapter stage.

## Decision

1. Implement one `ToobitAdapter` module with Spot and Futures ports (not separate microservices).
2. Use verified Spot v1 order routes for Spot execution until Spot v2 is verified and accepted.
3. Prefer Futures `/api/v2/futures/*` for new Futures execution work.
4. Keep unverified private Futures position/balance/stream details behind interfaces until re-verified.
5. Never call withdrawal endpoints.

## Consequences

* Dual version knowledge inside one module.
* Mapping layer from internal proposal actions to Spot vs Futures enums is mandatory.
* Quantity unit / `contractMultiplier` remains unresolved and must be verified before live Futures sizing.
