# Aegis — Architecture

**Version:** 1.0
**Status:** Proposed (Phase 0)
**Package:** `aegis`
**Related:** [PRD.md](PRD.md), [RFC.md](RFC.md), [THREAT_MODEL.md](THREAT_MODEL.md)

## 1. Classification legend

| Label | Use in this document |
| --- | --- |
| **Verified** | From official Toobit / TypeSafe docs (2026-10-09) |
| **Assumption** | Operational premise |
| **Proposed** | Design choice pending ADR acceptance |
| **Unresolved** | Needs owner or further documentation verification |

## 2. System context

Aegis is a personal trading system. External actors: project owner, Toobit exchange, TypeSafe Jev, optional LLM provider, optional configured news sources.

```mermaid
flowchart TB
  owner[Owner]
  aegis[AegisMonolith]
  pg[(PostgreSQL)]
  toobit[ToobitAPI]
  jev[TypeSafeJev]
  llm[LlmProvider]
  news[NewsSources]

  owner -->|control_and_config| aegis
  aegis --> pg
  aegis -->|market_and_trade| toobit
  aegis -->|systemone| jev
  aegis -->|structured_prompt| llm
  aegis -->|fetch_if_configured| news
```

## 3. Component view

**Proposed:** modular monolith with the pipeline below. One Toobit adapter module exposes Spot and Futures ports.

```mermaid
flowchart LR
  marketData[MarketData]
  validation[Validation]
  features[FeatureEngine]
  analysts[SpecializedAnalysts]
  evidence[EvidenceBuilder]
  jev[JevEvaluator]
  supervisor[LlmSupervisor]
  risk[RiskEngine]
  orders[OrderManager]
  toobit[ToobitAdapter]
  paper[PaperBroker]
  reconcile[Reconciliation]

  marketData --> validation
  validation --> features
  features --> analysts
  analysts --> evidence
  evidence --> jev
  jev --> supervisor
  supervisor --> risk
  risk -->|approve_paper| paper
  risk -->|approve_live| orders
  orders --> toobit
  toobit --> reconcile
  paper --> reconcile
```

### Component responsibilities

| Component | Responsibility | May hold exchange secrets |
| --- | --- | --- |
| Market Data | REST/WS ingest | No (public) / signed only if USER_STREAM requires key in adapter |
| Validation | Integrity, freshness, finalization | No |
| Feature Engine | Deterministic features | No |
| Analysts | Structured evidence | No |
| Evidence Builder | Versioned package | No |
| Jev Evaluator | System One call or mock | TypeSafe key only, never Toobit |
| LLM Supervisor | Schema proposal / NO_TRADE | Provider key only, never Toobit |
| Risk Engine | APPROVE / REJECT | No |
| Order Manager | Lifecycle, idempotency | No |
| Toobit Adapter | Sign and call Toobit | **Yes — only here** |
| Paper Broker | Simulated fills | No |
| Reconciliation | Compare local vs exchange | Reads via adapter |

## 4. Decision sequence

```mermaid
sequenceDiagram
  participant MD as MarketData
  participant VAL as Validation
  participant FE as FeatureEngine
  participant AN as Analysts
  participant EV as EvidenceBuilder
  participant JV as Jev
  participant SV as Supervisor
  participant RK as RiskEngine
  participant OM as OrderManager
  participant EX as ToobitOrPaper

  MD->>VAL: raw events
  VAL->>FE: validated candles
  FE->>AN: features
  AN->>EV: analyst evidence
  EV->>JV: evidence package
  JV->>SV: jev result or unavailable
  SV->>SV: schema validate proposal
  SV->>RK: TradeProposal
  alt any hard rule fails
    RK-->>SV: REJECT
  else approved and paper mode
    RK->>EX: paper simulate
  else approved and live armed
    RK->>OM: RiskDecision APPROVE
    OM->>EX: submit with clientOrderId
    EX-->>OM: ack or unknown
    OM->>OM: reconcile if unknown
  end
```

## 5. Candle lifecycle state machine

**Proposed** internal states (exchange stream may update open candles continuously).

```mermaid
stateDiagram-v2
  [*] --> Receiving
  Receiving --> Partial: open_interval
  Partial --> Partial: intrabar_update
  Partial --> Finalized: interval_closed
  Partial --> GapDetected: missing_bars
  Finalized --> Archived: persisted
  GapDetected --> Receiving: backfill_or_mark_stale
  Receiving --> Stale: freshness_timeout
  Stale --> Receiving: reconnect_and_resync
```

Rules:

