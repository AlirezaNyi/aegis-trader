# Aegis — Risk Policy

**Version:** 1.0  
**Policy status:** OWNER-APPROVED (paper enforcement)  
**Authority:** Project owner  
**Approved by:** Project owner (chat directive “approve”, 2026-10-09)  
**Related:** [PRD.md](PRD.md) §10, §21; [SRS.md](SRS.md); [ARCHITECTURE.md](ARCHITECTURE.md); `src/aegis/risk/owner_v1.py`

Live trading remains **disarmed** by default. Approving this policy does **not** set `AEGIS_TRADING_MODE=live` or `AEGIS_LIVE_ARMED=true`.

## Owner inputs (v1.0)

| Input | Value |
| --- | --- |
| Markets | Spot and Futures |
| Risk capital | 10 USDT |
| Daily loss limit | 2.2 USDT |
| Symbols | ADA, BTC, TRX, BNB, ETH → `*USDT` pairs |

### Assumptions applied when owner said “approve” without answering follow-ups

| Topic | Assumed value |
| --- | --- |
| Futures leverage | `1` (no leverage) |
| Directions | `long` only (spot + futures) |
| Order types | `LIMIT` only; TIF `GTC` |
| Emergency cancel/protect | `false` (manual only; ADR 0006) |
| Strategy allowlist | `aegis-default`, `demo` (+ versioned keys) |

To change any assumption, bump to policy **1.1+** and re-approve.

### Daily loss / drawdown accounting (v1.0)

* Quote currency: **USDT**
* Day boundary: **UTC** calendar day
* Daily loss: realized + unrealized PnL from equity at 00:00 UTC
* Drawdown: peak-to-trough on the same paper/live ledger equity series
* After hitting daily loss: cooldown **24h** (`RP-COOLDOWN-AFTER-LOSS`)

## 1. Principles

1. The deterministic Risk Engine is the only authority that may APPROVE a new trade.
2. LLMs, Jev, analysts, and strategies cannot modify this policy or override REJECT.
3. Missing, stale, inconsistent, or unknown critical state ⇒ REJECT new orders.
4. Stop-loss instructions are not a guarantee of fill price or fill occurrence.
5. Blocking new orders, canceling open orders, and protecting/closing positions are separate procedures.
6. Implementation must not invent “safe” leverage, loss, or size defaults and mark them approved without an owner-signed version.

## 2. Parameter register

| Parameter ID | Description | Unit | Value | Status |
| --- | --- | --- | --- | --- |
| RP-MODE-DEFAULT | Default trading mode | enum | `paper` | Engineering default |
| RP-LIVE-ARMED-DEFAULT | Live arm flag default | bool | `false` | Engineering default |
| RP-KILL-SWITCH-DEFAULT | Kill switch default | bool | `false` | Engineering default |
| RP-INSTRUMENT-ALLOWLIST-SPOT | Allowed Spot symbols | set | `ADAUSDT`, `BTCUSDT`, `TRXUSDT`, `BNBUSDT`, `ETHUSDT` | APPROVED |
| RP-INSTRUMENT-ALLOWLIST-FUTURES | Allowed Futures symbols | set | same as Spot | APPROVED |
| RP-MARKETS-ALLOWED | Allowed market types | set | `spot`, `futures` | APPROVED |
| RP-DIRECTIONS-ALLOWED | Allowed directions | map | spot:`long`; futures:`long` | APPROVED |
| RP-MAX-LEVERAGE | Max Futures leverage | ratio | `1` | APPROVED |
| RP-MAX-NOTIONAL-PER-ORDER | Max notional per new order | USDT | `2` | APPROVED |
| RP-MAX-NOTIONAL-PER-INSTRUMENT | Max notional per instrument | USDT | `4` | APPROVED |
| RP-MAX-AGGREGATE-NOTIONAL | Max aggregate notional | USDT | `10` | APPROVED |
| RP-MAX-OPEN-POSITIONS | Max open positions | count | `2` | APPROVED |
| RP-MAX-PENDING-ORDERS | Max pending/open orders | count | `2` | APPROVED |
| RP-DAILY-LOSS-LIMIT | Max daily loss | USDT | `2.2` | APPROVED |
| RP-DRAWDOWN-LIMIT | Max drawdown from peak | USDT | `3.0` | APPROVED |
| RP-SIZING-METHOD | Position sizing methodology | enum | `fixed_notional` | APPROVED |
| RP-STOP-LOSS-REQUIRED | Hard stop mandatory | bool | `true` | APPROVED |
| RP-STOP-LOSS-MAX-DISTANCE | Max stop distance | % | `5` | APPROVED |
| RP-TAKE-PROFIT-POLICY | Take-profit rules | policy id | `optional` | APPROVED |
| RP-ORDER-TYPES-ALLOWED | Permitted order types | set | `LIMIT` | APPROVED |
| RP-TIME-IN-FORCE-ALLOWED | Permitted TIF | set | `GTC` | APPROVED |
| RP-MAX-SPREAD | Max allowed spread | bps | `20` | APPROVED |
| RP-MIN-LIQUIDITY | Min liquidity check | flag | `depth_ok` (require `liquidity_ok`) | APPROVED |
| RP-MAX-SLIPPAGE-MODEL | Max modeled slippage | bps | `10` | APPROVED |
| RP-MAX-FEE-ESTIMATE | Fee constraint | bps | `20` | APPROVED |
| RP-FUNDING-CONSTRAINT | Futures funding gate | flag | `ok_flag` (require `funding_ok`) | APPROVED |
| RP-DATA-FRESHNESS-MS | Max market-data age | ms | `5000` | APPROVED |
| RP-PROPOSAL-TTL-MS | Max proposal age | ms | `30000` | APPROVED |
| RP-COOLDOWN-AFTER-LOSS | Cooldown after loss | duration | `24h` | APPROVED |
| RP-STRATEGY-ALLOWLIST | Eligible strategies | set | `aegis-default`, `demo` (+ versioned) | APPROVED |
| RP-DUPLICATE-WINDOW | Duplicate detection window | duration | `60s` | APPROVED |
| RP-EMERGENCY-CANCEL | Auto-cancel open orders | bool | `false` | APPROVED (disabled) |
| RP-EMERGENCY-PROTECT | Auto reduce/close positions | bool | `false` | APPROVED (disabled) |

