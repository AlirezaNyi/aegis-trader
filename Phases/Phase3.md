# Aegis Phase 3 — Features and Analytical Agents

Implement the deterministic Feature Engine and five specialized analysts:

1. Technical Analyst.
2. Quantitative Analyst.
3. News and Sentiment Analyst.
4. Analytical Risk Analyst.
5. Strategy Researcher.

Each analyst must use typed inputs and produce structured evidence with source references, timestamps, strategy/version metadata where relevant, and explicit uncertainty or unavailable states.

Requirements:

* Define feature formulas and candle warm-up requirements.
* Distinguish finalized data from intrabar observations.
* Prevent look-ahead bias.
* Handle insufficient sample sizes explicitly.
* Do not fabricate news, sentiment, or unavailable provider output.
* Treat retrieved external content as untrusted data.
* Keep the Analytical Risk Analyst separate from the deterministic Risk Engine.
* Record strategy hypotheses and experiment versions.
* Use bounded concurrency and timeouts.
* Add deterministic unit tests and mocked external dependencies.

No analyst may submit orders or access exchange credentials.

Acceptance criteria:

* Contracts are documented.
* Missing data produces explicit UNKNOWN/UNAVAILABLE states.
* Time alignment and look-ahead safeguards are tested.
* Tests and static checks pass.

Report each analyst's limitations and stop after this phase.
