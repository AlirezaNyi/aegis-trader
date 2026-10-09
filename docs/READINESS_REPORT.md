# Aegis — Phase 8 Readiness Report

**Date:** 2026-10-09
**Posture:** Paper default. Live trading **not** activated.
**Scope:** Deployment readiness per [Phases/Phase8.md](../Phases/Phase8.md).

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

## Unverified / residual risks

| Item | Status |
| --- | --- |
| Representative load measurement on 4 vCPU / 8 GB host | Not run in this phase artifact |
| End-to-end backup restore on a separate non-prod instance | Scripts provided; owner must execute |
| Dependency CVE / image scan CI | Owner CI responsibility |
| Prometheus/Grafana Alertmanager delivery | Optional; not in Compose |
| On-call notification channel | UNRESOLVED |
| Backup retention count | UNAPPROVED |
| Auto cancel/close on incident | UNAPPROVED |
| Owner-approved risk-policy numeric limits | UNAPPROVED — blocks live enforcement |
| Live Toobit client constructed in `create_app` | Intentionally **not** — `NullExecutionPort` |
| Full instrumented counters under production traffic | Counters exist; call-site wiring may be partial until soak |
| Unauthenticated ops HTTP on localhost bind | Compose binds `127.0.0.1` for API/Postgres; reverse-proxy/auth still owner choice for remote hosts |
| Host load / CVE image scan | Not executed in this artifact |

## Explicit non-goals (Phase 8 stop line)

* Real-money activation.
* Silent live arming via Compose or deploy scripts.
* Blind retry of uncertain orders.
* Inventing risk-policy numbers or unverified Toobit/Jev APIs.

## Conclusion

Phase 8 **deployment readiness scaffolding** is accepted for **paper** operation. Live activation remains a separate owner decision after checklist completion and residual risks above are accepted or closed.
