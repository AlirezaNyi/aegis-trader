# Pipeline limitations

## Implemented

* Explicit `run_paper_cycle` orchestrator (ADR 0002): validation → features →
  analysts → evidence → Jev → Supervisor → Risk → PaperBroker.
* Paper and development modes only; `trading_mode=live` and `kill_switch=true`
  are refused without calling Order Manager or any signed exchange client.
* Validation failures short-circuit before analyst/LLM spend and REJECT with
  freshness/integrity reasons.
* Unavailable Jev/LLM and empty budgets fail closed to `NO_TRADE` / Risk REJECT.
* Post-APPROVE paper fill failures leave `order=None` and set `blocked_reason`.

## Paper RiskContext honesty (do not treat as live proof)

`build_paper_risk_context` is **paper-only**. It currently sets:

* `daily_loss=0`, `drawdown=0`, `cooldown_active=False`, `duplicate_detected=False`
  — owner loss/cooldown/duplicate windows are not derived from ledger history yet.
* `balance_reconciled=True`, `reconciliation_ok=True`, and related OK flags —
  local paper simulation assumptions, not exchange inventory parity.

Do **not** reuse this builder for a live cycle. Paper soak does not prove that
loss/drawdown/cooldown/duplicate rules bind end-to-end.

## Credential adjacency

`PaperCycleDeps` holds `Settings` for mode/kill-switch and Risk/PaperBroker
construction. Analysts, Jev, and Supervisor must never receive `Settings` or
secrets. Prefer narrowing to a credential-free guards view before any live path.

## Deferred

* No continuous in-process candle subscription loop yet — callers invoke
  `run_paper_cycle` on finalized bars (tests, ops scripts, future WS hook).
* Exchange inventory reconcile remains out of scope (audit M1).
* Intent mapping requires `entry_price` + `sizing.notional` on APPROVE.
