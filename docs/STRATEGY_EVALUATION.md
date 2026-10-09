# Aegis — Strategy Evaluation

**Version:** 1.0
**Status:** Proposed (Phase 0)
**Related:** [PRD.md](PRD.md) §17, [RISK_POLICY.md](RISK_POLICY.md), [DATA_MODEL.md](DATA_MODEL.md)

## 1. Purpose

Define how Aegis evaluates strategies without claiming profitability from tests alone. Backtesting and paper trading are evidentiary tools for owner review, not automatic live activation.

## 2. Principles

1. Passing unit or integration tests does not establish profitability.
2. Avoid look-ahead bias and leakage from future candles or labels.
3. Separate training, validation, and out-of-sample (OOS) periods.
4. Model transaction costs: fees, spread, slippage, latency, and Futures funding where applicable.
5. Track strategy versions, parameter versions, dataset versions, and software versions.
6. Report uncertainty and sample-size limitations.
7. Paper and live performance must never be mixed in the same ledger metrics.

## 3. Backtest protocol (**Proposed**)

### 3.1 Data

* Use only finalized candles for strategies that require bar close.
* Document timezone and exchange event-time handling.
* Record symbol universe; avoid survivorship assumptions unless explicitly stated.
* Missing bars: document fill policy (skip, gap, or halt strategy).

### 3.2 Splits

| Split | Use |
| --- | --- |
| Training | Hypothesis development / parameter search |
| Validation | Limited selection among candidates |
| Out-of-sample | Final report metrics for owner review |

Walk-forward evaluation is preferred for scalping/intraday regimes.

### 3.3 Cost model parameters

All cost assumptions are configuration with documented defaults for simulation only. They are not live risk policy.

| Assumption | Status |
| --- | --- |
| Fee schedule | Must be documented per run; source **Unresolved** until owner sets |
| Spread model | Documented per run |
| Slippage model | Documented per run |
| Latency model | Documented per run |
| Partial fill model | Documented per run |
| Funding rate application | Use historical funding when available from verified endpoints |

### 3.4 Metrics to report

* Net return after modeled costs
* Max drawdown
* Sharpe and Sortino (methodology documented)
* Profit factor
* Win rate; average win/loss
* Trade count and sample size
* Exposure and turnover
* Fee and slippage contribution
* Performance by instrument, timeframe, regime
* Uncertainty / confidence intervals where appropriate

### 3.5 Overfitting controls

* Limit repeated optimization on the same OOS set.
* Strategy Researcher must record experiment metadata.
* Flag small samples and unstable parameters in reports.

## 4. Paper trading evaluation

Paper trading uses the same analytical and risk path as intended for live, with simulated execution.

### Owner-approved thresholds (**UNAPPROVED**)

| Parameter | Description | Value | Status |
| --- | --- | --- | --- |
| SE-PAPER-WINDOW | Predeclared evaluation calendar window | — | UNAPPROVED |
| SE-MIN-TRADES | Minimum trade count before live review | — | UNAPPROVED |
| SE-MIN-INSTRUMENTS | Optional instrument coverage | — | UNAPPROVED |
| SE-MAX-DRAWDOWN-PAPER | Optional paper drawdown review trigger | — | UNAPPROVED |

Until these are approved, paper trading may run for learning and system soak tests, but live activation review cannot claim “evaluation criteria met.”

Pending-owner register for this stage: [OWNER_PENDING_DECISIONS.md](OWNER_PENDING_DECISIONS.md).

## 5. Promotion rules (**UNAPPROVED**)

Strategy eligibility and promotion from research → paper → live-candidate require owner criteria (PRD §21). No automatic promotion.

## 6. Reporting template

Each evaluation artifact should include:

1. Strategy and parameter versions
2. Data range and splits
3. Cost and fill assumptions
4. Metrics table
5. Known limitations
6. Explicit statement: results do not guarantee future performance
7. Recommendation for owner: continue paper / revise / consider live review checklist

## 7. Acceptance for Phase 6

Phase 6 acceptance requires deterministic replay tests, documented assumptions, separate OOS reporting, paper ledger reconciliation, and live execution still disabled — not proof of edge.
