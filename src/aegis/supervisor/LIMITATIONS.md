# Supervisor limitations (Phase 4)

## Authority

* Produces validated `TradeProposal` only. Never submits orders, never imports `aegis.exchange` / `aegis.orders`, never receives Toobit credentials.
* Cannot APPROVE trades — deterministic Risk Engine (Phase 5) remains sole APPROVE authority.
* Prefer `NO_TRADE` when critical evidence is missing/unavailable/error, contradictory, or injection-tainted.

## LLM provider

* Supported OpenAI-compatible adapters (owner env): **`gemini`**, **`openrouter`**, **`groq`**.
  * Defaults:
    * Gemini `https://generativelanguage.googleapis.com/v1beta/openai` (official OpenAI-compat)
    * OpenRouter `https://openrouter.ai/api/v1`
    * Groq `https://api.groq.com/openai/v1`
  * Override with `AEGIS_LLM_BASE_URL` when needed.
* Recommended free soak for **personal** accounts (medium volume): **`gemini`** + a free Flash model from AI Studio (e.g. `gemini-2.5-flash`). Create key at aistudio.google.com — org/Groq signup not required.
* OpenRouter `:free` (~50 RPD without purchased credits) is for low-volume manual tests only.
* Groq remains supported if the owner can create an account; some personal signups are blocked without an organization.
* Empty / unknown `AEGIS_LLM_PROVIDER` or missing key/model → `UnavailableLlmPort` → `NO_TRADE` with **no network call**.
* Empty token/cost/latency budgets → `NO_TRADE` with **no network call**.
* `AEGIS_LLM_COST_BUDGET=0` is allowed for free tiers; adapter always reports `_meta.cost` (0 when upstream omits cost).
* Token and latency budgets must still be **> 0** when set.
* When budgets are set, LLM `_meta` must include token and cost usage; missing usage → NO_TRADE.
* Evidence dumped into prompts is scrubbed with the same credential redactor as the Jev adapter.
* API keys are never logged; HTTP error messages omit response bodies.

## Gates

* Missing/unavailable/error **technical** or **quantitative** → NO_TRADE. News `UNAVAILABLE` alone is allowed.
* Hard direction contradiction (technical trend vs quantitative direction / mean-return sign) → NO_TRADE.
* Jev OK alignment that clearly contradicts shared analyst direction → NO_TRADE.
* Prompt-injection markers in **any** analyst notes/sources/payload text → NO_TRADE (content treated as untrusted DATA).

## Confidence

* Any model-reported confidence is stored under `uncertainty` as **uncalibrated**.
* Identity fields (`proposal_id`, `correlation_id`, instrument, timeframe, analyst_results, jev_result, `created_at`) are overwritten from the evidence package — not trusted from the model.

## Deferred

* Cost is checked against provider-reported `_meta.cost` when present; free adapters map missing cost to `0`.
* Provider free-tier rate limits change over time — re-check Groq / OpenRouter docs before relying on soak volume.
* Jev remains TypeSafe-only (not OpenRouter).
