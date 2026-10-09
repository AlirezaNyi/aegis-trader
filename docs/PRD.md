# Aegis — Product Requirements Document (PRD)

**Version:** 1.1
**Status:** Approved product baseline (Phase 0 documentation)
**Project type:** Independent personal software project
**Project / package identity:** Aegis / `aegis`
**Primary exchange:** Toobit
**Default execution mode:** Paper Trading
**Live trading:** Disabled until separately approved

Related documents: [SRS.md](SRS.md), [RFC.md](RFC.md), [ARCHITECTURE.md](ARCHITECTURE.md), [RISK_POLICY.md](RISK_POLICY.md), [DECISIONS/](DECISIONS/).

## 1. Product vision

Aegis is a personal autonomous trading system designed to research, evaluate, and execute trading strategies on Toobit Spot and Futures markets.

The system uses specialized analytical agents, an independent Jev AI evaluation, and an LLM Supervisor to generate and assess trading proposals. A deterministic Risk Engine validates every proposed trade before the execution layer can act.

Aegis is designed for 1-minute and 5-minute scalping signals and 5-minute and 15-minute intraday analysis. The architecture must prioritize risk containment, traceability, operational reliability, and empirical evaluation over trading frequency.

Aegis must not assume that AI-generated signals are profitable. Performance must be measured through reproducible backtesting and paper trading.

## 2. Goals

* Monitor supported Toobit Spot and Futures instruments.
* Generate candidate trade ideas using specialized analytical agents.
* Evaluate evidence independently through Jev AI when its official API is verified and available.
* Resolve conflicting analytical evidence through an LLM Supervisor.
* Enforce hard trading limits using a deterministic Risk Engine.
* Execute approved orders through an isolated Toobit adapter (Spot and Futures ports).
* Track orders, fills, balances, and positions against exchange state.
* Support historical backtesting, paper trading, audit trails, and monitoring.
* Run initially on a modest server without a dedicated GPU.
* Support explicit, reviewed transition from paper trading to live trading.

## 3. Non-goals

* Guaranteeing returns, win rates, or capital preservation.
* Allowing an LLM to execute orders or change risk policy.
* Unrestricted autonomous strategy changes in production.
* Enabling withdrawals through exchange API credentials.
* Assuming undocumented Jev or Toobit API capabilities.
* Running an LLM on every market tick.
* Starting with a distributed microservice architecture without a demonstrated need.
* Enabling live trading automatically after a successful deployment or test run.

## 4. Target operating profile

* Exchange: Toobit.
* Markets: Spot and Futures.
* Direction: Long and Short where the selected market supports the action.
* Timeframes: 1m, 5m, and 15m.
* Trading styles: Scalping and intraday.
* Initial deployment: approximately 4 vCPU, 8 GB RAM, 80 GB SSD, no dedicated GPU.
* Execution lifecycle: Paper Trading by default; live mode requires a separate activation decision.
* Project identity: Aegis, independent and personal.
* Python package and project-owned service identifiers: `aegis`.

Instrument selection, supported order types, position modes, leverage limits, and margin semantics must be confirmed against current official exchange documentation. Numeric risk limits are owned in [RISK_POLICY.md](RISK_POLICY.md) and remain unapproved until the owner sets them.

## 5. Users and authority

The primary user is the project owner.

The owner controls:

* Strategy eligibility.
* Risk policy and policy version approval.
* Allowed instruments and markets.
* Deployment and credential provisioning.
* Paper-to-live readiness approval.
* Live-mode activation and emergency shutdown policy.

The owner must not be required to approve each individual trade after the live system has been explicitly enabled. However, enabling live trading, modifying hard risk limits, changing execution-critical settings, and deploying unreviewed strategies are separate controlled actions.

## 6. System architecture

Logical components (modular monolith; one process unless isolation requirements justify otherwise):

1. Market Data Gateway.
2. Data Validation and Normalization.
3. Feature Engineering Engine.
4. Technical Analyst.
5. Quantitative Analyst.
6. News and Sentiment Analyst.
7. Analytical Risk Analyst.
8. Strategy Researcher.
9. Evidence Builder.
10. Jev AI Adapter.
11. LLM Supervisor.
12. Deterministic Risk Engine.
13. Order Manager.
14. Toobit Adapter (Spot port and Futures port in one module).
15. Position and Order Reconciliation.
16. Backtesting Engine.
17. Paper Trading Engine.
18. Configuration and Secret Management.
19. Audit Logging, Metrics, Alerting, and Operations.

Required decision pipeline:

Market Data → Validation → Feature Engine → Specialized Analysts → Evidence Builder → Jev Evaluator → LLM Supervisor → Deterministic Risk Engine → Order Manager → Toobit Adapter → Reconciliation.

The Risk Engine is the only authority for approving or rejecting a new trade. LLMs and Jev must never receive exchange credentials, submit orders, or override hard risk limits.

## 7. Decision workflow