* Trading decisions that require finalized bars use only `Finalized` candles unless a strategy explicitly opts into intrabar (**Unresolved** which strategies may).
* `Stale` or `GapDetected` without successful resync blocks new Risk APPROVE for affected instruments.

## 6. Proposal lifecycle

```mermaid
stateDiagram-v2
  [*] --> Drafted
  Drafted --> SchemaInvalid: validation_fail
  Drafted --> PendingRisk: schema_ok
  SchemaInvalid --> [*]
  PendingRisk --> Rejected: risk_reject
  PendingRisk --> Expired: past_expiry
  PendingRisk --> Approved: risk_approve
  Approved --> PaperSimulated: paper_mode
  Approved --> Submitted: live_submit
  Submitted --> Unknown: timeout_or_http5xx
  Submitted --> Accepted: exchange_ack
  Unknown --> Reconciling: query_exchange
  Reconciling --> Accepted: found
  Reconciling --> Failed: not_found_and_policy_allows_fail
  Accepted --> PartiallyFilled: partial
  Accepted --> Filled: full
  PartiallyFilled --> Filled: remaining_done
  PaperSimulated --> Closed: paper_outcome
  Filled --> Closed: outcome_recorded
  Rejected --> [*]
  Expired --> [*]
  Failed --> [*]
  Closed --> [*]
```

## 7. Order state machine (local)

Map exchange statuses (**Verified** Toobit enums include `PENDING_NEW`, `NEW`, `PARTIALLY_FILLED`, `FILLED`, `PENDING_CANCEL`, `CANCELED`, `REJECTED`) onto local states:

```mermaid
stateDiagram-v2
  [*] --> Created
  Created --> SubmitAttempted: send
  SubmitAttempted --> PendingNew: pending_new
  SubmitAttempted --> Unknown: timeout_or_5xx
  PendingNew --> Open: new
  Open --> PartiallyFilled: partial
  Open --> Filled: filled
  Open --> PendingCancel: cancel_sent
  PartiallyFilled --> Filled: filled
  PartiallyFilled --> PendingCancel: cancel_sent
  PendingCancel --> Canceled: canceled
  Open --> Rejected: rejected
  Unknown --> Open: reconcile_found_open
  Unknown --> Filled: reconcile_found_filled
  Unknown --> Missing: reconcile_not_found
  Missing --> FailedTerminal: owner_or_policy
  Filled --> [*]
  Canceled --> [*]
  Rejected --> [*]
  FailedTerminal --> [*]
```

**Critical rule:** From `Unknown`, never create a second submit for the same intent without reconciliation. Prefer `newClientOrderId` (**Verified** Spot optional; Futures v2 mandatory).

## 8. Live trading gates

```mermaid
flowchart TD
  proposal[ApprovedRiskDecision]
  mode{trading_mode_live}
  armed{live_armed}
  kill{kill_switch_off}
  recon{reconciliation_ok}
  submit[ToobitAdapterSubmit]
  block[BlockNewOrders]

  proposal --> mode
  mode -->|no| block
  mode -->|yes| armed
  armed -->|no| block
  armed -->|yes| kill
  kill -->|no| block
  kill -->|yes| recon
  recon -->|no| block
  recon -->|yes| submit
```

## 9. Error handling principles

| Condition | Behavior |
| --- | --- |
| Stale market data | REJECT new trades; mark not ready |
| Jev timeout / 429 / 529 | Record unavailable; Supervisor may NO_TRADE; bounded retries only within evidence freshness window |
| LLM malformed output | Schema fail → NO_TRADE / drop; no execution |
| Risk REJECT | Stop; audit reason codes |
| Submit timeout / HTTP 5XX | **Verified** Toobit: execution status UNKNOWN → reconcile, no blind retry |
| Rate limit 429 | Back off until `X-Api-Limit-Reset-Timestamp` |
| Process restart | Reload open intents; reconcile before new orders |

## 10. Data stores

**Proposed:**

* PostgreSQL: instruments, candles, features, evidence, proposals, risk decisions, orders, fills, positions, paper ledger, audit, policy versions.
* Local filesystem or object storage for large backtest artifacts (optional later).
* No Redis in initial design.

## 11. Deployment topology

**Proposed:** Docker Compose on a single host: `aegis` app + PostgreSQL (+ optional Prometheus/Grafana). See [DEPLOYMENT.md](DEPLOYMENT.md).

## 12. Unresolved architecture questions

* Exact Futures private REST/WS paths for positions and balances (adapter interface until verified).
* Owner-approved emergency cancel/close automation.
* Whether Spot should migrate to API v2 once verified.
* LLM provider selection and budgets.
