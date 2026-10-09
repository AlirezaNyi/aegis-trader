# Aegis — Phase 8 Readiness Report

**Date:** 2026-10-09
**Posture:** Paper default. Live trading **not** activated.
**Scope:** Deployment readiness per [Phases/Phase8.md](../Phases/Phase8.md).
**Updated:** 2026-10-09 paper-evidence stage (audit M4/M5 follow-up).

## Verified in-repo

| Item | Evidence |
| --- | --- |
| Docker Compose + Dockerfile | `docker-compose.yml`, `Dockerfile` |
| CPU/memory limits | Compose `mem_limit` / `cpus` / `deploy.resources` for `aegis` and `postgres` |
| Concurrency / timeouts | Compose env + settings (`AEGIS_ANALYST_CONCURRENCY`, Jev/supervisor timeouts) |
| Health / ready | `GET /health`, `GET /ready` (phase 8; shutting_down fails ready) |
| Structured logs + correlation IDs | `aegis.logging` + `CorrelationIdMiddleware` |
| Ops metrics scrape | `GET /metrics` (`aegis.ops.OpsMetrics`) |
| Alert dry-run | `GET /ops/alerts/dry-run` |
| Graceful shutdown | lifespan `ShutdownGate`; uvicorn `--timeout-graceful-shutdown` |
| Backup / restore scripts | `scripts/backup_postgres.sh`, `scripts/restore_postgres.sh` |
| Secret absence smoke | `scripts/check_image_secrets.sh` |
| Paper-to-live checklist | `docs/OPERATIONS.md` §7 — remains owner-gated |
| Live remains off | Compose + Dockerfile defaults: paper, `live_armed=false` |
| Runtime public state (M5) | `app.state.runtime` + `database_probe`; full `Settings` not on `app.state`; secrets cleared on cycle deps |
| Paper soak CLI evidence | `scripts/paper_soak.py --once` → finalized `ADAUSDT` 1m bar; Risk `REJECT` on `NO_TRADE`; live gates off |

## M4 ops proof log (2026-10-09)

| Check | Result |
| --- | --- |
| Compose Postgres available for backup | **Not run** — `docker compose ps` showed no services at evidence time (prior `compose up --build` failed on network/DNS during image build) |
| `scripts/backup_postgres.sh` | Script present; requires running `postgres` service — **not executed** |
| `scripts/restore_postgres.sh` on separate non-prod host | **Not executed** — owner must prove off-box |
| Alert dry-run HTTP | Covered by `tests/test_ops.py` (`notification_delivered=false`) |
| Real notification channel delivery | **Not proven** — channel still UNRESOLVED ([OWNER_PENDING_DECISIONS.md](OWNER_PENDING_DECISIONS.md)) |
| Backup retention count | Still **UNAPPROVED** |

Honest residual: M4 remains **open** until the owner runs backup→restore on a non-prod instance and wires a real alert channel.

## Unverified / residual risks

| Item | Status |
| --- | --- |
| Representative load measurement on 4 vCPU / 8 GB host | Not run in this phase artifact |
| End-to-end backup restore on a separate non-prod instance | Scripts provided; **still unproven** (see M4 log) |
| Dependency CVE / image scan CI | Owner CI responsibility |
| Prometheus/Grafana Alertmanager delivery | Optional; not in Compose |
| On-call notification channel | UNRESOLVED |
| Backup retention count | UNAPPROVED |
| Auto cancel/close on incident | UNAPPROVED |
| Owner-approved risk-policy numeric limits | Approved as version **`1.0`** (see `docs/RISK_POLICY.md`); live still requires explicit arming + inventory reconcile |
| Account inventory reconcile (audit M1) | Paths still **Unverified** in `API_CONTRACTS.md` (re-checked 2026-10-09); no invented inventory client |
| Live Toobit client constructed in `create_app` | Intentionally **not** — `NullExecutionPort` |
| Paper decision cycle | `app.state.run_paper_cycle` / `PaperCycleDeps` for finalized-candle paper soak; live mode refused; no LLM-on-every-tick background loop |
| Paper soak runner | Optional `AEGIS_PAPER_SOAK_ENABLED` (default false): public REST poll → one cycle per new final bar; `NullExecutionPort` remains |
| SE-* paper evaluation thresholds | UNAPPROVED — soak is learning only ([PAPER_EVALUATION_REPORT.md](PAPER_EVALUATION_REPORT.md)) |
| Full instrumented counters under production traffic | Counters exist; call-site wiring may be partial until soak |
| Unauthenticated ops HTTP on localhost bind | Compose binds `127.0.0.1` for API/Postgres; reverse-proxy/auth still owner choice for remote hosts |
| Host load / CVE image scan | Not executed in this artifact |

## Explicit non-goals (Phase 8 stop line)

* Real-money activation.
* Silent live arming via Compose or deploy scripts.
* Blind retry of uncertain orders.
* Inventing risk-policy numbers or unverified Toobit/Jev APIs.

## Conclusion

Phase 8 **deployment readiness scaffolding** is accepted for **paper** operation. Paper-evidence follow-up closed M5 on the HTTP surface, re-confirmed M1 as Unverified (no invented paths), recorded soak evidence, and left M4 ops E2E **owner-proven**. Live activation remains a separate owner decision after checklist completion and residual risks above are accepted or closed.
