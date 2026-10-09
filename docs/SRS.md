# Aegis — Software Requirements Specification (SRS)

**Version:** 1.0
**Status:** Proposed (Phase 0)
**Source of truth:** [PRD.md](PRD.md)
**Package identity:** `aegis`

## Document conventions

| Label | Meaning |
| --- | --- |
| **Verified** | Confirmed against official provider documentation as of the cited date |
| **Assumption** | Working premise pending evidence |
| **Proposed** | Engineering recommendation awaiting owner acceptance via ADR |
| **Unresolved** | Owner or documentation decision still required |

Requirement IDs are stable. Traceability columns reference PRD sections and delivery phases.

## 1. Scope

### In scope

Personal autonomous trading on Toobit Spot and Futures for scalping (1m, 5m) and intraday (5m, 15m), paper-first, with agent-led analysis, Jev evaluation, LLM supervision, and a deterministic Risk Engine.

### Out of scope

Profit guarantees, LLM-led execution, automated live enablement, withdrawals, undocumented APIs, per-agent microservices, GPU-dependent inference.

## 2. Functional requirements

### 2.1 Market data and validation

| ID | Requirement | PRD | Phase |
| --- | --- | --- | --- |
| SRS-MD-001 | The system shall ingest Toobit market data only through verified REST and WebSocket endpoints documented in [API_CONTRACTS.md](API_CONTRACTS.md). | §7, §13 | 2 |
| SRS-MD-002 | The system shall normalize Spot and Futures instruments separately, preserving exchange symbol identifiers and market type. | §4, §6 | 2 |
| SRS-MD-003 | The system shall support candle intervals 1m, 5m, and 15m. | §4 | 2 |
| SRS-MD-004 | The system shall distinguish exchange event time from local receive time. | §7 | 2 |
| SRS-MD-005 | The system shall detect duplicates, gaps, malformed payloads, out-of-order events, and stale data. | §7, §10 | 2 |
| SRS-MD-006 | Incomplete candles shall not be treated as finalized unless a strategy explicitly allows intrabar use. | §8 | 2–3 |
| SRS-MD-007 | Invalid or stale critical market data shall make the system not-ready for new trading decisions. | §10, §16 | 2, 5 |

### 2.2 Features and analysts

| ID | Requirement | PRD | Phase |
| --- | --- | --- | --- |
| SRS-AN-001 | The Feature Engine shall compute deterministic features from validated market data. | §6–7 | 3 |
| SRS-AN-002 | Technical, Quantitative, News/Sentiment, Analytical Risk, and Strategy Researcher components shall each return structured evidence with provenance and timestamps. | §8 | 3 |
| SRS-AN-003 | Missing or stale analyst inputs shall yield explicit UNKNOWN / NOT_APPLICABLE / UNAVAILABLE states, never fabricated facts. | §8 | 3 |
| SRS-AN-004 | The Analytical Risk Analyst shall not approve, reject, or size trades. | §8, §10 | 3, 5 |
| SRS-AN-005 | Analyst and feature work shall use bounded concurrency and timeouts. | §18 | 3 |
| SRS-AN-006 | News and retrieved content shall be treated as untrusted input. | §15 | 3–4 |

### 2.3 Evidence, Jev, and Supervisor

| ID | Requirement | PRD | Phase |
| --- | --- | --- | --- |
| SRS-EV-001 | Evidence Builder shall produce a versioned, auditable evidence package referencing analyst outputs. | §7 | 3–4 |
| SRS-JV-001 | Jev integration shall use only the official TypeSafe System One API when configured; otherwise a mock returning unavailable. | §13 | 4 |
| SRS-JV-002 | Jev confidence and probabilities shall not be labeled as calibrated profit probability without empirical calibration. | §9 | 4 |
| SRS-JV-003 | Jev and LLM components shall never receive exchange credentials or execution tools. | §3, §15 | 4–7 |
| SRS-SV-001 | The LLM Supervisor shall emit a schema-validated proposal with action BUY, SELL, HOLD, or NO_TRADE. | §9 | 4 |
| SRS-SV-002 | The Supervisor shall prefer NO_TRADE when critical evidence is missing or the proposal cannot be justified. | §8 | 4 |
| SRS-SV-003 | Prompt content from news, documents, or tool output shall not be treated as trusted system instructions. | §15 | 4 |
| SRS-SV-004 | LLM invocation shall be event- and schedule-bounded, not per market tick. | §3, §18 | 4 |

### 2.4 Risk Engine

