# Aegis Phase 4 — Jev AI and LLM Supervisor

Implement Jev evaluation and the LLM Supervisor.

First verify official Jev documentation for authentication, endpoints, request/response schemas, supported capabilities, rate limits, and error handling. If verification is impossible, implement only an adapter interface and clearly labeled mock.

Jev adapter requirements:

* Typed request and response schemas.
* Explicit timeout and bounded retry behavior.
* Rate-limit and provider-error handling.
* Correlation IDs and version metadata.
* Validation of malformed and missing responses.
* No exchange credentials or execution tools.

Supervisor requirements:

* Review analyst evidence and Jev's independent assessment.
* Identify agreement, disagreement, stale data, missing evidence, and contradictions.
* Produce a validated BUY, SELL, HOLD, or NO_TRADE proposal.
* Include instrument, market type, direction, timeframe, strategy version, entry conditions, proposed exits, invalidation conditions, evidence references, expiry, and uncertainty.
* Prefer NO_TRADE when critical evidence is unavailable.
* Treat model-reported confidence as uncalibrated unless empirically validated.
* Enforce token, latency, and cost budgets.
* Defend against prompt injection in external content.

The Supervisor must not execute orders, access exchange credentials, activate live mode, or override the Risk Engine.

Test provider outages, malformed outputs, contradictions, timeouts, prompt injection, and cost limits. Keep live execution disabled. Report verified Jev capabilities and stop after this phase.
