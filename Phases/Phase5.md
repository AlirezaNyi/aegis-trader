# Aegis Phase 5 — Deterministic Risk Engine

Implement the deterministic Risk Engine as the only authority allowed to approve or reject a proposed new trade.

Enforce explicit, versioned checks for:

* Paper/live mode and kill switch.
* Market and instrument allowlists.
* Account and balance availability.
* Data freshness and integrity.
* Position sizing and notional exposure.
* Aggregate exposure and open-order limits.
* Futures leverage, margin, and liquidation-related constraints.
* Daily loss and drawdown limits.
* Fees, spread, liquidity, slippage, and funding where applicable.
* Stop-loss and exit-protection policy.
* Symbol precision and minimum order sizes.
* Strategy/configuration version validity.
* Duplicate proposals and idempotency.
* Account and exchange-state consistency.

Every decision must return APPROVE or REJECT, policy version, rule IDs, rejection reasons, validated parameters, timestamp, expiry, and correlation ID.

Rules:

* Missing, stale, invalid, or inconsistent critical state means REJECT for new orders.
* No model may override a hard limit.
* Do not silently invent financially safe leverage or loss limits.
* Keep blocking new orders separate from canceling orders and protecting open positions.
* Treat stop-loss execution as uncertain, not guaranteed.
* Make decisions deterministic for the same input and policy version.

Write tests for every rule, boundary values, numeric precision, and the invariant that rejected proposals never reach Order Manager.

Do not enable live trading. Report proposed policy values requiring owner approval and stop after this phase.
