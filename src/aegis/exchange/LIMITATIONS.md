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
* Spot submit response classification (`_http.classify_submit_response`, docs re-read
  2026-10-13). Only a **parseable** Toobit error `code` that is not execution-unknown
  is a definitive reject → `ExchangeRejectError(code, msg, http_status)` (HTTP 2xx or
  4xx). Everything else is AMBIGUOUS → `AmbiguousSubmitError` (manager marks
  `UNKNOWN`, blocks new submits, reconciles via `get_order`; never blind-retry):
  execution-unknown codes (`AMBIGUOUS_SUBMIT_ERROR_CODES` in `exchange/errors.py`:
  -1000, -1001, -1006, -1007, -1141 duplicate clientOrderId, -1146); a `code` that is
  present but unparseable (any HTTP status, including 200); any non-2xx without a
  parseable `{code,msg}` body (e.g. bodyless 400/408/499, HTML, `{"msg"}` only);
  2xx non-JSON / non-object; 5xx; transport errors. An ack is never assumed `NEW`:
  missing or unrecognised `status` → `OrderStatus.UNKNOWN` (logged).
* HTTP 429 on any **submit** (Spot and Futures) → `RateLimitedSubmitError`
  (subclass of `AmbiguousSubmitError`, carries `reset_timestamp_ms` from
  `X-Api-Limit-Reset-Timestamp`). Toobit does not document that 429 guarantees
  non-acceptance, so it is never a definitive reject. Exception: Spot `orderTest`
  mode — orderTest never reaches the matching engine, so its 429 / refusals raise
  `OrderTestRejectError` (definitive, non-blocking).
* Futures submit: the Futures v2 error-code table is **not verified**, so no futures
  submit error is treated as definitive. 429, 5xx, transport, any HTTP >= 300, non-JSON
  / non-object body, or a non-success `code` without `data`/`status` →
  `AmbiguousSubmitError` (UNKNOWN + reconcile). Futures therefore never produces a
  `REJECTED` order from an exchange response.
* Spot cancel uses `clientOrderId`; Spot query uses `origClientOrderId` (verified
  2026-10-13). Query `-2013` under 200 or 4xx → not found (`None`).
* Pre-submit precision guard `exchange/precision.py` (`apply_spot_precision`,
  `SymbolFilters`): qty floor to `stepSize`; LIMIT price to `tickSize` (BUY floor,
  SELL ceil); reject `< minQty` / `< minNotional`; missing filter → reject (fail
  closed); MARKET BUY refused (quote-amount precision unverified); MARKET SELL needs
  `reference_price`. Filters are passed in (`submit(intent, filters=...)`); the
  client never fetches metadata. Rejection raises `OrderPrecisionError` before I/O.
* Order-test mode: `ToobitSpotExecutionClient(use_order_test=True)` /
  `build_execution_port(..., use_order_test=True)` only call
  `POST /api/v1/spot/orderTest`; success → `OrderStatus.TEST_ACCEPTED` (terminal,
  no exchange order exists); Futures disabled in that router. Factory still requires
  all live gates + credentials. `scripts/toobit_order_test.py` sends exactly one
  orderTest LIMIT BUY (requires `--i-understand-order-test`; httpx request hook
  refuses any other method/path).
* HTTP 429 on non-submit signed calls (query/cancel) → `RateLimitError` with
  `X-Api-Limit-Reset-Timestamp` when present
* Withdraw `POST /api/v1/account/withdraw` is denied via `assert_withdraw_forbidden`
* `LIVE_SUBMIT_SUPPORTED = True` (code path exists); factory returns `NullExecutionPort` unless live gates + credentials
* Async I/O: Spot / Futures clients, router and the signed helpers in `_http.py` use
  `httpx.AsyncClient` (`await submit / cancel / get_order / test_order`). An injected
  `AsyncClient` (e.g. `httpx.MockTransport` in tests) is owned by the caller and not
  closed by `aclose()`; clients/router support `async with`. Classification,
  path allowlist, withdraw deny and precision guard are unchanged by the conversion.
  `scripts/toobit_order_test.py` uses an async request hook (`_order_test_hook`).
* Sides: `OrderIntent.side` / `Order.side` are `schemas.orders.OrderSide`
  (`BUY`/`SELL`, normalized case/whitespace, anything else rejected at the schema
  boundary). Futures direction words (`LONG`, `BUY_OPEN`, ...) are no longer accepted
  as intent sides; close/hedge position sides go in `extras["position_side"]`. An
  exchange-reported side that is not exactly BUY/SELL (Futures spellings unverified)
  keeps the side we sent.
* Futures order `side` always comes from the Risk-approved `intent.side`; `extras`
  can never change it. `extras["side"]` equal to `intent.side` is ignored; a
  differing (or non-string) value raises `side_map.OrderSideMappingError` (an
  `ExchangeRejectError` with no http/code ⇒ definitive local reject, REJECTED, no
  block) before any request is built. `extras["position_side"]` /
  `extras["positionSide"]` must be a verified value (`LONG`/`SHORT`) and must agree
  when both are present; otherwise the same typed reject.

## Unverified / out of scope

* HTTP status that accompanies a single-order error body (200 vs 4xx) — handled either way
* `ToobitExecutionRouter.submit` does not forward `filters`; whoever wires live submit
  must call the precision guard (or Spot `submit(..., filters=...)`) with fresh
  `exchangeInfo` filters. Without filters the client passes values through unchanged
* Futures submit responses are not classified with `classify_submit_response`
  (futures v2 uses a `code: 200` envelope and its error codes are unverified); every
  futures submit error is ambiguous (see above). A futures order that the exchange
  definitively refused will therefore block new submits until reconcile shows it
  absent and `allow_new_submits` is called (conservative by design)
* Cancel HTTP 5xx → `ExchangeError` (status unknown), not a typed reject

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
