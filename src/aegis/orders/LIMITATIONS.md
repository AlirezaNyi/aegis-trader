# Order Manager — limitations (Phase 7)

## Implemented

* Risk APPROVE + proposal_id / correlation_id bind before adapter submit
* Live gates via `assert_live_execution_allowed` / kill switch / `trading_ready`
* `ledger_kind=LIVE` only — paper refused
* Idempotent one submit attempt per `client_order_id`
* Ambiguous submit (timeout/5xx/transport) → `UNKNOWN` → reconciler; never blind second submit
* Any other post-attempt submit exception also maps to `UNKNOWN` + block
* Partial-fill / cancel status applied from exchange `get_order`
* New submits blocked while any order is `UNKNOWN` **or** `MISSING` (one not-found query is not enough to reopen flow; use `allow_new_submits` only after clean state)
* Optional Postgres upsert for live rows (`persist.upsert_live_order`)

## Gaps

* No automatic emergency cancel/close (ADR 0006)
* Numeric risk-policy limits remain UNAPPROVED — Order Manager does not invent them
* Position / balance account-level reconcile is not part of Order Manager
* Live client construction left to parent (`build_execution_port`); `create_app` keeps `NullExecutionPort` / `live_submit_client=not_constructed`
