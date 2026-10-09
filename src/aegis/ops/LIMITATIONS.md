# Ops module limitations (Phase 8)

* Prometheus scrape is in-process text only; no Grafana/Alertmanager stack in Compose by default.
* Alert evaluator is **dry-run** — notification channel remains UNRESOLVED (see `docs/OPERATIONS.md`).
* Disk free bytes are not auto-sampled; inject via `OpsMetrics.set_disk_free` or leave unset.
* Model/order/risk counters start at zero until call sites record events; absence of traffic is not a green signal.
* Backup/restore scripts use `pg_dump` / `pg_restore` against Compose Postgres; retention count remains UNAPPROVED.
* Live trading is never armed by ops tooling.
