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

### Structural rules (non-numeric)

These rules carry no owner-set numeric value. They validate proposal geometry and are enforced on every new-trade candidate regardless of policy version or parameter approval status (including the draft policy). They are recorded in `rules_evaluated` when the corresponding level is present (`RP-ENTRY-VS-BOOK`: whenever the order type is not `MARKET`).

| ID | Check | Failure modes |
| --- | --- | --- |
| RP-STOP-LOSS-SIDE | When `stop_loss` is present: long ⇒ `stop_loss < entry_price`; short ⇒ `stop_loss > entry_price` | Wrong side; equal to entry; `entry_price` missing or non-numeric; direction not long/short |
| RP-TAKE-PROFIT-SIDE | When `take_profit` is present: long ⇒ `take_profit > entry_price`; short ⇒ `take_profit < entry_price` | Wrong side; equal to entry; `entry_price` missing or non-numeric; direction not long/short |
| RP-ENTRY-VS-BOOK | When `order_type` is not `MARKET` (missing ⇒ treated as `LIMIT`, matching intent building): BUY ⇒ `entry_price <= RiskContext.best_ask`; SELL ⇒ `entry_price >= RiskContext.best_bid`. No tolerance (a tolerance would be an owner-set value). Prevents a fictitious entry from satisfying the side rules: a limit that crosses the book fills near the touch, not at the declared price. | Entry crosses the observed book; required book side (`best_ask` for BUY, `best_bid` for SELL) missing or non-positive ("book reference missing"); `entry_price` missing, non-numeric, or non-positive |

`entry_price` is read from `entry_conditions.entry_price` / `entry_conditions.price` / `sizing.entry_price` / `sizing.price` (same lookup as `RP-STOP-LOSS-MAX-DISTANCE`). Whether a stop/target is *required* remains governed by `RP-STOP-LOSS-REQUIRED` / `RP-TAKE-PROFIT-POLICY`.

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
3. Runtime loader: `AEGIS_RISK_POLICY_VERSION` (default `1.0`). Set `0.1-draft` to force all-UNAPPROVED draft. Set `1.1` for owner limits from env (§9). Unknown versions fail closed to the draft.
4. Live mode still requires explicit arming; draft versions cannot be used for live APPROVE.

## 8. Known constraints of v1.0

* 10 USDT capital with BTC/ETH min notionals may make many live symbols untradable; prefer paper soak and/or ADA/TRX first.
* Short selling not permitted until a later policy version.
* MARKET orders not permitted until a later policy version.
* Emergency auto-cancel/close remains off.

## 9. Policy 1.1-env (owner limits from environment)

Selected with `AEGIS_RISK_POLICY_VERSION=1.1` (alias `1.1-env`). Loader: `src/aegis/risk/owner_v1_1.py` via `build_risk_policy_from_settings`.

### Approval model

* **Setting a value in the environment is the owner's approval of that value.** Engineering supplies no defaults and this document records no numbers for 1.1.
* Limits are a **percent of the initial capital base** `C = AEGIS_PAPER_INITIAL_BALANCE` (USDT). The engine compares derived USDT values.
* **Fail closed:** when a required value (or `C`) is empty, every parameter derived from it is loaded as `UNAPPROVED` (value `null`). An `UNAPPROVED` parameter rejects every new trade. `missing_policy_env(settings)` returns the missing env var names (names only, never values) for startup logs / dashboards; it returns `[]` when 1.1 is not selected.
* Settings validation (`config/settings.py`) rejects out-of-range values at startup: percentages must be `0 < x <= 100`, daily `<=` weekly `<=` monthly, per-trade `<=` position, min reward:risk `> 0`, fee `>= 0`.
* **Recorded version:** `1.1-env+<h>`, where `<h>` is the first 8 hex chars of SHA-256 over the canonical, sorted parameter set (id, status, unit, normalized value). Every `RiskDecision.policy_version` therefore identifies exactly which limits were in force. `10` and `10.0` hash the same; any value/status/unit change produces a new hash. The version never contains `draft`.
* Live still requires `AEGIS_TRADING_MODE=live`, `AEGIS_LIVE_ARMED=true`, and `AEGIS_KILL_SWITCH=false`. Selecting 1.1 does not arm live.
* **Paper-only (rule `RP-ENV-POLICY-LIVE`):** when `trading_mode=live` and the active `policy_version` starts with `1.1-env`, the Risk Engine REJECTs every new trade with `RP-ENV-POLICY-LIVE` ("env-derived policy 1.1 is paper-only until owner records live approval and a live capital base"), even if every other rule would approve. Paper and development modes are unaffected. Structural rule, no numbers. Conservative default pending the owner decisions below (safety review H2).

### Pending owner decisions (1.1)

* **Env-as-approval for live:** whether an env value may count as owner approval for live trading, or whether live requires a separately recorded (versioned) owner approval. Until decided, `RP-ENV-POLICY-LIVE` blocks live.
* **Live capital base:** `C` is `AEGIS_PAPER_INITIAL_BALANCE`, a paper-named setting. Live needs an explicit live capital-base setting (and a rule for how it relates to reconciled exchange equity). Not implemented.
* **Upper bounds:** settings validation only enforces `0 < pct <= 100` and period ordering. Owner-set upper bounds on 1.1 percentages (per-position, per-trade, loss limits, drawdown) are not defined; engineering will not invent them. The aggregate cap is bounded structurally at `C` only.

### Parameters derived from env

