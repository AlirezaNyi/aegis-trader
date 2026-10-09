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

## Upstream pipeline

* Callers must supply an honest `RiskContext`; missing critical fields ⇒ REJECT.
* Tiny notional caps (2–10 USDT) may conflict with exchange min notional on BTC/ETH — expect REJECT via `RP-MIN-SIZE` / exchange filters when wired.

## Live remains disabled by default

* Live submit still requires `trading_mode=live`, `live_armed=true`, and `kill_switch=false`.
* Loading policy `1.0` does **not** arm live mode.