### Engineering defaults vs financial policy

`RP-MODE-DEFAULT`, `RP-LIVE-ARMED-DEFAULT`, and kill-switch behavior are safety engineering defaults from the PRD. They are not substitutes for the approved leverage, loss, or size limits above.

## 3. Mandatory rule checks (logical)

Every new-trade evaluation must run these checks. Numeric comparisons use only approved parameter values.

1. Kill switch off for new orders.
2. Trading mode permits the requested execution path (paper vs live).
3. Live path: arm flag true.
4. Instrument and market on allowlist.
5. Direction permitted.
6. Strategy version eligible.
7. Account/balance/margin available and reconciled.
8. Market data fresh and integrity OK.
9. Sizing within max notional / leverage / aggregate / open position / pending limits.
10. Daily loss and drawdown within limits (convention above).
11. Spread, liquidity, fee, funding, slippage constraints.
12. Stop/exit policy satisfied.
13. Exchange precision, min size, step size, min notional satisfied.
14. Proposal not expired; not a duplicate.
15. Order type and TIF permitted.
16. Reconciliation state OK.

Any failure ⇒ REJECT with rule id(s).

## 4. Decision output contract

```text
RiskDecision:
  decision: APPROVE | REJECT
  policy_version: string
  rules_evaluated: [rule_id]
  rejection_reasons: [rule_id, detail]
  validated_order_params: object | null
  account_state_refs: [...]
  market_state_refs: [...]
  decided_at: timestamp
  expires_at: timestamp
  correlation_id: string
  proposal_id: string
```

## 5. Separate emergency procedures

| Procedure | Purpose | v1.0 |
| --- | --- | --- |
| Stop-new-orders | Prevent additional risk | Active on kill switch, stale data, reconcile failure, daily loss |
| Cancel-open-orders | Remove resting orders | **Manual** — `RP-EMERGENCY-CANCEL=false` |
| Protect-or-close-positions | Reduce exposure | **Manual** — `RP-EMERGENCY-PROTECT=false` |

## 6. Exchange metadata (not Aegis policy)

Official Toobit `riskLimits` (e.g. exchange `maxLeverage`) are exchange constraints. Aegis enforces the **stricter** owner limit (`RP-MAX-LEVERAGE=1` in v1.0).

## 7. Policy versioning and approval

1. Parameters are listed here and mirrored in `src/aegis/risk/owner_v1.py`.
2. Owner review record: approver, timestamp, `policy_version` (this section header).
3. Runtime loader: `AEGIS_RISK_POLICY_VERSION` (default `1.0`). Set `0.1-draft` to force all-UNAPPROVED draft.
4. Live mode still requires explicit arming; draft versions cannot be used for live APPROVE.

## 8. Known constraints of v1.0

* 10 USDT capital with BTC/ETH min notionals may make many live symbols untradable; prefer paper soak and/or ADA/TRX first.
* Short selling not permitted until a later policy version.
* MARKET orders not permitted until a later policy version.
* Emergency auto-cancel/close remains off.
