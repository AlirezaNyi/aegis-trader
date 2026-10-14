# Risk Engine limitations

## Owner policy v1.0

* Default runtime loads owner-approved snapshot `1.0` via `AEGIS_RISK_POLICY_VERSION` (see `owner_v1.py` and `docs/RISK_POLICY.md`).
* Set `AEGIS_RISK_POLICY_VERSION=0.1-draft` to force all financial params UNAPPROVED (BUY/SELL always REJECT on those rules).
* Unknown version ids fail closed to the draft.

## Owner policy 1.1-env

* `AEGIS_RISK_POLICY_VERSION=1.1` (or `1.1-env`) loads `owner_v1_1.owner_env_policy_v1_1`: v1.0 plus owner env limits as % of `AEGIS_PAPER_INITIAL_BALANCE` (see `docs/RISK_POLICY.md` §9). Effective version `1.1-env+<sha256[:8]>`.
* The capital base is the **initial** balance, not current equity: limits do not shrink after losses or grow after gains. In live mode the base is still `AEGIS_PAPER_INITIAL_BALANCE` (the setting name is paper-specific); the owner must set it to the real capital before arming.
* **Live is blocked for 1.1-env:** `RP-ENV-POLICY-LIVE` rejects every new trade when `trading_mode=live` and the policy version starts with `1.1-env` (safety review H2). Lifting it needs owner decisions on env-as-live-approval and a live capital-base setting (`docs/RISK_POLICY.md` §9).
* `RP-MAX-AGGREGATE-NOTIONAL` under 1.1 is `min(position cap × max open positions, C)`: structurally no more than 100% of the capital base (no spot leverage). No other upper bounds on 1.1 percentages exist beyond settings validation (`<= 100`).
* Missing env ⇒ derived params UNAPPROVED ⇒ every new trade REJECTs. `missing_policy_env()` lists names; the parent must surface it at startup.
* `RP-WEEKLY-LOSS-LIMIT`, `RP-MONTHLY-LOSS-LIMIT`, `RP-MAX-RISK-PER-TRADE`, `RP-MIN-REWARD-RISK` are in `OPTIONAL_FINANCIAL_PARAM_IDS`: enforced only when present in the policy (absent in v1.0 and the draft), REJECT when present but UNAPPROVED.
* The engine does **not** compute `weekly_loss`, `monthly_loss`, or `period_lock_until`; the caller must supply them (None ⇒ REJECT for the loss rules; None ⇒ no lock). Period boundaries (ISO week, calendar month, UTC) are the caller's responsibility.
* Cost-aware rules use `RiskContext.fee_estimate` (bps per side), `spread` (bps, full spread), and `slippage_model_bps` (bps per side). Round-trip cost per unit = `entry × (2·fee + spread + 2·slippage) / 10000`. This is a model: actual fills, maker/taker mix, and stop slippage can differ. Negative inputs (e.g. maker rebates) are rejected rather than credited.
* `RP-MAX-RISK-PER-TRADE` bounds the planned loss at the stop price plus modeled costs; stop fills are not guaranteed (gaps, no stop order resting on the exchange) so realized loss can exceed it.
* `RP-PERIOD-LOCK` is structural (all policy versions) and only reflects what the caller sets.

## Emergency cancel / protect (ADR 0006)

* v1.0 marks `RP-EMERGENCY-CANCEL` / `RP-EMERGENCY-PROTECT` as APPROVED **false** (manual only).
* This engine only decides **new-trade** APPROVE/REJECT.
* Blocking new orders ≠ cancel open orders ≠ protect/close positions.

## Stop-loss is not a fill guarantee

* When stops are present or required, validated params / rejection details note that stop-loss instructions do not guarantee fill price or occurrence.

## Stop / take-profit side checks (structural)

* `RP-STOP-LOSS-SIDE` and `RP-TAKE-PROFIT-SIDE` reject levels on the wrong side of (or equal to) `entry_price` for the proposal direction. They run regardless of policy approval status.
* They compare against the proposal's declared `entry_price`, not a live mark. A market order whose fill moves past the stop/target is not caught here; no numeric buffer is applied (that would be an owner policy value).
* Missing or non-numeric `entry_price` (or direction not long/short) fails closed. `RP-STOP-LOSS-MAX-DISTANCE` also fails closed (REJECT, not exception) on a non-numeric `entry_price`.

## Entry vs order book (structural)

* `RP-ENTRY-VS-BOOK` ties the declared `entry_price` to the observed book for non-`MARKET` orders (missing `order_type` is treated as `LIMIT`): BUY requires `entry_price <= best_ask`, SELL requires `entry_price >= best_bid`. Runs regardless of policy approval status. No tolerance is applied.
* It is keyed on `action` (BUY/SELL = order side), not `direction`.
* `RiskContext.best_bid` / `best_ask` must be a fresh observed book top for the proposal instrument; the engine does not check book age separately (it relies on the caller and `RP-DATA-FRESHNESS-MS`). Missing book side ⇒ REJECT, so callers without a book (e.g. candle-only backtest replay) reject every limit proposal until they supply one.
* It does not prevent a passive limit far *below* the ask (BUY) from resting and later filling after a move; stop/TP side rules still compare against the declared entry, which then equals the actual limit price.
* `MARKET` orders skip this rule; their fill price vs declared entry remains unchecked (see side checks above).

## Upstream pipeline

* Callers must supply an honest `RiskContext`; missing critical fields ⇒ REJECT.
* Tiny notional caps (2–10 USDT) may conflict with exchange min notional on BTC/ETH — expect REJECT via `RP-MIN-SIZE` / exchange filters when wired.

## Live remains disabled by default

* Live submit still requires `trading_mode=live`, `live_armed=true`, and `kill_switch=false`.
* Loading policy `1.0` does **not** arm live mode.
