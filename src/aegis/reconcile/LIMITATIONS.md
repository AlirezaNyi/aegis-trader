# Reconcile — limitations (Phase 7)

## Implemented

* `Reconciler.query_and_update` uses `ExecutionPort.get_order(client_order_id)`
* Found → map exchange status onto local `OrderStatus` (OPEN / PARTIALLY_FILLED / FILLED / …)
* Not found → `MISSING`
* Restart recovery reconciles UNKNOWN and non-terminal orders before clearing submit block
  (async, under `OrderManager.lock`). A failed query is isolated and reported in
  `RestartRecoveryResult.errors`; when any query failed, blocks are **not** lifted.
* All reconcile paths are async (`await query_and_update(...)`). `query_and_update`
  never takes `manager.lock` (the manager calls it while holding the lock after an
  ambiguous submit); other callers use `reconcile_open_orders` / restart recovery.
* `Reconciler.reconcile_open_orders(manager)` batches every non-terminal / UNKNOWN
  live order through the verified per-order `get_order` with bounded concurrency
  (`asyncio.Semaphore`, `DEFAULT_RECONCILE_CONCURRENCY = 4` — engineering default to
  stay under exchange rate limits, **not** risk policy). Per-order error isolation:
  a failed query keeps prior local state (UNKNOWN stays UNKNOWN and blocking) and is
  reported in `ReconcileBatchResult.errors` (exception class only). The batch holds
  `manager.lock`, so no submit interleaves with it.
* `ReconcileSweeper` — periodic background sweep (interval
  `DEFAULT_SWEEP_INTERVAL_SECONDS = 30` + uniform jitter up to
  `DEFAULT_SWEEP_JITTER_SECONDS = 5`; engineering defaults, not policy). Stops on
  `ShutdownGate.begin_shutdown()` or `stop()` / `aclose()`. Records
  `OpsMetrics.reconcile_runs` per sweep and `reconcile_failures` per failed query /
  failed sweep; logs `reconcile_sweep` events. Never submits or cancels. **Not wired
  into `create_app`** (parent decides; only meaningful with a live port).
* Sticky reconcile-incomplete block: any failed query in `reconcile_ids` (restart
  recovery, sweep, or batch) records the id in `OrderManager.reconcile_pending_ids`
  and blocks new submits — including failures on `OPEN` / `PARTIALLY_FILLED`
  orders. It is cleared only by `OrderManager.clear_reconcile_incomplete`, called by
  `reconcile_open_orders` / `recover_open_intents` after an error-free batch that
  covered every pending id (pending ids are always re-queried, even if locally
  terminal or intent-only). `cancel`, `apply_exchange_order`, `allow_new_submits`
  and single-id `query_and_update` never clear it.
* `recover_open_intents(load_error=LiveOrderLoadError)` marks persisted live state
  unloadable (`OrderManager.state_load_failed`): new submits stay blocked for the
  process lifetime (no clear method; fix the row and restart).
* `MISSING` results are **reported only** (`ReconcileBatchResult.missing`); blocks
  caused by MISSING are never auto-cleared (owner decision pending).

## Gaps

* Exact Spot/Futures query paths live in `aegis.exchange.paths.VERIFIED_QUERY_PATHS` (official docs cited by API_CONTRACTS)
* Account-wide open-order / position / balance reconcile **not implemented** (audit M1)
* **2026-10-09 re-check:** [docs/API_CONTRACTS.md](../../../docs/API_CONTRACTS.md) still marks Futures private balance/position REST and full account inventory as **Unverified**. No inventory methods were added to `ExecutionPort`; inventing paths is forbidden. Live inventory parity remains blocked until official paths are verified and accepted.
* `MISSING` → `FAILED_TERMINAL` requires owner/policy decision (not automated)
* Emergency cancel-all not automated (ADR 0006)
