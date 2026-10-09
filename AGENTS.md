# Aegis — Agent Guide

Personal autonomous trading system for Toobit Spot and Futures. Package identity: `aegis`. Repository folder may be named `Ageis`; use package name `aegis` in code and config.

Source of truth for product and architecture: [`docs/`](docs/). Phase guides: [`Phases/`](Phases/). Architecture decisions: [`docs/DECISIONS/`](docs/DECISIONS/).

## Current state

- **Implemented through Phase 8:** foundation through Order Manager + Toobit adapters + reconciler, plus deployment readiness (Compose resource limits, `/metrics`, alert dry-run, correlation middleware, graceful shutdown, backup/restore scripts, ops docs, readiness report). Final system audit: [`docs/FINAL_SYSTEM_AUDIT.md`](docs/FINAL_SYSTEM_AUDIT.md) — **PAPER-TRADING READY**; live not activated. LLM provider choice, spend budgets, and risk-policy numerics remain owner-approved / empty-or-draft by default.
- **Stubbed:** `validation` (pipeline stage). Live submit client is **not constructed** in `create_app` until owner arms all live gates and wires `build_execution_port`.
- **Exchange adapter:** credential-plane HMAC adapters exist (`LIVE_SUBMIT_SUPPORTED=True`); default runtime uses `NullExecutionPort`. Never call withdrawal endpoints.
- **Default mode:** paper. Live trading requires explicit owner approval and all live gates. Phase 8 does **not** activate live.

## Decision pipeline

Market Data → Validation → Feature Engine → Specialized Analysts → Evidence Builder → Jev → LLM Supervisor → Deterministic Risk Engine → Order Manager → Toobit Adapter / Paper → Reconciliation.

The Risk Engine is the only authority that may APPROVE a new trade. Analysts, Jev, and the Supervisor never receive Toobit credentials, never submit orders, and never override hard risk limits.

## Phase map

| Phase | Focus | Primary code |
| --- | --- | --- |
| 1 | Foundation, guards, schemas, health | `src/aegis/config`, `guards`, `schemas`, `interfaces`, `api`, `db` |
| 2 | Toobit public market data | `src/aegis/market_data` |
| 3 | Features, analysts, evidence | `src/aegis/features`, `analysts`, `evidence` |
| 4 | Jev + LLM Supervisor | `src/aegis/jev`, `supervisor` |
| 5 | Deterministic Risk Engine | `src/aegis/risk` |
| 6 | Backtest + paper trading | `src/aegis/backtest`, `paper` |
| 7 | Order execution + reconcile | `src/aegis/orders`, `exchange`, `reconcile` |
| 8 | Deployment readiness | Compose, ops docs, metrics |

Stop at the assigned phase acceptance line. Do not implement later phases unless the parent explicitly assigns them.

## Safety invariants

See [`docs/TEST_PLAN.md`](docs/TEST_PLAN.md) (`INV-01`–`INV-08`) and [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

1. Rejected risk decisions never reach adapter submit.
2. Models never access Toobit credentials.
3. Live submit requires `trading_mode=live`, `live_armed=true`, and `kill_switch=false`.
4. Submit timeout / HTTP 5xx → reconcile before any second submit; never blind retry.
5. Stale or missing critical data → REJECT new orders.
6. Never call withdrawal endpoints.
7. Paper and live ledgers stay separate (`ledger_kind`).
8. Invalid proposal schema never reaches Risk APPROVE.
9. Do not invent unverified Toobit or Jev endpoints, fields, or behaviors.
10. Do not invent numeric risk-policy values; they are UNAPPROVED until the owner sets them.
11. Do not use LangGraph-style agent tool loops (ADR 0002) or the Toobit Agent Trade Kit (ADR 0007).
12. Do not read or print secrets from `.env`, credentials files, or private keys. Use `.env.example` only.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # placeholders only

uvicorn aegis.main:app --reload --app-dir src
docker compose up --build

alembic upgrade head

pytest
ruff check src tests
mypy src
```

## Subagent delegation

Project subagents live in [`.cursor/agents/`](.cursor/agents/). The main agent chooses phase and scope, then delegates:

| Subagent | When to use |
| --- | --- |
| `market-data` | Toobit public REST/WS, candles, quality, fixtures |
| `research-pipeline` | Features, analysts, evidence packages |
| `decision-plane` | Jev, Supervisor, Risk Engine, live guards (Phases 4–5) |
| `execution-ledger` | Orders, paper, backtest, reconcile, exchange adapter (Phases 6–7) |
| `verifier` | After implementation: run tests and static checks |
| `safety-reviewer` | After risky diffs: threat-model and safety review (readonly) |

Rules:

- Prefer one implementation subagent per change. Parallelize only when owned file sets do not overlap.
- Specialists return control to the parent; they do not launch other subagents.
- Parent runs `verifier` after implementation, then `safety-reviewer` when the diff touches guards, settings, exchange, risk, orders, logging, or env examples.
- Main agent retains: ADR authorship, owner-only policy values, `main.py` wiring, Docker/Compose, and non-module docs.

## Orchestration

The **main Cursor Agent** is the engineering orchestrator. There is no separate orchestrator subagent.

Skills:

| Skill | Use for |
| --- | --- |
| [`.cursor/skills/agent-orchestration/SKILL.md`](.cursor/skills/agent-orchestration/SKILL.md) | Classify task, risk, dependency graph, handoffs, verify, escalate |
| [`.cursor/skills/phase-change/SKILL.md`](.cursor/skills/phase-change/SKILL.md) | Phase/module feature work |
| [`.cursor/skills/bug-fix/SKILL.md`](.cursor/skills/bug-fix/SKILL.md) | Bugs and failing tests |
| [`.cursor/skills/safety-change/SKILL.md`](.cursor/skills/safety-change/SKILL.md) | Live gates, secrets, risk, exchange, HIGH-risk changes |

Operating principles:

- **Trivial work:** main agent edits directly (typo, one import, tiny isolated change). Do not delegate.
- **Discover once:** use built-in `explore` when needed; pass short findings + file paths to specialists — not the whole conversation.
- **Parallel edits:** only when ownership file sets do not overlap. Serialize shared schemas, settings, and `main.py`.
- **Pipeline order:** when both apply, `decision-plane` before `execution-ledger`.
- **Gates:** implement → `verifier` → `safety-reviewer` for HIGH or safety-touching diffs. Max two implement↔verify loops, then ask the human.
- **Escalate:** owner policy numbers, live arming, ADR conflicts, destructive DB/ops, secrets, ambiguous product choices, repeated failures.
- **Commits:** only when the user explicitly asks.

Hooks (safety only — not orchestration loops): [`.cursor/hooks.json`](.cursor/hooks.json) blocks secret file reads and dangerous trading/ops shell commands.

## Docs map

| Doc | Use for |
| --- | --- |
| [`docs/PRD.md`](docs/PRD.md) | Goals, non-goals, authority |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Components and state machines |
| [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) | Verified vs unverified external APIs |
| [`docs/RISK_POLICY.md`](docs/RISK_POLICY.md) | Risk rules; numeric values remain owner-approved |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Persistence entities |
| [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) | Trust boundaries |
| [`docs/TEST_PLAN.md`](docs/TEST_PLAN.md) | Invariants and phase tests |
| Module `LIMITATIONS.md` | Known gaps for that module |
