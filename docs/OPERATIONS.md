# Aegis — Operations

**Version:** 1.0
**Status:** Proposed (Phase 0)
**Related:** [DEPLOYMENT.md](DEPLOYMENT.md), [RISK_POLICY.md](RISK_POLICY.md), [ARCHITECTURE.md](ARCHITECTURE.md)

## 1. Operating modes

| Mode | Intent |
| --- | --- |
| Development | Mocks/fixtures; no live submits |
| Backtesting | Offline replay |
| Paper | Full decision path; simulated execution |
| Live | Disabled until separate owner activation |

## 2. Health and readiness

| Check | Failure action |
| --- | --- |
| Process up (`/health`) | Alert; restart per runbook |
| DB reachable | Not ready; block new orders |
| Market data freshness | Not ready for affected instruments; REJECT new |
| Reconciliation complete | Block new orders until resolved |
| Kill switch | Block new orders |

## 3. Monitoring signals (**Proposed**)

* Market-data lag, gaps, reconnects
* Analyst/Jev/LLM latency, errors, validation failures, estimated cost
* Proposal counts; NO_TRADE reasons
* Risk APPROVE/REJECT by rule id
* Order submit/ack/fill/reconcile latency
* Position discrepancies; unknown order states
* CPU, memory, disk, DB size

Alert when: data disconnected/stale, repeated model failures, order uncertainty, reconcile failure, disk exhaustion, kill switch / daily loss trip (once limits approved).

## 4. Incident procedures

### 4.1 Stop-new-orders

**Trigger examples:** kill switch, stale data, unknown order state, reconcile failure, host resource crisis.

**Actions:**

1. Set kill switch / confirm mode blocks new.
2. Alert owner.
3. Preserve logs and correlation ids.
4. Do not invent cancels/closes unless approved procedure says so.

### 4.2 Cancel-open-orders

**Status:** UNAPPROVED automation. Until approved, owner manually cancels via exchange UI or a future audited control action.

### 4.3 Protect-or-close-positions

**Status:** UNAPPROVED automation. Until approved, owner manually manages exposure.

### 4.4 Unknown order after submit

1. Do not resubmit blindly.
2. Query by `clientOrderId` / exchange order id using verified endpoints.
3. Update local state; keep new orders blocked while unknown.
4. Escalate if not resolved within owner-defined SLA (**UNAPPROVED**).

## 5. Backup and recovery

**Proposed:**

* Daily Postgres backups; retain count **UNAPPROVED**.
* Test restore on a non-production instance before claiming readiness.
* After restore: run reconciliation; keep live disarmed.

## 6. Secret rotation

1. Create new exchange key with least privilege + IP restriction.
2. Inject via secret mechanism; restart `aegis`.
3. Disable old key.
4. Audit that withdraw endpoint remains unused.
5. Rotate TypeSafe/LLM keys similarly.

## 7. Live activation checklist (owner)

Do not auto-complete. Owner must review:

* [ ] Toobit and Jev behavior re-verified against official docs
* [ ] Risk policy version approved with numeric limits filled
* [ ] Backtest assumptions and OOS results reviewed
* [ ] Paper evaluation window/sample criteria met (**after** those criteria are approved)
* [ ] Idempotency and reconciliation tests passed
* [ ] Incident procedures understood (stop-new vs cancel vs protect)
* [ ] Secrets and permissions verified (no withdrawal use)
* [ ] Monitoring, alerting, backup/restore verified
* [ ] Known limitations accepted
* [ ] Explicit arm of live mode

## 8. Routine paper operations

* Prefer paper soak on the target host before any live review.
* Cap LLM/Jev spend with budgets (**owner-set**).
* Review disk growth from candles and audit logs.

## 9. Unresolved operational decisions

* On-call / notification channel.
* Retention periods.
* Whether emergencies may auto-cancel or auto-close.
* SLA for unknown-order resolution.