| ID | Requirement | PRD | Phase |
| --- | --- | --- | --- |
| SRS-RK-001 | Only the deterministic Risk Engine may approve or reject a new trade. | §10 | 5 |
| SRS-RK-002 | Risk decisions shall be deterministic for identical inputs and policy version. | §18 | 5 |
| SRS-RK-003 | Missing, stale, inconsistent, or unknown critical state shall cause REJECT for new orders. | §10 | 5 |
| SRS-RK-004 | Numeric policy limits shall be versioned and owner-approved before enforcement as production policy. Unapproved parameters shall not silently receive invented defaults. | §10, §21 | 5 |
| SRS-RK-005 | Models shall not modify risk limits or override REJECT. | §3, §10 | 5 |
| SRS-RK-006 | Blocking new orders, canceling open orders, and protecting/closing positions shall be separately defined procedures. | §10 | 5, 8 |
| SRS-RK-007 | Every decision shall record policy version, rule IDs, reasons, correlation ID, and validated parameters. | §10 | 5 |

### 2.5 Execution and reconciliation

| ID | Requirement | PRD | Phase |
| --- | --- | --- | --- |
| SRS-EX-001 | Order Manager shall accept only APPROVE risk decisions that are unexpired and idempotent. | §11 | 7 |
| SRS-EX-002 | Live submission shall require paper/live mode, explicit arm flag, and kill switch all permitting live trading. | §12, §20 | 7–8 |
| SRS-EX-003 | On timeout or HTTP 5XX after a signed submit attempt, the system shall reconcile exchange state and shall not blindly resubmit. | §11 | 7 |
| SRS-EX-004 | Client order identifiers shall be used where supported to prevent duplicates. | §11 | 7 |
| SRS-EX-005 | Exchange state is authoritative for fills, positions, and balances. | §11 | 7 |
| SRS-EX-006 | Incomplete critical reconciliation shall block new orders. | §11 | 7 |
| SRS-EX-007 | The Toobit adapter shall never call withdrawal endpoints. | §15 | 7 |
| SRS-EX-008 | Paper execution shall use a separate simulated account and shall not sign live orders. | §12 | 6 |

### 2.6 Modes and evaluation

| ID | Requirement | PRD | Phase |
| --- | --- | --- | --- |
| SRS-MD-100 | Default runtime mode shall be Paper Trading. | §12 | 1, 6 |
| SRS-BT-001 | Backtests shall prevent look-ahead bias and separate train / validation / out-of-sample periods. | §17 | 6 |
| SRS-BT-002 | Backtests shall model fees, spread, slippage, latency, and Futures funding where applicable. | §17 | 6 |
| SRS-EV-100 | Passing automated tests shall not be interpreted as profitability. | §17 | 6, 9 |

## 3. Non-functional requirements

| ID | Requirement | PRD | Phase |
| --- | --- | --- | --- |
| SRS-NF-001 | Initial target host: ~4 vCPU, 8 GB RAM, 80 GB SSD, no GPU. | §4, §18 | 8 |
| SRS-NF-002 | Structured logs with correlation IDs across proposal → risk → order → fill. | §16 | 1, 8 |
| SRS-NF-003 | Explicit timeouts and bounded retries for all external calls. | §18 | 1–8 |
| SRS-NF-004 | Graceful shutdown and restart recovery with reconciliation. | §16 | 7–8 |
| SRS-NF-005 | Secrets never committed; never logged; never sent to models. | §15 | 1, 8 |
| SRS-NF-006 | Unit, contract, and integration tests via pytest; static checks via Ruff and mypy. | §14 | 1 |

## 4. Interface requirements

| ID | Requirement | PRD | Phase |
| --- | --- | --- | --- |
| SRS-IF-001 | Internal contracts shall be Pydantic models as specified in [API_CONTRACTS.md](API_CONTRACTS.md) and [DATA_MODEL.md](DATA_MODEL.md). | §9, §14 | 1 |
| SRS-IF-002 | FastAPI shall expose health and readiness; readiness shall reflect data and reconciliation state. | §16 | 1, 2 |
| SRS-IF-003 | Owner control actions that change live mode or kill switch shall be authenticated, audited, and unreachable by model components. | §5, §15 | 8 |

## 5. Constraints and assumptions

### Proposed (engineering)

* Modular monolith with package `aegis`.
* Explicit orchestration; PostgreSQL as system of record; Redis deferred.
* See [RFC.md](RFC.md) and [DECISIONS/](DECISIONS/).

### Assumptions

* Owner operates a single primary account and is the sole authority for live activation.
* Official Toobit and TypeSafe documentation remain the integration references.

### Unresolved

* All items in PRD §21 and [RISK_POLICY.md](RISK_POLICY.md) parameters marked `UNAPPROVED`.

## 6. Acceptance for Phase 0

Phase 0 is complete when this SRS and the related architecture documents exist, separate verified / assumption / proposed / unresolved content, contain no invented financially approved risk numbers, and mark unverified external endpoints as interfaces or mocks.