| Parameter ID | Value | Unit | Env input(s) |
| --- | --- | --- | --- |
| RP-MAX-NOTIONAL-PER-ORDER | `position_pct × C / 100` | USDT | `AEGIS_RISK_POSITION_PCT`, `C` |
| RP-MAX-NOTIONAL-PER-INSTRUMENT | same as per-order (no pyramiding) | USDT | same |
| RP-MAX-AGGREGATE-NOTIONAL | `min(per-order cap × v1.0 RP-MAX-OPEN-POSITIONS, C)` — structurally bounded at 100% of `C` (no leverage on spot); not a tunable number | USDT | same |
| RP-DAILY-LOSS-LIMIT | `daily_pct × C / 100` | USDT | `AEGIS_RISK_DAILY_LOSS_PCT`, `C` |
| RP-WEEKLY-LOSS-LIMIT *(new)* | `weekly_pct × C / 100` | USDT | `AEGIS_RISK_WEEKLY_LOSS_PCT`, `C` |
| RP-MONTHLY-LOSS-LIMIT *(new)* | `monthly_pct × C / 100` | USDT | `AEGIS_RISK_MONTHLY_LOSS_PCT`, `C` |
| RP-DRAWDOWN-LIMIT | `drawdown_pct × C / 100` | USDT | `AEGIS_RISK_MAX_DRAWDOWN_PCT`, `C` |
| RP-MAX-RISK-PER-TRADE *(new, optional)* | `per_trade_pct × C / 100`; absent when `AEGIS_RISK_PER_TRADE_PCT` is empty | USDT | `AEGIS_RISK_PER_TRADE_PCT`, `C` |
| RP-MIN-REWARD-RISK *(new)* | owner ratio | ratio | `AEGIS_RISK_MIN_REWARD_RISK` |
| RP-TAKE-PROFIT-POLICY | `required` | policy id | — |
| RP-SIZING-METHOD | `risk_pct_capital` if per-trade set, else `pct_capital` | enum | `AEGIS_RISK_PER_TRADE_PCT` |

All other parameters (allowlists, markets, directions, leverage, open/pending counts, stop required + max distance, order types, TIF, spread/liquidity/slippage/fee caps, funding, freshness, TTL, cooldown, strategy allowlist, duplicate window, emergency flags) are inherited **unchanged** from v1.0 (§2).

Required env for 1.1: `AEGIS_PAPER_INITIAL_BALANCE`, `AEGIS_RISK_POSITION_PCT`, `AEGIS_RISK_DAILY_LOSS_PCT`, `AEGIS_RISK_WEEKLY_LOSS_PCT`, `AEGIS_RISK_MONTHLY_LOSS_PCT`, `AEGIS_RISK_MAX_DRAWDOWN_PCT`, `AEGIS_RISK_MIN_REWARD_RISK`. Optional: `AEGIS_RISK_PER_TRADE_PCT`. `AEGIS_TRADING_FEE_BPS` is not a policy value; it feeds the caller's `RiskContext.fee_estimate`.

### New rules (enforced only when the parameter is present in the active policy)

| Rule ID | Check | Failure modes |
| --- | --- | --- |
| RP-WEEKLY-LOSS-LIMIT | `RiskContext.weekly_loss <= limit` (loss since ISO week start, Monday 00:00 UTC) | Loss `>` limit; `weekly_loss` missing / non-numeric; parameter UNAPPROVED |
| RP-MONTHLY-LOSS-LIMIT | `RiskContext.monthly_loss <= limit` (loss since calendar-month start, UTC) | Loss `>` limit; `monthly_loss` missing / non-numeric; parameter UNAPPROVED |
| RP-MIN-REWARD-RISK | `R_net >= min`, with `Cu = entry × (2·fee_bps + spread_bps + 2·slippage_bps) / 10000` and `R_net = (\|TP − entry\| − Cu) / (\|entry − SL\| + Cu)`. `R_net` (truncated to 4 dp) is recorded in the rejection detail and, on APPROVE, in `validated_order_params.reward_risk_net` | `R_net < min`; entry / SL / TP missing or non-numeric; any of `fee_estimate`, `spread`, `slippage_model_bps` missing, non-numeric, or negative; zero risk per unit; parameter UNAPPROVED |
| RP-MAX-RISK-PER-TRADE | Implied loss if the stop fills at its price, incl. costs: `proposed_notional / entry × (\|entry − SL\| + Cu) <= budget` | Loss `>` budget; `proposed_notional` / entry / SL / cost inputs missing; parameter UNAPPROVED. Stop fill is not guaranteed, so this bounds the *planned* loss only |
| RP-TAKE-PROFIT-POLICY | Value `required` (or boolean `true`) ⇒ `take_profit` must be present | Missing `take_profit` |

### Structural rule (all policy versions)

| Rule ID | Check | Failure modes |
| --- | --- | --- |
| RP-PERIOD-LOCK | When `RiskContext.period_lock_until` is set (by the caller after a daily/weekly/monthly loss-limit breach), reject while `now < period_lock_until` | Lock in force; naive vs aware datetime mismatch (fail closed). `None` ⇒ no lock |

### Sizing (code-owned, never the model)

* `pct_capital`: `notional = RP-MAX-NOTIONAL-PER-ORDER`, rounded **down** to 0.01 USDT.
* `risk_pct_capital`: `notional = min(position cap, risk_budget × entry / (|entry − SL| + Cu))`, rounded **down** to 0.01 USDT, where `Cu` uses the caller's modeled round-trip cost in bps. Missing entry / SL / cap / budget / cost ⇒ no notional ⇒ REJECT.
* The Risk Engine independently re-checks the sized notional against every cap and against `RP-MAX-RISK-PER-TRADE` using the costs in `RiskContext`.
