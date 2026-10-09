# Supervisor limitations (Phase 4)

## Authority

* Produces validated `TradeProposal` only. Never submits orders, never imports `aegis.exchange` / `aegis.orders`, never receives Toobit credentials.
* Cannot APPROVE trades — deterministic Risk Engine (Phase 5) remains sole APPROVE authority.
* Prefer `NO_TRADE` when critical evidence is missing/unavailable/error, contradictory, or injection-tainted.

## LLM provider

* Provider choice remains **UNAPPROVED**. `build_llm_port` returns `UnavailableLlmPort` until an owner-approved adapter exists.
* Empty `AEGIS_LLM_PROVIDER` / key / model **or** empty token/cost/latency budgets → `NO_TRADE` with **no network call**.
* Token, cost, and latency budgets are enforced by the supervisor, not by `LlmPort`.
* When budgets are set, LLM `_meta` must include token and cost usage; missing usage → NO_TRADE.
* Evidence dumped into prompts is scrubbed with the same credential redactor as the Jev adapter.

## Gates

* Missing/unavailable/error **technical** or **quantitative** → NO_TRADE. News `UNAVAILABLE` alone is allowed.
* Hard direction contradiction (technical trend vs quantitative direction / mean-return sign) → NO_TRADE.
* Jev OK alignment that clearly contradicts shared analyst direction → NO_TRADE.
* Prompt-injection markers in **any** analyst notes/sources/payload text → NO_TRADE (content treated as untrusted DATA).

## Confidence

* Any model-reported confidence is stored under `uncertainty` as **uncalibrated**.
* Identity fields (`proposal_id`, `correlation_id`, instrument, timeframe, analyst_results, jev_result, `created_at`) are overwritten from the evidence package — not trusted from the model.

## Deferred

* Cost is checked against provider-reported `_meta.cost` when present; no live pricing table is hard-coded.
* Concrete LLM HTTP adapters are out of scope until owner selects a provider.
