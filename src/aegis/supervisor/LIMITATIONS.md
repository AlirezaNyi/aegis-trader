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
* Optional owner-set `AEGIS_LLM_DAILY_CALL_CAP` (empty = no cap; must be > 0 when set; no default
  value is chosen by engineering). `factory.build_call_limiter` returns an in-process
  `DailyCallLimiter`; once the cap is hit for the current UTC day, `supervise()` returns NO_TRADE
  with reason `llm_daily_call_cap_reached` and makes **no network call**. The cap is checked only
  after pre-LLM gates, budgets, and provider checks pass, so gate-rejected cycles do not consume it.
  Counter is in-memory only: it resets on process restart and is not shared across processes.
  The parent (pipeline/main) must wire the limiter; the default `call_limiter=None` keeps prior behaviour.
* HTTP 429 from the provider raises `LlmRateLimitedError` (message `LLM HTTP 429`, no body, no retry)
  and `supervise()` returns NO_TRADE with reason `llm_rate_limited` (distinct from `llm_call_failed`).
  No backoff/cool-down is implemented; the next cycle will call again unless the cap blocks it.
* Evidence dumped into prompts is scrubbed with the same credential redactor as the Jev adapter.
* API keys are never logged; HTTP error messages omit response bodies.

## Gates

* Missing/unavailable/error **technical** or **quantitative** → NO_TRADE. News `UNAVAILABLE` alone is allowed.
* Hard direction contradiction (technical trend vs quantitative direction / mean-return sign) → NO_TRADE.
* Jev OK alignment that clearly contradicts shared analyst direction → NO_TRADE.
* Prompt-injection markers in **any** analyst notes/sources/payload text → NO_TRADE (content treated as untrusted DATA).

## Output contract

* Preferred LLM output is the compact `SupervisorDecision` (`supervisor/decision.py`):
  action, direction, entry_price, stop_loss, take_profit, rationale, invalidation, confidence_note.
  Unknown keys are dropped.
* Code fills identity, `entry_conditions.order_type` / `time_in_force`, `sizing`, and `expires_at`
  from `ProposalDefaults`. The cycle derives them only from APPROVED policy values
  (`pipeline/defaults.py`). Missing or ambiguous values stay empty, so Risk rejects.
* The model never sets notional or leverage on the compact path. HOLD / NO_TRADE are forced flat.
* Policy 1.1 sizing (`ProposalDefaults.position_cap_usdt`, `risk_budget_usdt`, `cost_bps_round_trip`):
  `pct_capital` ⇒ notional = position cap; `risk_pct_capital` ⇒
  `min(cap, budget × entry / (|entry − SL| + entry × cost_bps / 10000))`; both rounded **down** to 0.01.
  Missing inputs drop `notional` (Risk rejects). The caller must derive these only from APPROVED policy
  values; `cost_bps_round_trip` should be a conservative upper bound (e.g. using the approved max spread)
  because the Risk Engine re-checks `RP-MAX-RISK-PER-TRADE` with the live `RiskContext` costs and will
  reject if actual costs exceed the sizing estimate.
* When `ProposalDefaults.min_reward_risk` is set, the prompt marks `take_profit` REQUIRED and states the
  net reward:risk formula with `cost_bps_round_trip`. These are hints only; numbers come from code-owned
  defaults, never from evidence. The Risk Engine enforces `RP-MIN-REWARD-RISK` / `RP-TAKE-PROFIT-POLICY`.
* Legacy full-`TradeProposal` payloads (with `proposal_id`) are still accepted for tests.
* Not yet verified against a real provider: how often a given model returns valid compact JSON.

## Confidence

* Any model-reported confidence is stored under `uncertainty` as **uncalibrated**.
* Identity fields (`proposal_id`, `correlation_id`, instrument, timeframe, analyst_results, jev_result, `created_at`) are overwritten from the evidence package — not trusted from the model.

## Deferred

* Cost is checked against provider-reported `_meta.cost` when present; free adapters map missing cost to `0`.
* Provider free-tier rate limits change over time — re-check Groq / OpenRouter docs before relying on soak volume.
* Jev remains TypeSafe-only (not OpenRouter).
* `supervise()` and `LlmPort.complete_structured` are async (`httpx.AsyncClient`). Any provider exception other than
  `LlmRateLimitedError` is a deliberate fail-closed boundary: NO_TRADE with `llm_call_failed`, logged as event
  `supervisor_llm_call_failed` with the exception class name only. `asyncio.CancelledError` is not swallowed.
