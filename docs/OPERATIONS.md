# Aegis — Operations

**Version:** 1.1
**Status:** Phase 8 accepted (paper posture)
**Related:** [DEPLOYMENT.md](DEPLOYMENT.md), [RISK_POLICY.md](RISK_POLICY.md), [ARCHITECTURE.md](ARCHITECTURE.md), [READINESS_REPORT.md](READINESS_REPORT.md)

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
| Graceful shutdown | `/ready` returns `ready=false` while draining |

Correlation: clients may send `X-Correlation-Id`; responses echo it; structured JSON logs include the id when set.

## 3. Monitoring signals

Scrape: `GET /metrics` (Prometheus text). Snapshot helpers live in `aegis.ops.OpsMetrics`.

* Market-data lag, gaps, reconnects (via market-data metrics when configured)
* Analyst/Jev/LLM latency, errors, validation failures, estimated cost (counters; wire call sites as traffic exists)
* Proposal counts; NO_TRADE reasons
* Risk APPROVE/REJECT by rule id
* Order submit/ack/fill/reconcile latency
* Position discrepancies; unknown order states
* Kill switch / shutting_down gauges
* CPU, memory, disk, DB size (host-level; disk free injectable into ops metrics)

Alert dry-run: `GET /ops/alerts/dry-run` — evaluates stale data, provider failures, unknown orders, position discrepancies, reconcile failures, disk exhaustion, emergency-stop. **Does not notify** (channel UNRESOLVED).

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

Scripts:

* `scripts/backup_postgres.sh [out_dir]` — `pg_dump -Fc` from Compose `postgres`.
* `scripts/restore_postgres.sh path/to.dump` — `pg_restore --clean`; then restart `aegis`, verify `/ready`, run reconciliation, **keep live disarmed**.

Retention count remains **UNAPPROVED**. Test restore on a non-production instance before claiming live readiness.

## 6. Secret rotation

1. Create new exchange key with least privilege + IP restriction.
2. Inject via secret mechanism (runtime env / secret store); restart `aegis`.
3. Disable old key.
4. Audit that withdraw endpoint remains unused.
5. Rotate TypeSafe/LLM keys similarly.

Smoke: `scripts/check_image_secrets.sh`.

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
* [ ] Explicit arm of live mode (`AEGIS_TRADING_MODE=live` **and** `AEGIS_LIVE_ARMED=true`, kill switch false)

## 8. Routine paper operations

* Prefer paper soak on the target host before any live review.
* Cap LLM/Jev spend with budgets (**owner-set**); empty budgets fail closed.
* Review disk growth from candles and audit logs.
* Avoid LLM on every market tick — event-driven analysis, caching, timeouts, budgets.

### 8.1 Owner risk limits (policy 1.1, env)

Select `AEGIS_RISK_POLICY_VERSION=1.1`. All values are **percent of the initial
capital base** (`AEGIS_PAPER_INITIAL_BALANCE`; set it to the real account size
before any live review). There are no code defaults: any empty required value makes
every new trade REJECT, and the startup log plus `/ops/dashboard` (`risk_policy.
missing_env`) list the missing names.

| Env | Meaning | Practitioner convention (not a recommendation) |
| --- | --- | --- |
| `AEGIS_RISK_POSITION_PCT` | Max notional per position | sized so several positions fit the capital |
| `AEGIS_RISK_PER_TRADE_PCT` | Loss if the stop is hit (sizes by stop distance) | commonly 0.5–2% |
| `AEGIS_RISK_DAILY_LOSS_PCT` | Daily (UTC) equity loss before lock | below weekly |
| `AEGIS_RISK_WEEKLY_LOSS_PCT` | ISO-week (Mon 00:00 UTC) loss before lock | between daily and monthly |
| `AEGIS_RISK_MONTHLY_LOSS_PCT` | Calendar-month loss before lock | above weekly |
| `AEGIS_RISK_MAX_DRAWDOWN_PCT` | Drawdown from peak equity | — |
| `AEGIS_RISK_MIN_REWARD_RISK` | Min reward:risk **after** round-trip fees, spread, slippage | breakeven win rate = 1/(1+R) |
| `AEGIS_TRADING_FEE_BPS` | Real Toobit fee per side (use taker; see toobit.com/vip) | — |

Validation enforces 0 < pct ≤ 100, daily ≤ weekly ≤ monthly, and per-trade ≤ position.
Losses are equity-based (realized + unrealized). Breaching a limit locks new entries
until the next period starts; protective stop/target exits still run. Period state and
the paper ledger persist in Postgres (`risk_period_state`); if they cannot be restored,
new trades REJECT until the state is repaired (no silent reset).

### 8.2 Decision source and rule parameters

`AEGIS_DECISION_SOURCE=llm|rules|rules+llm`. `rules` needs no LLM spend; `rules+llm`
lets the LLM only veto rule candidates. Rules need `AEGIS_RULE_PARAMS_FILE` (JSON from
`scripts/walk_forward.py`); missing/invalid params => every cycle NO_TRADE.

## 9. Restart recovery

1. Compose/`uvicorn` graceful shutdown marks shutting_down; `/ready` fails closed.
2. On boot: alembic migrate, `/ready` checks DB + paper/live gates.
3. Run reconcile for any UNKNOWN / blocked orders before accepting new work (see Phase 7 reconciler).
4. Keep `AEGIS_LIVE_ARMED=false` until owner checklist complete.

## 10. Unresolved operational decisions

* On-call / notification channel.
* Retention periods.
* Whether emergencies may auto-cancel or auto-close.
* SLA for unknown-order resolution.

Tracked without invented values in [OWNER_PENDING_DECISIONS.md](OWNER_PENDING_DECISIONS.md). Paper soak evidence: [PAPER_EVALUATION_REPORT.md](PAPER_EVALUATION_REPORT.md).
