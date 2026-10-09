# Reconcile — limitations (Phase 7)

## Implemented

* `Reconciler.query_and_update` uses `ExecutionPort.get_order(client_order_id)`
* Found → map exchange status onto local `OrderStatus` (OPEN / PARTIALLY_FILLED / FILLED / …)
* Not found → `MISSING`
* Restart recovery reconciles UNKNOWN and non-terminal orders before clearing submit block

## Gaps

* Exact Spot/Futures query paths live in `aegis.exchange.paths.VERIFIED_QUERY_PATHS` (official docs cited by API_CONTRACTS)
* Account-wide open-order / position / balance reconcile not implemented
* `MISSING` → `FAILED_TERMINAL` requires owner/policy decision (not automated)
* Emergency cancel-all not automated (ADR 0006)
