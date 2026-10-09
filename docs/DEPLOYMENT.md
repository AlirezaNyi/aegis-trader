# Aegis — Deployment

**Version:** 1.0
**Status:** Proposed (Phase 0)
**Target host:** ~4 vCPU, 8 GB RAM, 80 GB SSD, no GPU
**Package / service id:** `aegis`

## 1. Principles

* Paper Trading is the default in every example environment.
* No deployment command may silently enable live trading.
* Secrets are injected at runtime; never committed.
* Resource limits and bounded concurrency protect a small host.
* Toobit Agent Trade Kit / MCP order tools are not part of this deployment.

## 2. Proposed Compose topology

```text
services:
  aegis:        # FastAPI app + in-process workers
  postgres:     # system of record
  # optional later:
  # prometheus
  # grafana
```

**Proposed** resource ceilings (tunable; not financial risk policy):

| Service | CPU | Memory |
| --- | --- | --- |
| `aegis` | leave headroom for OS | e.g. ≤ 4–5 GB |
| `postgres` | shared | e.g. ≤ 2 GB |
| monitoring | optional | small |

Exact Compose files are Phase 1/8 deliverables.

## 3. Configuration surface

| Variable (example names) | Purpose | Default posture |
| --- | --- | --- |
| `AEGIS_TRADING_MODE` | `paper` / `live` | `paper` |
| `AEGIS_LIVE_ARMED` | explicit arm | `false` |
| `AEGIS_KILL_SWITCH` | block new orders when true | `false` |
| `DATABASE_URL` | Postgres | required |
| `TOOBIT_API_KEY` / `TOOBIT_API_SECRET` | Exchange | empty in paper-only |
| `TYPESAFE_API_KEY` | Jev | optional; mock if absent |
| `LLM_*` | Supervisor provider | optional until chosen |

`.env.example` must contain placeholders only.

## 4. Networking (**Proposed**)

* Postgres not published to public internet.
* Owner control endpoints bound to localhost or private interface until hardened.
* Outbound HTTPS to `api.toobit.com`, `stream.toobit.com`, `api.typesafe.ai`, and chosen LLM provider only as configured.

## 5. Live mode deployment rules

Live requires:

1. Owner-approved risk policy version loaded.
2. Credentials with least privilege; IP allowlist where supported.
3. Withdrawal not used by Aegis; prefer exchange-side denial if available (**Unresolved**).
4. Monitoring and backup verified.
5. Explicit `AEGIS_TRADING_MODE=live` **and** `AEGIS_LIVE_ARMED=true`.
6. Completed checklist in [OPERATIONS.md](OPERATIONS.md).

## 6. Rollback

* Prefer immutable image tags.
* Rollback = redeploy previous image + known DB migration compatibility.
* After rollback, verify reconciliation and keep live disarmed until checks pass.

## 7. Security checks before declare ready

* Dependency audit / image scan.
* No secrets in image layers or compose files.
* Confirm kill switch and paper default on boot.

## 8. Phase 8 acceptance link

See [Phases/Phase8.md](../Phases/Phase8.md). Production readiness ≠ live activation.
