# Aegis — API Contracts and External Verification Log

**Version:** 1.0
**Status:** Proposed (Phase 0)
**Verification date:** 2026-10-09
**Method:** Read official documentation only; no live API calls, no credentials used

Sources:

* Toobit: https://api-docs.toobit.com/ (including basic information, spot market data, USDT-M market data, spot account/trade, USDT-M API v2, WebSocket pages, introduction)
* TypeSafe Jev: https://docs.typesafe.ai/api , https://docs.typesafe.ai/models , https://docs.typesafe.ai/sdk/python

## 1. Legend

| Label | Meaning |
| --- | --- |
| **Verified** | Present in official docs on the verification date |
| **Unverified** | Not confirmed; use interface + mock; do not invent |
| **Internal** | Aegis-owned contract |

---

## 2. Internal contracts (Pydantic targets)

### 2.1 TradeProposal (Internal)

| Field | Type | Required |
| --- | --- | --- |
| proposal_id | UUID / string | yes |
| correlation_id | string | yes |
| instrument | symbol + market_type | yes |
| direction | long/short/flat as applicable | when trading |
| timeframe | `1m` \| `5m` \| `15m` | yes |
| strategy_id / strategy_version | string | yes |
| action | `BUY` \| `SELL` \| `HOLD` \| `NO_TRADE` | yes |
| entry_conditions | object | when BUY/SELL |
| expires_at | timestamptz | yes |
| stop_loss / take_profit | optional decimals | per policy |
| sizing / leverage | optional | never authoritative over Risk |
| evidence_refs | list | yes |
| analyst_results | list including unavailable | yes |
| jev_result | object or unavailable | yes |
| uncertainty / invalidation | object | yes |
| supervisor_model_meta | object | yes |
| created_at | timestamptz | yes |

**Mapping note:** Internal `BUY`/`SELL` are proposal intents. They do **not** map 1:1 to Toobit Spot `BUY`/`SELL` or Futures `side`+`positionSide`. Mapping is performed only inside Order Manager / adapter after Risk APPROVE.

### 2.2 RiskDecision (Internal)

See [RISK_POLICY.md](RISK_POLICY.md) §4.

### 2.3 EvidencePackage (Internal)

`schema_version`, `created_at`, `correlation_id`, feature refs, analyst evidence list, instrument, timeframe, as_of timestamps.

### 2.4 Normalized market event (Internal)

`exchange`, `market_type`, `symbol`, `event_time`, `received_at`, `channel`, `payload`, `sequence` if available.

---

## 3. Toobit — verified facts

### 3.1 General

| Fact | Status |
| --- | --- |
| REST base `https://api.toobit.com` | Verified |
| Timestamps in milliseconds | Verified |
| API key header `X-BB-APIKEY` | Verified |
| SIGNED endpoints: HMAC-SHA256; signature lowercase hex; param order must match | Verified |
| `totalParams` = query string concatenated with body; v2 JSON: `queryString + jsonBody` compact | Verified |
| `timestamp` required on SIGNED; `recvWindow` default 5000, max 60000 | Verified |
| HTTP 429 rate limit; headers `X-Api-Limit-Status`, `X-Api-Limit`, `X-Api-Limit-Reset-Timestamp` | Verified |
| Base limits: 3000 REQUEST_WEIGHT / 1 minute; 60 ORDERS / 2 seconds | Verified |
| HTTP 5XX: do **not** treat as definitive failure; execution may have succeeded | Verified |
| Prefer WebSocket for market data to reduce REST weight | Verified |
| IP restrictions recommended on API keys | Verified |
| Withdrawal endpoint exists: `POST /api/v1/account/withdraw` — **Aegis must never call** | Verified existence |
| Separate public testnet/sandbox | **Unverified** (not found) |
| Spot order test: `POST /api/v1/spot/orderTest` validates without matching engine | Verified |
| Futures orderTest equivalent | **Unverified** |
| Default API key permission wording conflict (“Enable Reading” vs all secure routes) | **Unresolved** (docs disagree) |

### 3.2 Public market data

| Endpoint | Notes | Status |
| --- | --- | --- |
| `GET /api/v1/time` | Server time | Verified |
| `GET /api/v1/exchangeInfo` | Symbols + contracts; `rateLimits` field deprecated/removal warned | Verified |
| `GET /quote/v1/klines` | Intervals include 1m, 5m, 15m; limit default/max 1000; without start/end returns latest only | Verified |
| `GET /quote/v1/depth` | Order book | Verified |
| `GET /quote/v1/trades` | Recent trades | Verified |
| `GET /quote/v1/markPrice` | Mark price | Verified |
| `GET /quote/v1/markPrice/klines` | Mark klines | Verified |
| `GET /api/v1/futures/fundingRate` | Funding | Verified |
| `GET /api/v1/futures/historyFundingRate` | Funding history | Verified |
| `GET /quote/v1/openInterest` | OI | Verified |
| `GET /api/v1/futures/riskLimits` | Exchange risk tiers; includes `maxLeverage` examples — exchange metadata only | Verified |
| Market WS base `wss://stream.toobit.com` path `/quote/ws/v1` | Topics include `kline_$interval`, `trade`, `depth`, `realtimes` | Verified |
| Kline intervals list includes 1m, 5m, 15m | Verified |

