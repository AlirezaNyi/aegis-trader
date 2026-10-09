# Aegis — Final System Audit

**Date:** 2026-10-09  
**Scope:** Independent audit against PRD / architecture / risk policy / test plan and [Phases/Final System Audit.md](../Phases/Final%20System%20Audit.md).  
**Method:** Readonly code inspection, module LIMITATIONS, ADRs, plus executed static checks. **No real trades. Live not armed.**

---

## 1. Executive summary

Aegis implements a paper-first pipeline through Phase 8 with hard live gates, Risk-Engine-only APPROVE, fail-closed Supervisor/budgets, NullExecutionPort at boot, withdraw denial, and no blind retry on ambiguous submits. Unit/static verification is green (**137** pytest passed; ruff; mypy **114** files; secret-absence smoke).

**Recommendation: PAPER-TRADING READY.**

Not **READY FOR OWNER REVIEW OF LIVE ACTIVATION** until: owner-approved risk-policy numerics, account-level exchange reconcile (positions/balances), emergency cancel/protect decision, and owner-run backup restore / monitoring channel.

Passing tests do not establish profitability or guarantee safety.

---

## 2. Checklist (audit items 1–15)

| # | Item | Verdict |
| --- | --- | --- |
| 1 | No model/agent bypass of Risk Engine | **PASS** |
| 2 | Live disabled by default; no implicit activation | **PASS** |
| 3 | Missing/stale critical data blocks new orders | **PASS** (draft UNAPPROVED + integrity hard-fails; continuous `validation` stage still stubbed) |
| 4 | Ambiguous submit → reconcile, not blind retry | **PASS** |
| 5 | Duplicate order prevention | **PASS** (clientOrderId + attempt tracker; RP duplicate window when approved) |
| 6 | Credentials inaccessible to analysts/Jev/LLM | **PASS** (Settings on `app.state` is residual T-11 surface) |
| 7 | Withdrawal not requested | **PASS** |
| 8 | Risk policies versioned / deterministic / auditable | **PASS** (financial RP-* remain **UNAPPROVED**) |
| 9 | Orders/fills/positions/balances reconcile vs exchange | **GAP** — per-order get_order only; no inventory reconcile |
| 10 | New-order block ≠ cancel/protect | **PASS** (auto cancel/protect UNAPPROVED / ADR 0006) |
| 11 | Backtest look-ahead + modeled costs | **PASS** (scoped; no funding/latency model) |
| 12 | Paper vs live ledger separation | **PASS** |
| 13 | Toobit/Jev verified or marked unresolved | **PASS** (honest LIMITATIONS remain) |
| 14 | Resource / retries / concurrency / costs bounded | **PASS** |
| 15 | Monitoring / incident / backup / recovery | **PARTIAL** — documented + dry-run tests; E2E restore/notify unproven |

---

## 3. Findings by severity

### CRITICAL
None.

### HIGH
None for paper posture. Live activation remains owner-blocked by design (UNAPPROVED policy, NullExecutionPort).

### MEDIUM

| ID | Finding | Evidence | Impact |
| --- | --- | --- | --- |
| M1 | No account-wide fill/position/balance reconcile against exchange | `reconcile/LIMITATIONS.md`, `interfaces/execution.py` (submit/cancel/get_order only) | Audit item 9 incomplete for live inventory parity |
| M2 | Financial RP-* UNAPPROVED; numeric freshness/size not owner-signed | `risk/policy.py`, `docs/RISK_POLICY.md` | Live APPROVE correctly blocked; cannot claim live-ready |
| M3 | Emergency cancel/protect automation UNAPPROVED | ADR 0006; `OPERATIONS.md` §4.2–4.3 | Kill blocks new orders only; open exposure needs owner action |
| M4 | Backup restore / alert delivery not E2E proven | `READINESS_REPORT.md`; alert dry-run only | Ops “tested” claim incomplete for live |
| M5 | Full `Settings` (incl. secrets) on `app.state` | `main.py`, threat model T-11 | Miswired dump endpoint could leak credentials |

### LOW / INFORMATIONAL

| ID | Finding | Evidence |
| --- | --- | --- |
| L1 | Pipeline `validation` stage stubbed; RiskContext population is caller-owned | `AGENTS.md`; no tick loop in `create_app` |
| L2 | Ops counters may stay zero until call sites record events | `ops/LIMITATIONS.md` |
| L3 | Futures qty/hedge-mode and testnet still unresolved | `exchange/LIMITATIONS.md`, `API_CONTRACTS.md` |
| L4 | Backtest `skip_risk` research path is paper-only | `backtest/runner.py` |
| I1 | Compose binds API/Postgres to localhost; no HTTP auth on ops endpoints | `docker-compose.yml` |
| I2 | Starlette TestClient / httpx deprecation warning | pytest warning |

---

## 4. Test results (executed)

| Check | Result |
| --- | --- |
| `pytest` | **137 passed**, 1 warning (Starlette/httpx), ~0.8s |
| `ruff check src tests` | Pass |
| `mypy src` | Pass (114 source files) |
| `./scripts/check_image_secrets.sh` | Pass |

Targeted safety coverage already in suite includes (non-exhaustive): risk handoff, reject-never-calls-adapter, ambiguous timeout/5xx, idempotent clientOrderId, withdraw forbidden, live guard defaults, paper ledger refuse LIVE, look-ahead guard, ops dry-run / kill-switch ready fail-closed.

**Not executed:** live Toobit submits, real money, E2E pg restore on separate host, Alertmanager delivery, representative 4 vCPU load soak.

---

## 5. Security and operational readiness

| Area | Status |
| --- | --- |
| Live gates | `paper` + `live_armed=false` defaults; startup validation; Risk + OrderManager re-check |
| Credential plane | HMAC confined to `exchange/`; analysts/Jev/LLM do not receive Toobit secrets |
| Withdraw | Hard-denied in adapter |
| Blind retry | Forbidden; UNKNOWN → reconcile |
| Monitoring | `/metrics`, `/ops/alerts/dry-run`, structured JSON logs + correlation middleware |
| Backup | `scripts/backup_postgres.sh` / `restore_postgres.sh` |
| Deploy | Compose resource limits; graceful shutdown; readiness report |

Paper soak on target host is appropriate. Live remains a separate owner decision.

---

## 6. Backtesting and paper-trading limitations

* Look-ahead blocked via as_of candle replay (`backtest/replay.py`); tested.
* Fee/slippage/spread knobs exist (paper defaults 10/5 bps — **engineering**, not risk policy).
* Not modeled: latency, partial fills in backtest path, Futures funding.
* Paper ledger is simulation-only; never mixed with LIVE `ledger_kind`.
* Paper reconcile is local checksum, not exchange-authoritative.

---

## 7. Unresolved assumptions and risks

* Owner-approved risk-policy numeric values (all financial RP-*).
* On-call / notification channel; backup retention.
* Whether emergencies may auto-cancel or auto-close.
* Account inventory REST (balances/positions) for full reconcile.
* Futures quantity unit / hedge-mode / testnet verification.
* Continuous decision-loop wiring with populated RiskContext (no tick loop in `create_app` today).
* Host compromise or Settings serialization → secret exposure.

---

## 8. Recommendation

| Option | Choice |
| --- | --- |
| NOT READY | — |
| **PAPER-TRADING READY** | **Selected** |
| READY FOR OWNER REVIEW OF LIVE ACTIVATION | **Not selected** |

**Rationale:** Safety invariants for paper operation hold under inspection and tests. Live review is premature until M1–M4 and owner policy approval are closed. Do not enable live trading as part of this audit.
