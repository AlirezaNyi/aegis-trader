# Aegis Phase 8 — Deployment and Production Readiness

Prepare Aegis for an initial server of approximately 4 vCPU, 8 GB RAM, and 80 GB SSD without a dedicated GPU.

Implement:

* Docker Compose deployment.
* CPU, memory, disk, worker, and concurrency limits.
* Health and readiness checks.
* Structured logs and correlation IDs.
* Metrics for data freshness, model latency, token/API cost, risk decisions, order lifecycle, and reconciliation.
* Alerts for stale data, provider failures, unknown order state, position discrepancies, disk exhaustion, and emergency-stop activation.
* Graceful shutdown and restart recovery.
* Database backup and tested restore procedures.
* Secret injection and rotation instructions.
* Container/dependency security checks.
* Operational runbooks and rollback procedures.
* A paper-to-live readiness checklist.

Avoid running LLMs on every market tick. Use event-driven analysis, caching, timeouts, and explicit budgets.

Do not activate live trading automatically. A model outage or data outage must not trigger uncontrolled retries. New orders must be blocked when critical state is uncertain, while procedures for protecting existing positions remain separately defined.

Measure resource usage under representative load. Test restart recovery, reconciliation, backup restoration, and alerts. Produce a readiness report that explicitly lists unverified items and unresolved risks. Stop before real-money activation.
