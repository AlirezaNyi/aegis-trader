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
* Optional `PaperSoakRunner`: REST poll for new finalized bars (default off via
  `AEGIS_PAPER_SOAK_ENABLED`). Debounces so the same final bar does not re-call LLM.
* `AEGIS_PAPER_SOAK_SYMBOL` accepts a comma-separated list (max 10 unique symbols).
  Each symbol is polled separately. Risk policy v1.0 allowlist remains
  ADA/BTC/TRX/BNB/ETH USDT — other symbols may cycle but Risk will reject new trades.

## Paper RiskContext honesty

`build_paper_risk_context` uses `PaperEquityTracker` for `daily_loss`, `drawdown`,
and `cooldown_active`, and `ProposalHistory` for duplicates.

* Mark-to-market uses the cycle's latest close for the traded instrument; other
  open symbols fall back to entry price.
* `balance_reconciled=True` / `reconciliation_ok=True` are **paper-local**
  assumptions — not exchange inventory parity (audit M1).
* Do **not** reuse this builder for a live cycle.

## Credential adjacency

`PaperCycleDeps` holds `Settings` for mode/kill-switch and Risk/PaperBroker.
Analysts, Jev, and Supervisor must never receive `Settings` or secrets.
Soak uses public REST only (no trade keys).

## Owner review of suggestions

* CLI: `scripts/paper_soak.py --once` prints SUGGESTION / RISK / PAPER_ORDER / HINT.
* HTTP (when soak is running in-app): `GET /ops/paper/last-cycle` — last soak outcome only; paper simulation; no live submit.
* `NO_TRADE` + Risk `REJECT`/`RP-ACTION` is expected when the bot declines a trade.

## Deferred

* WS as primary finalize trigger (REST poll is the soak v1 trigger).
* Exchange inventory reconcile remains out of scope (audit M1).
* Intent mapping requires `entry_price` + `sizing.notional` on APPROVE.
* SE-* paper evaluation thresholds remain UNAPPROVED (soak is learning, not promotion).
