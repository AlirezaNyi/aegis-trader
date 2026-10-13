# Risk Engine limitations

## Owner policy v1.0

* Default runtime loads owner-approved snapshot `1.0` via `AEGIS_RISK_POLICY_VERSION` (see `owner_v1.py` and `docs/RISK_POLICY.md`).
* Set `AEGIS_RISK_POLICY_VERSION=0.1-draft` to force all financial params UNAPPROVED (BUY/SELL always REJECT on those rules).
* Unknown version ids fail closed to the draft.

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
