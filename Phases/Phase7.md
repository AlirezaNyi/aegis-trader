# Aegis Phase 7 — Toobit Order Execution

Implement the Toobit Spot and Futures execution adapters only after prior phases pass acceptance review.

Verify current official documentation for authentication, order types, client order IDs, order states, partial fills, cancellation, precision, margin, position mode, rate limits, and test-environment availability.

Implement:

* Order Manager state machine.
* Exchange-specific Spot and Futures adapters.
* Pre-submission Risk Engine approval.
* Idempotency and duplicate-order prevention.
* Order status and cancellation handling.
* Partial-fill processing.
* Position, fill, and balance reconciliation.
* Restart recovery.
* Audit logging and execution metrics.
* Bounded retry and rate-limit behavior.

Critical invariants:

* A rejected proposal cannot reach exchange submission.
* An ambiguous timeout must trigger reconciliation before retry.
* Never blindly retry a timed-out order submission.
* Exchange state is authoritative for actual fills and positions.
* Unknown critical account or order state blocks new orders.
* Secrets are isolated from analytical components and never logged.
* No withdrawal permission is requested.
* Live trading remains disabled unless separately and explicitly enabled.

Test rejected orders, timeouts, duplicate requests, partial fills, rate limits, restarts, reconciliation failures, and risk-policy enforcement using mocks or a verified test environment.

Do not place real orders. Report remaining operational risks and stop before live activation.
