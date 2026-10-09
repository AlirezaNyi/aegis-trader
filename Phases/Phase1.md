# Aegis Phase 1 — Repository Foundation

Implement the approved repository foundation for Aegis.

Read the approved PRD, SRS, RFC, architecture, risk policy, and data contracts first (`docs/`). Treat them as the source of truth. Package and project-owned service identifiers are fixed as `aegis` (do not invent alternate package names).

Implement:

1. The approved Python package structure using `aegis` as the package name.
2. Typed configuration and startup validation.
3. Structured logging and correlation IDs.
4. Shared Pydantic schemas for market data, evidence, proposals, risk decisions, orders, fills, and positions.
5. Interfaces for exchange data, execution, Jev, LLM providers, persistence, and time.
6. Health and readiness endpoints.
7. Database migration infrastructure if approved.
8. pytest, Ruff, and mypy configuration.
9. Docker Compose for local development.
10. `.env.example` with placeholders only.
11. A live-mode guard that prevents accidental real execution.
12. Initial unit tests and developer documentation.

Rules:

* No real exchange execution.
* No real API keys.
* No secrets in source control.
* No fabricated provider integrations.
* No employer or corporate branding.
* Keep external dependencies minimal.
* Paper Trading must be the default.

Acceptance criteria:

* The application starts locally.
* Invalid configuration fails clearly.
* Tests and static checks pass.
* No order can reach a real exchange.
* Interfaces and error behavior are documented.

Report changed files, tests actually run, architecture deviations, and unresolved questions. Stop after this phase.
