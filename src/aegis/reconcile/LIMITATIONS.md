# Reconcile — limitations (Phase 7)

## Implemented

* `Reconciler.query_and_update` uses `ExecutionPort.get_order(client_order_id)`
* Found → map exchange status onto local `OrderStatus` (OPEN / PARTIALLY_FILLED / FILLED / …)
* Not found → `MISSING`
* Restart recovery reconciles UNKNOWN and non-terminal orders before clearing submit block

## Gaps

* Exact Spot/Futures query paths live in `aegis.exchange.paths.VERIFIED_QUERY_PATHS` (official docs cited by API_CONTRACTS)
* Account-wide open-order / position / balance reconcile **not implemented** (audit M1)
* **2026-10-09 re-check:** [docs/API_CONTRACTS.md](../../../docs/API_CONTRACTS.md) still marks Futures private balance/position REST and full account inventory as **Unverified**. No inventory methods were added to `ExecutionPort`; inventing paths is forbidden. Live inventory parity remains blocked until official paths are verified and accepted.
* `MISSING` → `FAILED_TERMINAL` requires owner/policy decision (not automated)
* Emergency cancel-all not automated (ADR 0006)
