# Aegis — Risk Policy

**Version:** 0.1-draft
**Policy status:** DRAFT — no numeric financial limits are approved
**Authority:** Project owner must approve a numbered policy version before live enforcement
**Related:** [PRD.md](PRD.md) §10, §21; [SRS.md](SRS.md); [ARCHITECTURE.md](ARCHITECTURE.md)

## 1. Principles

1. The deterministic Risk Engine is the only authority that may APPROVE a new trade.
2. LLMs, Jev, analysts, and strategies cannot modify this policy or override REJECT.
3. Missing, stale, inconsistent, or unknown critical state ⇒ REJECT new orders.
4. Stop-loss instructions are not a guarantee of fill price or fill occurrence.
5. Blocking new orders, canceling open orders, and protecting/closing positions are separate procedures.
6. Implementation must not invent “safe” leverage, loss, or size defaults and mark them approved.

## 2. Parameter register

Each parameter has status `UNAPPROVED` until the owner sets a value and signs a policy version.

| Parameter ID | Description | Unit | Value | Status |
| --- | --- | --- | --- | --- |
| RP-MODE-DEFAULT | Default trading mode | enum: `paper` \| `live` | `paper` | Proposed engineering default (not a financial limit) |
| RP-LIVE-ARMED-DEFAULT | Live arm flag default | bool | `false` | Proposed engineering default |
| RP-KILL-SWITCH-DEFAULT | Kill switch default (`true` = block new) | bool | `false` (normal) with ability to set `true` | Proposed engineering default |
| RP-INSTRUMENT-ALLOWLIST-SPOT | Allowed Spot symbols | set of symbol ids | — | UNAPPROVED |
| RP-INSTRUMENT-ALLOWLIST-FUTURES | Allowed Futures symbols | set of symbol ids | — | UNAPPROVED |
| RP-MARKETS-ALLOWED | Allowed market types | set: `spot`, `futures` | — | UNAPPROVED |
| RP-DIRECTIONS-ALLOWED | Allowed directions per market | map | — | UNAPPROVED |
| RP-MAX-LEVERAGE | Maximum Futures leverage | ratio | — | UNAPPROVED |
| RP-MAX-NOTIONAL-PER-ORDER | Max notional per new order | quote currency | — | UNAPPROVED |
| RP-MAX-NOTIONAL-PER-INSTRUMENT | Max notional per instrument | quote currency | — | UNAPPROVED |
| RP-MAX-AGGREGATE-NOTIONAL | Max aggregate notional | quote currency | — | UNAPPROVED |
| RP-MAX-OPEN-POSITIONS | Max open positions | count | — | UNAPPROVED |
| RP-MAX-PENDING-ORDERS | Max pending/open orders | count | — | UNAPPROVED |
| RP-DAILY-LOSS-LIMIT | Max daily loss (convention TBD) | quote currency or % | — | UNAPPROVED |
| RP-DRAWDOWN-LIMIT | Max drawdown from reference equity | % or currency | — | UNAPPROVED |
| RP-SIZING-METHOD | Position sizing methodology | enum / formula id | — | UNAPPROVED |
| RP-STOP-LOSS-REQUIRED | Whether hard stop is mandatory | bool | — | UNAPPROVED |
| RP-STOP-LOSS-MAX-DISTANCE | Max stop distance | % or ticks | — | UNAPPROVED |
| RP-TAKE-PROFIT-POLICY | Take-profit rules | policy id | — | UNAPPROVED |
| RP-ORDER-TYPES-ALLOWED | Permitted order types | set | — | UNAPPROVED |
| RP-TIME-IN-FORCE-ALLOWED | Permitted TIF values | set | — | UNAPPROVED |
| RP-MAX-SPREAD | Max allowed spread | bps or absolute | — | UNAPPROVED |
| RP-MIN-LIQUIDITY | Min depth / liquidity check | metric TBD | — | UNAPPROVED |
| RP-MAX-SLIPPAGE-MODEL | Max modeled slippage | bps | — | UNAPPROVED |
| RP-MAX-FEE-ESTIMATE | Fee constraint | bps or currency | — | UNAPPROVED |
| RP-FUNDING-CONSTRAINT | Futures funding gate | rule TBD | — | UNAPPROVED |
| RP-DATA-FRESHNESS-MS | Max market-data age for APPROVE | milliseconds | — | UNAPPROVED |
| RP-PROPOSAL-TTL-MS | Max proposal age | milliseconds | — | UNAPPROVED |
| RP-COOLDOWN-AFTER-LOSS | Cooldown after loss / error | duration | — | UNAPPROVED |
| RP-STRATEGY-ALLOWLIST | Eligible strategy ids/versions | set | — | UNAPPROVED |
| RP-DUPLICATE-WINDOW | Duplicate proposal detection window | duration | — | UNAPPROVED |
| RP-EMERGENCY-CANCEL | Auto-cancel open orders on emergency | bool / procedure | — | UNAPPROVED |
| RP-EMERGENCY-PROTECT | Auto reduce/close positions on emergency | bool / procedure | — | UNAPPROVED |

### Engineering defaults vs financial policy

`RP-MODE-DEFAULT`, `RP-LIVE-ARMED-DEFAULT`, and kill-switch behavior are safety engineering defaults from the PRD. They are not substitutes for approved leverage, loss, or size limits.

## 3. Mandatory rule checks (logical)

Every new-trade evaluation must run these checks. Numeric comparisons use only approved parameter values; if a required parameter is `UNAPPROVED`, production live mode must remain blocked.

1. Kill switch off for new orders.
2. Trading mode permits the requested execution path (paper vs live).
3. Live path: arm flag true.
4. Instrument and market on allowlist.
5. Direction permitted.
6. Strategy version eligible.
7. Account/balance/margin available and reconciled.
8. Market data fresh and integrity OK.
9. Sizing within max notional / leverage / aggregate / open position / pending limits.
10. Daily loss and drawdown within limits (accounting convention must be documented when values are approved).
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

| Procedure | Purpose | Default until approved |
| --- | --- | --- |
| Stop-new-orders | Prevent additional risk | Activate on kill switch, stale data, reconcile failure |
| Cancel-open-orders | Remove resting orders | **Unresolved** — manual owner action until `RP-EMERGENCY-CANCEL` approved |
| Protect-or-close-positions | Reduce exposure | **Unresolved** — manual owner action until `RP-EMERGENCY-PROTECT` approved |

## 6. Exchange metadata (not Aegis policy)

Official Toobit `riskLimits` responses may include fields such as `maxLeverage` (example values have appeared in public docs). Those values are exchange constraints. They must never be copied into this policy as Aegis-approved limits without an owner decision.

## 7. Policy versioning and approval

1. Draft parameters in this file or a machine-readable policy document.
2. Owner review records: approver, timestamp, policy_version.
3. Only approved versions may be loaded when `trading_mode=live`.
4. Paper mode may load a draft policy only for simulation, still without inventing unmarked defaults for live.

## 8. Open decisions (owner)

See PRD §21. Material items include instrument allowlists, leverage, exposure, daily loss, drawdown, sizing, stops, order types, strategy eligibility, paper evaluation criteria, and emergency cancel/close authority.
