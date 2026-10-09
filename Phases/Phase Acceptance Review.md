# Aegis — Independent Phase Acceptance Review

Review the current Aegis implementation against the approved PRD and the current phase's acceptance criteria.

Do not modify code before reporting findings.

Inspect:

* Functional correctness and architecture.
* Security boundaries and secret handling.
* Risk Engine enforcement.
* Data freshness and state consistency.
* Timeout, retry, and recovery behavior.
* Duplicate-order prevention.
* Test coverage and test quality.
* Resource usage and unbounded concurrency.
* Documentation accuracy.
* Unverified external API assumptions.

Run the relevant tests and static checks. Report only tests that were actually executed.

Classify findings as CRITICAL, HIGH, MEDIUM, LOW, or INFORMATIONAL. Each finding must include evidence, affected files/components, impact, and recommended remediation.

Explicitly test whether:

1. Any path bypasses Risk Engine approval.
2. Any LLM or Jev component can execute orders or access exchange credentials.
3. A submission timeout can cause a duplicate order.
4. Stale data or unknown account state can permit a new order.
5. Live mode can activate without explicit owner configuration.
6. External API behavior has been invented rather than verified.

Conclude with PASS, PASS WITH NON-BLOCKING ISSUES, or FAIL.

Do not proceed to the next phase automatically. Report blockers and wait for approval before substantial remediation.
