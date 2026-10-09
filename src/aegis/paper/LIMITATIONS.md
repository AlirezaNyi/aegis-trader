# Paper trading limitations (Phase 6)

## Simulation assumptions (not risk policy)

* `paper_fee_bps` and `paper_slippage_bps` are **engineering defaults for simulation only**.
* They are **not** owner-approved risk-policy values and must not be read as live fee schedules.
* Default fill model: immediate **full** fill at limit price (when set) or `mid ± slippage_bps`.
* Partial fills, latency, queue position, and Futures funding are **not** modeled in Phase 6.

## Ledger isolation

* PaperBroker writes only `ledger_kind=paper`.
* LIVE ledger rows are refused; Phase 6 never writes live account state.
* Paper balances/positions are independent of any exchange account.

## No exchange connectivity

* PaperBroker must **never** import or call Toobit private trade APIs or construct live submit clients.
* Live submit remains Phase 7+ and gated by `trading_mode` + `live_armed` + `kill_switch`.

## Risk handoff

* Production path: `submit_from_risk_decision` requires non-expired Risk **APPROVE** and matching `proposal_id` / correlation IDs (INV-01).
* Fill application is private (`_apply_approved_intent`); there is no public bypass around Risk.

## Restart recovery

* In-memory snapshot + checksum equality reconcile balances/positions.
* Postgres persistence uses `orders` / `fills` / `positions` / `paper_balances` with `ledger_kind='paper'`.
* Reconcile is a simple checksum / equality check — not exchange-authoritative live reconcile.
