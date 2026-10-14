# Analyst limitations (Phase 3)

## Shared

* Analysts never receive exchange credentials, never submit orders, and never call the Risk Engine.
* Missing critical inputs yield `unavailable` / `not_applicable` / `error` — never fabricated facts.
* Concurrent execution is bounded (default concurrency 5, timeout 2s per analyst). One timeout does not cancel the others.
* Analysts are declared once in `aegis.analysts.runner.DEFAULT_ANALYST_REGISTRY` (`AnalystSpec`: type, async factory, declared `AnalystInput`s). `run_analysts` iterates the registry in registration order; output order = registry order. A factory receives only its declared inputs plus `evidence_time`. The registry is keyed by `AnalystType`, so a genuinely new analyst kind also needs a new `AnalystType` enum value in `aegis.schemas.evidence`.
* The broad `except Exception` in the runner is a deliberate isolation boundary: any analyst failure becomes `ERROR` evidence (`reason="error: <msg>"`) and is logged (`analyst_error`, analyst type, exception class). Timeouts log `analyst_timeout`. Sync analysts run via `asyncio.to_thread`; a timed-out thread is not forcibly killed (it finishes in the background, result discarded).
* No runtime `assert` is used for control flow (it would vanish under `python -O`); malformed snapshots (e.g. status `ok` with `value=None`) are treated as missing -> `unavailable`.
* Jev and LLM Supervisor are **out of scope** for Phase 3.

## Technical

* Labels (`trend`, `rsi_zone`, `volatility_label`, `volume_confirm`) are descriptive thresholds, not order signals.
* Requires `sma_20`, `ema_20`, `rsi_14`, and `atr_14` at `ok`; otherwise the whole evidence is `unavailable`.
* Payload also carries raw `rsi_14`, `ema_20`, `sma_20`, `volume_ratio` as Decimal strings (`volume_ratio` is `null` when the feature is not `ok`). These are copies of snapshot values, not signals.
* `aegis.strategy.decide` (deterministic `rules-v1`) consumes `trend`, `rsi_14`, and `volume_ratio`. Renaming or retyping those keys silently turns every rule decision into NO_TRADE; keep them stable.

## Quantitative

* Needs at least **30** simple returns for an `ok` summary. That threshold is a **reporting minimum**, not a risk or position-size limit.
* Output explicitly states it is not a profitability claim.
* `mean_simple_return` is computed over **all** supplied final candles up to `as_of`, so its window depends on how much history the caller passes (e.g. a backtest replay window). `aegis.strategy` uses it as a sign gate only.

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