1. Receive market data from verified Toobit endpoints and streams.
2. Validate timestamps, symbol metadata, sequence/order, completeness, and freshness.
3. Calculate deterministic features.
4. Trigger relevant analytical agents according to configured schedules and events.
5. Build a versioned evidence package.
6. Submit the package to Jev when configured and available.
7. Ask the LLM Supervisor to assess the evidence and produce a structured proposal.
8. Validate the proposal schema and its references.
9. Submit the proposal to the deterministic Risk Engine.
10. Reject it if any mandatory policy check fails.
11. In paper mode, simulate the approved order.
12. In explicitly enabled live mode, submit it through Order Manager and the Toobit adapter.
13. Reconcile orders, fills, positions, and balances against exchange state.
14. Persist an auditable record of the proposal, risk decision, execution events, and outcomes.

## 8. Analytical agents

### Technical Analyst

Analyzes trend, momentum, volatility, volume, and documented technical features.

### Quantitative Analyst

Analyzes statistical behavior, return distributions, correlations, and strategy metrics. It must account for sample size and statistical uncertainty.

### News and Sentiment Analyst

Evaluates configured, timestamped sources. Missing or stale sources must produce an explicit unavailable state, not invented information.

### Analytical Risk Analyst

Identifies volatility, liquidity, spread, funding, execution, and market-structure risks. It cannot replace or override the deterministic Risk Engine.

### Strategy Researcher

Proposes hypotheses, experiment plans, and parameter evaluations. It must track strategy versions and guard against data leakage and overfitting.

### Jev AI Evaluator

Provides an independent assessment through a verified integration. Its output is evidence, not authority.

### LLM Supervisor

Reviews analyst outputs, disagreement, missing evidence, and Jev results. It returns a schema-validated proposal or NO_TRADE. It cannot access exchange credentials or execute orders.

## 9. Trade proposal contract

A proposal must include, where applicable:

* Unique proposal ID and correlation ID.
* Instrument, market type, direction, and timeframe.
* Strategy identifier and version.
* Proposed action: BUY, SELL, HOLD, or NO_TRADE.
* Entry conditions and expiry.
* Proposed stop-loss and take-profit.
* Proposed sizing and leverage, if applicable.
* Evidence references and timestamps.
* Analyst and Jev results, including unavailable states.
* Explicit uncertainty and invalidation conditions.
* Supervisor model/provider metadata.
* Proposal creation time and expiration time.

A model-reported confidence value must not be represented as a calibrated probability of profit unless calibration has been empirically validated.

Internal proposal actions are not one-to-one with exchange side enums. Mapping is defined in [API_CONTRACTS.md](API_CONTRACTS.md).

## 10. Deterministic Risk Engine

The Risk Engine is the only authority that approves or rejects a proposed new trade.

Required controls include:

* Trading mode and kill switch.
* Market and instrument allowlists.
* Account, balance, and margin availability.
* Market-data freshness and integrity.
* Position sizing and maximum notional exposure.
* Aggregate exposure and maximum open positions.
* Futures leverage and margin constraints.
* Daily loss and drawdown controls.
* Pending-order limits and duplicate-proposal detection.
* Spread, liquidity, fees, funding, and slippage constraints.
* Stop-loss and exit-protection policy.
* Exchange precision, minimum size, and quantity increments.
* Strategy and configuration version validity.
* Account and exchange-state reconciliation.

Numeric limits must be explicit, configurable, versioned, tested, and approved by the owner. The implementation must not silently invent a financially safe leverage or loss limit. See [RISK_POLICY.md](RISK_POLICY.md).

If critical data or account state is missing, stale, inconsistent, or unknown, the engine must reject new orders.

The system must separately define:

* Blocking new orders.
* Canceling open orders.
* Protecting, reducing, or closing open positions.

A stop-loss is not a guarantee of execution at its requested price.

## 11. Execution and reconciliation

Order Manager must:

* Accept only validated risk decisions.
* Validate proposal expiry and idempotency.
* Use verified exchange order identifiers and capabilities.
* Track order lifecycle and partial fills.
* Reconcile uncertain submission outcomes before retrying.
* Avoid blind retries after timeouts.
* Reconcile actual positions, fills, and balances after restarts.
* Block new orders when critical reconciliation is incomplete.
* Record every execution-related state transition.

Exchange state is authoritative for actual fills, positions, and balances. Local state must be reconciled rather than blindly trusted.

## 12. Modes

### Development

Uses mocks, fixtures, and local services. No live order submission.

### Backtesting

Replays historical data with documented assumptions for costs, latency, and fills.

### Paper Trading

Uses the live analytical and risk pipeline but simulates execution and maintains separate simulated account state.

### Live Trading

Disabled by default. Requires an explicit configuration, credential setup, owner approval, and a completed readiness checklist. No test or deployment command may silently enable it.

## 13. External integrations

### Toobit

Use current official documentation to verify authentication, REST and WebSocket capabilities, instrument metadata, Spot/Futures differences, order states, precision, rate limits, and supported test environments. Verified facts and gaps are recorded in [API_CONTRACTS.md](API_CONTRACTS.md).

### Jev AI

