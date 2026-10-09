# Aegis — Paper evaluation report (system soak)

**Date:** 2026-10-09  
**Recommendation for owner:** **continue paper**  
**Live activation:** **not** recommended from this artifact. Live remains disarmed.

Template: [STRATEGY_EVALUATION.md](STRATEGY_EVALUATION.md) §6.  
Owner thresholds: [OWNER_PENDING_DECISIONS.md](OWNER_PENDING_DECISIONS.md) (SE-* still **UNAPPROVED**).

## 1. Strategy and parameter versions

| Field | Value |
| --- | --- |
| Strategy id | `aegis-default` |
| Strategy version | `0.1.0` (paper cycle default) |
| Risk policy | Owner v1.0 (`AEGIS_RISK_POLICY_VERSION` default) |
| Software | Aegis package via local `.venv`; paper soak CLI `scripts/paper_soak.py --once` |
| Trading mode | `paper` |
| Live armed | `false` |
| Kill switch | `false` |
| Execution port | PaperBroker / NullExecutionPort path (no signed Toobit submit) |

## 2. Data range and splits

| Field | Value |
| --- | --- |
| Instrument | Spot `ADAUSDT` (policy allowlist) |
| Timeframe | `1m` finalized bars |
| Trigger | Public Toobit REST klines → one cycle per new final open |
| Evidence window | Single soak poll at ~2026-10-09 15:38 UTC |
| Final bar observed | `2026-10-09 15:38:00+00:00` |
| Train / validation / OOS | **N/A** — system soak, not a promotion sample |

SE-PAPER-WINDOW and SE-MIN-TRADES remain UNAPPROVED; this run does **not** claim evaluation criteria met.

## 3. Cost and fill assumptions

| Assumption | Value | Notes |
| --- | --- | --- |
| Paper fee | default engineering `paper_fee_bps` | Not risk-policy |
| Paper slippage | default engineering `paper_slippage_bps` | Not risk-policy |
| Live fills | Not exercised | No live client constructed |
| Funding / latency model | Not applied | See backtest LIMITATIONS |

## 4. Metrics table

| Metric | Result |
| --- | --- |
| Cycles with new final bar | ≥1 (`ran_cycle=True`) |
| Validation ok | `True` |
| Proposal action | `NO_TRADE` (fail-closed Jev/LLM path with empty budgets) |
| Risk decision | `REJECT` |
| Rejection reasons | `RP-ACTION` — `NO_TRADE` / `direction=flat` not approve candidates |
| Paper order created | No |
| Correlation id (sample) | `paper-cycle-909fd071bb97` |
| Net return / drawdown / Sharpe | **N/A** — no APPROVE fills in this soak sample |
| Trade count | 0 |

Empty LLM/Jev budgets ⇒ Supervisor fail-closed to NO_TRADE is expected and is valid pipeline evidence.

## 5. Known limitations

* SE-* thresholds UNAPPROVED — soak is learning only ([OWNER_PENDING_DECISIONS.md](OWNER_PENDING_DECISIONS.md)).
* Account inventory reconcile still Unverified (audit M1; [API_CONTRACTS.md](API_CONTRACTS.md)).
* Alert delivery and backup restore E2E not proven on a separate host (audit M4; [READINESS_REPORT.md](READINESS_REPORT.md)).
* Emergency auto cancel/protect UNAPPROVED (ADR 0006).
* Compose was not running at soak time; soak used local CLI against public market data.

## 6. Performance disclaimer

Results do **not** guarantee future performance. Passing pipeline soak and unit tests does not establish profitability or live safety.

## 7. Owner recommendation

**Continue paper.** Next owner steps before any live checklist:

1. Approve SE-* window and minimum trades if promotion review is desired.  
2. Set LLM/Jev budgets only if real model calls are wanted during soak.  
3. Wire notification channel and prove backup restore off-box.  
4. Re-verify inventory REST paths before live inventory reconcile.  
5. Do **not** set `AEGIS_TRADING_MODE=live` or `AEGIS_LIVE_ARMED=true` from this report.
