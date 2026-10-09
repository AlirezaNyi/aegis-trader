# Aegis — Test Plan

**Version:** 1.0
**Status:** Proposed (Phase 0)
**Tooling:** pytest, Ruff, mypy (**Proposed**)
**Related:** [SRS.md](SRS.md), [Phases/](../Phases/)

## 1. Goals

* Prove safety invariants before any live trading.
* Prefer deterministic tests with fixtures and mocks.
* Never place real exchange orders in CI or local default workflows.
* Record actual test results in phase reports; do not claim unrun tests passed.

## 2. Test layers

| Layer | Scope |
| --- | --- |
| Unit | Pure functions: features, risk rules, parsers, state machines |
| Contract | Pydantic schema validation; golden JSON fixtures |
| Integration | In-process pipeline with mocked Toobit/Jev/LLM |
| Property | Risk numeric boundaries where Hypothesis (or similar) is justified |
| Replay | Deterministic candle/feature/backtest fixtures |
| Operational | Startup guards, readiness, backup restore drills (Phase 8) |

## 3. Global invariants (every phase that touches execution path)

| ID | Invariant | How tested |
| --- | --- | --- |
| INV-01 | Rejected RiskDecision never reaches adapter submit | Integration spy/mock |
| INV-02 | Models have no access to Toobit credentials | Dependency injection audits; unit tests that supervisor/jev modules lack secret fields |
| INV-03 | Live submit blocked when mode≠live or disarmed or kill switch on | Unit + integration |
| INV-04 | Timeout/5XX after submit → reconcile path, no second submit | State machine tests |
| INV-05 | Stale/missing critical data → REJECT | Risk unit tests |
| INV-06 | Withdrawal endpoint never called | Adapter allowlist / static deny list test |
| INV-07 | Paper ledger isolated from live ledger | Data model tests |
| INV-08 | Invalid proposal schema → no Risk APPROVE path | Contract tests |

## 4. Phase-mapped tests

### Phase 0 — Documentation

* Checklist: all required docs present; unverified APIs marked; no approved numeric risk limits invented.

### Phase 1 — Foundation

* Config fails on invalid settings.
* App starts in development/paper.
* Live guard prevents live without explicit config.
* Shared schemas import and validate fixtures.
* Ruff + mypy + unit tests green.

### Phase 2 — Market data

* Fixtures: normal, malformed, duplicate, gap, out-of-order, stale.
* Candle aggregation against known inputs.
* Reconnect/resubscribe behavior with fake WS.
* Freshness/readiness metrics exposed.
* No execution code paths enabled.

### Phase 3 — Features and analysts

* Deterministic feature snapshots.
* Timeframe alignment tests.
* Look-ahead leakage tests.
* Missing data → UNAVAILABLE, not fabricated.
* Bounded timeout tests with slow mocks.

### Phase 4 — Jev and Supervisor

* Mock Jev: ok, timeout, 429, 529, 422, malformed.
* Confidence not labeled as P(profit).
* Supervisor: contradictory signals, missing analysts, prompt injection payloads → safe NO_TRADE / schema fail.
* Token/latency budget enforcement.
* Invalid proposals never reach execution.

### Phase 5 — Risk Engine

* Unit test every policy rule id.
* Boundary and precision tests.
* Property tests for monotonic limit violations where useful.
* Unapproved required params block live path.
* REJECT cannot be flipped by model metadata fields.

### Phase 6 — Backtest and paper

* Deterministic replay.
* Cost application consistency.
* OOS separation assertions.
* Paper reconcile after restart.
* Live still disabled.

### Phase 7 — Toobit execution

* Mocks only (or official orderTest for Spot signature validation — never matching-engine live money in dev).
* Duplicate clientOrderId handling.
* Partial fills, cancels, rejects, rate limits.
* Unknown submit reconciliation.
* Risk REJECT cannot call adapter.

### Phase 8 — Deployment readiness

* Resource limits under load smoke.
* Alert wiring dry-run.
* Backup/restore test.
* Secret absence in images/logs.
* Live checklist documented; live remains off.

### Phase 9 — Final audit

* Re-run invariant suite; independent review per [Phases/Final System Audit.md](../Phases/Final%20System%20Audit.md).

## 5. Acceptance review gate

Use [Phases/Phase Acceptance Review.md](../Phases/Phase%20Acceptance%20Review.md). Blocking findings stop the next phase.

## 6. Non-goals for tests

* Proving strategy profitability.
* Hitting production Toobit with real capital.
* Inventing undocumented exchange behaviors in mocks presented as verified.
