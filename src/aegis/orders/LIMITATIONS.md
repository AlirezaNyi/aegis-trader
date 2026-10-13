# Order Manager — limitations (Phase 7)

## Implemented

* Risk APPROVE + proposal_id / correlation_id bind before adapter submit
* Live gates via `assert_live_execution_allowed` / kill switch / `trading_ready`
* `ledger_kind=LIVE` only — paper refused
* Idempotent one submit attempt per `client_order_id`
* Ambiguous submit → `UNKNOWN` → reconciler (`get_order`); new submits blocked;
  never blind second submit. Ambiguous = timeout / transport / HTTP 5xx / HTTP 429
  (`RateLimitedSubmitError` or a bare `RateLimitError`) / non-2xx without a
  parseable Toobit `{code,msg}` body / unparseable `code` (even under HTTP 200) /
  execution-unknown codes / any futures submit error (futures error codes unverified).
  The manager re-checks every `ExchangeRejectError` with
  `exchange.errors.is_definitive_submit_reject` (defence in depth).
* Definitive reject → terminal `REJECTED`; does **not** block later submits; never
  auto-retried; the same `client_order_id` is never resubmitted. Only: a parseable
  Spot error `code` not in `AMBIGUOUS_SUBMIT_ERROR_CODES` under a non-429, non-5xx
  status; local pre-submit guards (`OrderPrecisionError`, Futures-disabled router);
  Spot `orderTest` refusals (`OrderTestRejectError`, nothing reaches the engine)
* Any other post-attempt submit exception also maps to `UNKNOWN` + block
* Ack whose status maps to `UNKNOWN` (missing/unrecognised) → same as ambiguous:
  block + reconcile
* If the reconciler raises during post-submit reconcile, the order stays `UNKNOWN`,
  submits stay blocked, and the exception is logged (type only), not propagated
* `TEST_ACCEPTED` (Spot orderTest mode) is terminal and non-blocking; no exchange
  order exists for it
* Partial-fill / cancel status applied from exchange `get_order`
* `cancel` recomputes critical flags after storing the ack: an `UNKNOWN` (or
  `MISSING`) cancel result blocks new submits until reconciled. Cancel exceptions
  propagate to the caller and leave local state unchanged
* New submits blocked while any order is `UNKNOWN` **or** `MISSING` (one not-found query is not enough to reopen flow; use `allow_new_submits` only after clean state)
* Optional Postgres upsert for live rows (`persist.upsert_live_order`); refuses
  non-live `ledger_kind` (`LiveLedgerKindError`) and `TEST_ACCEPTED` orders
  (`TestOrderPersistError`) — orderTest results never enter the live ledger

## Gaps

* No automatic emergency cancel/close (ADR 0006)
* Owner risk policy **v1.0** is APPROVED for paper enforcement; live remains disarmed.
  Order Manager does not invent numeric limits — it honors Risk decisions only.
* Position / balance account-level reconcile is not part of Order Manager
* Live client construction left to parent (`build_execution_port`); `create_app` keeps `NullExecutionPort` / `live_submit_client=not_constructed`
