# Exchange adapter — limitations (Phase 7)

## Verified and implemented

* REST base `https://api.toobit.com`
* Auth: `X-BB-APIKEY`, HMAC-SHA256 lowercase hex, `timestamp`, `recvWindow`
* Spot submit: `POST /api/v1/spot/order` (optional `orderTest`)
* Futures submit: `POST /api/v2/futures/order` with mandatory `newClientOrderId`
* Spot query/cancel: `GET` / `DELETE /api/v1/spot/order` (official Spot Account/Trade page cited by `docs/API_CONTRACTS.md` for Phase 7)
* Futures query/cancel: `GET` / `DELETE /api/v2/futures/order` (official USDT-M API v2 page)
* Paths gated in `VERIFIED_SUBMIT_PATHS` / `VERIFIED_QUERY_PATHS`
* Timeout / HTTP 5xx on submit → `AmbiguousSubmitError` (never fake reject)
* HTTP 429 → `RateLimitError` with `X-Api-Limit-Reset-Timestamp` when present
* Withdraw `POST /api/v1/account/withdraw` is denied via `assert_withdraw_forbidden`
* `LIVE_SUBMIT_SUPPORTED = True` (code path exists); factory returns `NullExecutionPort` unless live gates + credentials

## Unverified / out of scope

* Spot MARKET BUY quantity-as-quote-notional semantics — passed through as `intent.quantity`; callers must supply exchange-correct units
* Futures quantity unit (contracts vs token / `contractMultiplier`) — unresolved in API_CONTRACTS
* Hedge-mode requirement for `positionSide` — unverified
* Futures orderTest equivalent — unverified
* Batch orders, amend, algo/plan orders, leverage set — not implemented in Phase 7
* Balance / position REST inventory — **Unverified** (re-checked 2026-10-09 in API_CONTRACTS); reconcile remains per-order `get_order` only (audit M1 open)
* Live client is **not** constructed by `create_app` in this phase — parent wires factory
* Agent Trade Kit / MCP order tools — forbidden (ADR 0007)

## Safety

* Secrets stay in this package; never log api keys, secrets, or signatures
* Paper ledger never mixes with live (`ledger_kind`)