Use official TypeSafe documentation to verify authentication, endpoints, model capabilities, schemas, rate limits, and error handling. If the API cannot be verified for a required capability, implement only an adapter interface and mock.

### LLM providers

Use a provider abstraction with typed outputs, timeouts, bounded retries, configurable cost budgets, and observability. Do not assume a particular provider is available until configured.

## 14. Proposed engineering stack

* Python (`aegis` package).
* FastAPI.
* Pydantic.
* PostgreSQL.
* Explicit in-process orchestration (LangGraph deferred; see [RFC.md](RFC.md)).
* Redis deferred until justified (see [RFC.md](RFC.md)).
* pytest.
* Ruff and mypy.
* Docker Compose.
* Prometheus and Grafana where operationally justified.

The initial architecture is a modular monolith unless evidence supports a different design.

## 15. Security requirements

* No exchange withdrawal permission.
* Least-privilege credentials and IP restrictions where supported.
* Secrets injected through configuration or a secret manager, never committed.
* Exchange credentials isolated from analytical agents and LLM calls.
* No secrets, signatures, or sensitive account details in logs or prompts.
* Strict validation of all model and external API outputs.
* External news and retrieved content treated as untrusted input.
* Auditable changes to risk policy and live-mode configuration.
* Dependency and container security checks before deployment.

## 16. Reliability and observability

Track:

* Market-data freshness, gaps, reconnects, and event lag.
* Agent availability, errors, latency, and output validation failures.
* Jev and LLM latency, usage, and estimated cost.
* Proposal counts and NO_TRADE reasons.
* Risk approvals and rejections by policy rule.
* Order submission, acknowledgment, fill, and reconciliation latency.
* Position discrepancies and unresolved order states.
* CPU, memory, database, and disk usage.

Required failure handling includes bounded retries, graceful shutdown, recovery after restart, database backup and restore, and alerts for conditions requiring intervention.

## 17. Backtesting and evaluation

Backtests must:

* Avoid look-ahead bias.
* Separate training, validation, and out-of-sample data.
* Include modeled fees, spread, slippage, and applicable Futures funding.
* Document fill, latency, and market-data assumptions.
* Track strategy and parameter versions.
* Report net performance, drawdown, profit factor, trade count, exposure, turnover, and performance across market regimes.
* Report uncertainty and sample-size limitations.

Paper-trading performance must be measured over a predeclared evaluation window and a meaningful number of trades before live activation is considered. Those thresholds are owner decisions; see [STRATEGY_EVALUATION.md](STRATEGY_EVALUATION.md).

Passing tests does not establish profitability.

## 18. Non-functional requirements

* Deterministic validation and risk decisions for identical inputs and policy versions.
* Explicit timeouts and bounded concurrency.
* Recoverability after process and host restarts.
* Traceability from market evidence to proposal, risk decision, order, fill, and final outcome.
* Modular adapters for external providers.
* Typed contracts and automated tests.
* Resource usage appropriate for the initial server.
* No dependency on a dedicated GPU.
* No unbounded LLM invocation on market ticks.

## 19. Delivery phases

0. Requirements and architecture (this documentation set).
1. Repository foundation.
2. Market data.
3. Feature engine and analysts.
4. Jev and Supervisor.
5. Risk Engine.
6. Backtesting and paper trading.
7. Toobit execution.
8. Deployment and production readiness.
9. Independent system audit and separately approved live activation.

Each phase must have explicit acceptance criteria and a review ([Phases/Phase Acceptance Review.md](../Phases/Phase%20Acceptance%20Review.md)). The next phase must not start automatically if the current phase has blocking findings.

## 20. Live activation gate

Live activation requires explicit owner review of:

* Verified exchange and Jev integration behavior.
* Risk policy and hard limits.
* Backtest assumptions and out-of-sample results.
* Paper-trading evidence.
* Order idempotency and reconciliation tests.
* Incident and emergency procedures.
* Secret management and exchange permissions.
* Monitoring, alerting, backup, and recovery.
* Known limitations and unresolved risks.

Activation is a separate decision. It must never happen automatically because implementation, tests, or deployment succeeded.

## 21. Open decisions

The following must be documented and approved before they become enforced production policy. The authoritative register is [RISK_POLICY.md](RISK_POLICY.md) (financial controls) and [DECISIONS/](DECISIONS/) (engineering choices). Do not invent values during implementation.

* Initial instrument allowlist.
* Maximum leverage and exposure.
* Maximum daily loss and drawdown.
* Position sizing methodology.
* Stop-loss and exit policy.
* Permitted order types.
* Strategy eligibility criteria.
* Paper-trading evaluation window and minimum sample criteria.
* LLM provider, budget, and timeout limits.
* News/sentiment data sources.
* Live activation and emergency procedures (including whether emergency may cancel or close positions).
* Futures position mode and quantity unit semantics relative to `contractMultiplier`.
* Whether the exchange account can deny withdrawal while allowing trade and account reads.

**Product principle:** Aegis may autonomously evaluate and execute eligible trades only after explicit live activation, but no AI component may autonomously weaken its safety boundaries.
