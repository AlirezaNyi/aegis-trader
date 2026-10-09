# Aegis — RFC: Initial System Design

**Version:** 1.0
**Status:** Proposed for owner acceptance
**Date:** 2026-10-09
**Related ADRs:** [DECISIONS/](DECISIONS/)

## 1. Summary

Build Aegis as a Python modular monolith (`aegis`) that runs the approved decision pipeline in one process with PostgreSQL as the system of record. Paper Trading is the default. Live trading remains disabled until a separate owner decision. External Toobit and Jev behavior is limited to capabilities verified in [API_CONTRACTS.md](API_CONTRACTS.md).

## 2. Motivation

The PRD requires risk containment, auditability, and empirical evaluation before any live capital is at risk. A modular monolith matches the initial host (~4 vCPU, 8 GB RAM, 80 GB SSD), reduces operational surface area, and keeps the Risk Engine on the same critical path as execution without network hops between agents.

## 3. Proposed design

### 3.1 Runtime shape

| Concern | Proposal |
| --- | --- |
| Process model | Single FastAPI application process + background workers in-process |
| Package | `aegis` |
| Persistence | PostgreSQL |
| Cache / bus | None initially (Redis deferred) |
| Orchestration | Explicit Python orchestrator (no agent tool loop) |
| Exchange | One Toobit adapter module with Spot and Futures ports |
| Models | Jev adapter (TypeSafe official API) + LLM provider abstraction |
| Default mode | Paper Trading |

### 3.2 Module boundaries

```text
aegis/
  market_data/       # gateway, WS/REST clients, normalization
  validation/        # freshness, integrity, candle finalization
  features/          # deterministic feature engine
  analysts/          # technical, quant, news, analytical risk, strategy researcher
  evidence/          # evidence package builder
  jev/               # TypeSafe System One adapter + mock
  supervisor/        # LLM supervisor (schema-constrained)
  risk/              # deterministic risk engine
  orders/            # order manager, state machine
  exchange/          # Toobit adapter (credentials live only here)
  paper/             # paper broker and simulated ledger
  backtest/          # historical replay
  reconcile/         # exchange vs local reconciliation
  api/               # health, readiness, owner control
  audit/             # structured audit events
  config/            # typed settings, secret injection
```

### 3.3 Trust and credential boundary

Only `aegis.exchange` may load Toobit API keys or compute HMAC signatures. Analysts, Jev, Supervisor, Evidence Builder, and Risk Engine receive redacted market and account *summaries* required for decisions, never raw secrets.

### 3.4 Live gates (proposed)

Live order submission requires all of:

1. `trading_mode == live`
2. `live_armed == true` (explicit owner arm)
3. `kill_switch == false`

Any failure rejects new live submits. Models cannot write these flags.

### 3.5 Emergency posture (proposed until owner approves otherwise)

On critical failure or unknown exchange state: block new orders and alert the owner. Do not automatically cancel open orders or close positions until an emergency procedure ADR is approved.

## 4. Rejected alternatives

| Alternative | Why rejected for Phase 0–8 |
| --- | --- |
| One microservice per analyst | No demonstrated isolation need; increases ops cost on a small host |
| LangGraph (or similar) supervisor tool loop | Tool loops invite execution/credential coupling; safety model forbids model-side tools |
| Redis as primary event bus | Adds moving parts; PostgreSQL + in-process queues suffice initially |
| Vercel AI Gateway / OpenRouter / other Jev proxies | Official path is `https://api.typesafe.ai/v1/systemone`; gateways are out of scope |
| Toobit Agent Trade Kit / MCP order tools | Exposes order placement to an AI client; violates credential and execution isolation |
| Assumed Toobit testnet | No separate testnet verified in official docs as of 2026-10-09 |
| Invented risk numeric defaults marked “safe” | PRD forbids silent financial policy invention |

## 5. External integration stance

### Verified enough to design against

* Toobit REST base, signing, rate limits, Spot order + orderTest, Futures v2 order/leverage, public klines/depth, funding/mark endpoints, Spot userDataStream, withdrawal endpoint existence (never call).
* TypeSafe Jev System One HTTP API and Python `typesafe_sdk`.

### Interface + mock until re-verified

* Futures private position/balance stream details beyond what is confirmed in the verification log.
* Futures orderTest equivalent.
* Spot API v2 order shape.
* Hedge vs one-way position mode requirements.

## 6. Risks of this RFC

* Single process means a bug can halt both analysis and execution; mitigated by kill switch, readiness gates, and paper default.
* Explicit orchestration requires careful scheduling to avoid LLM cost blowups; mitigated by budgets and event triggers.
* Incomplete futures private API verification delays Phase 7; mitigated by interface/mocks and documentation re-check before coding.

## 7. Acceptance

This RFC is accepted when the owner agrees with the modular monolith, deferred Redis/LangGraph/gateways/Agent Trade Kit, paper default, and the emergency “block new only” interim posture—or records an alternate ADR.