Symbol examples: Spot `ETHUSDT`; Futures contract `BTC-SWAP-USDT`.

### 3.3 Spot trading (**Verified** paths)

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/spot/order` | New order; side `BUY`/`SELL`; optional `newClientOrderId` |
| `POST /api/v1/spot/orderTest` | Validate only |
| `POST /api/v1/spot/batchOrders` | Batch up to 20 same symbol |
| Query/cancel/open/history endpoints | Documented on Spot Account/Trade page (implement from that page in Phase 7) |

Spot API v2 order shape: **Unverified** for Aegis design until read and accepted.

### 3.4 Futures trading

| Endpoint | Purpose | Status |
| --- | --- | --- |
| `POST /api/v2/futures/order` | New regular order; JSON body; mandatory `newClientOrderId`; `side` BUY/SELL + `positionSide` LONG/SHORT; `LIMIT`/`MARKET`; optional embedded TP/SL | Verified |
| `POST /api/v2/futures/batch-orders` | Batch | Verified |
| `POST /api/v2/futures/order/update` | Amend | Verified |
| Cancel / get / open / history / user-trades under `/api/v2/futures/*` | Documented on v2 page | Verified |
| `POST /api/v2/futures/algo-order` | Plan / conditional orders | Verified |
| `POST /api/v2/futures/leverage` | Set leverage | Verified |
| `POST /api/v1/futures/order` with `BUY_OPEN` style sides | Appears in code examples | Verified as documented example; **Proposed** prefer v2 for new work |
| Futures quantity unit: contracts vs token vs `contractMultiplier` | Docs conflict / incomplete | **Unresolved** |
| Hedge mode requirement for `positionSide` | | **Unverified** |
| Futures private balance/position REST beyond v2 snippets | Partial on docs; full inventory **Unverified** for design freeze | Interface until re-check |
| Futures private user stream parity with Spot `userDataStream` | Spot listenKey verified; futures private WS details | **Unverified** |

### 3.5 Order statuses (**Verified**)

`PENDING_NEW`, `NEW`, `PARTIALLY_FILLED`, `FILLED`, `CANCELED`, `PENDING_CANCEL`, `REJECTED`.

Plan order statuses include `ORDER_NEW`, `ORDER_FILLED`, `ORDER_REJECTED`, `ORDER_CANCELED`, `ORDER_FAILED`.

### 3.6 Spot user data stream (**Verified**)

* `POST /api/v1/userDataStream` create listenKey (60 minutes)
* `PUT` keepalive (~every 30 minutes recommended)
* `DELETE` close

---

## 4. Jev / TypeSafe — verified facts

| Fact | Status |
| --- | --- |
| `POST https://api.typesafe.ai/v1/systemone` | Verified |
| Auth: `Authorization: Bearer <API_KEY>`; env `TYPESAFE_API_KEY` | Verified |
| Body: `state`, `model`, `questions` map | Verified |
| Question types: `noul`, `choice` (≤255 options), `score` (2–10 levels) | Verified |
| Choice/Score include probabilities and `confidence` (distribution concentration, not P(profit)) | Verified |
| `model` alias `jev-latest` → `jev-1.13.0` as of models page on verification date | Verified (aliases can move) |
| Response includes versioned `model` and `usage` | Verified |
| Context: 64k per request; 32k for state + longest question | Verified |
| Published rate limits 100k tokens/s and 80 req/s — docs say may change | Verified as published; treat as volatile |
| Published pricing on models page — cite page/date; do not hard-code as permanent constant in code comments as policy | Verified as published |
| Errors: 401, 422, 429, 529 | Verified |
| Official Python SDK `typesafe_sdk` with `system_one` | Verified |
| SDK default retries on 429/529 — Aegis must bound retries vs evidence freshness | Verified behavior + Aegis constraint |
| Official integration via AI gateway / OpenRouter mirrors | Documented as alternate SDK base_url examples; **Proposed reject** for Aegis (use official TypeSafe host only) |
| Jev places exchange orders | Not a capability — returns structured answers only | Verified non-capability |

### 4.1 Aegis Jev adapter contract (Internal)

Inputs: redacted EvidencePackage (size within context budgets).

Outputs: normalized answers + `status` + model version + latency + usage; or `unavailable`.

Never include: Toobit keys, signatures, withdraw addresses, full account credentials.

---

## 5. LLM Supervisor provider contract (Internal)

* Provider-agnostic interface: `complete_structured(prompt, schema, timeout, budget)`.
* Output must validate against TradeProposal schema or fail closed to NO_TRADE.
* Provider choice, timeouts, and budgets: **UNAPPROVED** owner decision.

---

## 6. Owner control HTTP surface (**Proposed**)

Local/authenticated endpoints only, e.g.:

* `GET /health`
* `GET /ready`
* `POST /control/kill-switch` (owner)
* `POST /control/live-arm` (owner)

No public internet exposure without additional hardening (**Unresolved** deployment network design).

---

## 7. Re-verification checklist (before Phase 2 and Phase 7)

1. Re-read Toobit basic information, Spot/Futures market data, Spot trade, Futures v2 trade, WS pages.
2. Re-read TypeSafe API + models pages; pin model id if thresholds depend on a version.
3. Update this log with date and diffs.
4. Any still-unverified path remains interface + mock.
