# Risk Engine limitations (Phase 5)

## Draft policy cannot APPROVE

* Default policy version `0.1-draft` keeps every financial parameter in `docs/RISK_POLICY.md` §2 as `UNAPPROVED` with `value=None`.
* BUY/SELL evaluations against the draft always **REJECT**, citing UNAPPROVED required params (and any other failures).
* APPROVE is only possible when a `RiskPolicy` is explicitly constructed with APPROVED params (tests or a future owner-loaded version).

## Owner must approve a policy version

* Numeric leverage, notional, loss, drawdown, freshness, allowlists, and related limits are **not** invented as production defaults.
* `build_risk_policy_from_settings` always returns `default_draft_policy()` — there is no owner-approved loader yet.
* Live mode: draft policy versions are rejected via `RP-POLICY-VERSION`; any required UNAPPROVED param also blocks APPROVE.
* `RiskPolicy` snapshots are immutable (`MappingProxyType`); use `with_approved_params` to build a new snapshot.

## Emergency cancel / protect unresolved (ADR 0006)

* This engine only decides **new-trade** APPROVE/REJECT.
* Blocking new orders ≠ cancel open orders ≠ protect/close positions.
* `RP-EMERGENCY-CANCEL` and `RP-EMERGENCY-PROTECT` remain UNAPPROVED; no automatic cancel/close.

## Stop-loss is not a fill guarantee

* When stops are present or required, validated params / rejection details note that stop-loss instructions do not guarantee fill price or occurrence.

## Upstream pipeline still stubbed

* Validation, account/ledger feeds, and Order Manager / paper / exchange submit are outside Phase 5.
* Callers must supply an honest `RiskContext`; missing critical fields ⇒ REJECT.

## Live remains disabled

* Live submit still requires `trading_mode=live`, `live_armed=true`, and `kill_switch=false` (see `aegis.guards.live`).
* Phase 5 does not arm live mode or weaken settings startup validation.
