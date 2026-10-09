# Analyst limitations (Phase 3)

## Shared

* Analysts never receive exchange credentials, never submit orders, and never call the Risk Engine.
* Missing critical inputs yield `unavailable` / `not_applicable` / `error` — never fabricated facts.
* Concurrent execution is bounded (default concurrency 5, timeout 2s per analyst). One timeout does not cancel the others.
* Jev and LLM Supervisor are **out of scope** for Phase 3.

## Technical

* Labels (`trend`, `rsi_zone`, `volatility_label`, `volume_confirm`) are descriptive thresholds, not order signals.
* Requires `sma_20`, `ema_20`, `rsi_14`, and `atr_14` at `ok`; otherwise the whole evidence is `unavailable`.

## Quantitative

* Needs at least **30** simple returns for an `ok` summary. That threshold is a **reporting minimum**, not a risk or position-size limit.
* Output explicitly states it is not a profitability claim.

## News and Sentiment

* No owner-approved news sources yet (PRD open decision). With empty input → `unavailable` / `no_configured_source`.
* When items are supplied, text is stored as **untrusted** data. No fabricated sentiment score is produced.
* News text must never be treated as system instructions or alter control flow.

## Analytical Risk

* Separate from the deterministic Risk Engine (`aegis.risk`).
* Does not emit APPROVE/REJECT, sizing, or leverage.
* Spread and funding appear only when callers provide them; otherwise those fields are `unavailable`.

## Strategy Researcher

* Records draft hypotheses with leakage controls (finalized bars, `as_of` enforcement).
* Does not activate or mutate production strategies.
* Without a hypothesis → `not_applicable`.
