---
name: phase-change
description: >-
  Implements Aegis phase or module feature work using ownership-based
  delegation. Use when adding Phase 2–7 behavior, extending market data,
  features, analysts, evidence, Jev, supervisor, risk, paper, backtest, orders,
  or exchange adapters within an assigned phase.
---

# Phase / module change

Follow `.cursor/skills/agent-orchestration/SKILL.md` first.

## Steps

1. Confirm the assigned phase from the user or `Phases/PhaseN.md`. Stop at that phase’s acceptance line.
2. Map work to owners (`AGENTS.md` phase map). Prefer **one** implementation specialist.
3. Discover once (`explore` if needed). Summarize paths and constraints.
4. Delegate with a minimal handoff JSON (task, phase, files, findings, constraints).
5. Main agent owns ADR edits, owner policy values, `main.py` wiring, Compose, and non-module docs after specialists return.
6. Run `verifier`. For HIGH or safety-touching files, run `safety-reviewer`.
7. Report residuals and open owner decisions. Do not invent risk-policy numbers or unverified Toobit/Jev APIs.

## Parallelism

- `market-data` ∥ `research-pipeline` only if file sets do not overlap.
- Shared schema / settings / `main.py`: serialize first, then parallelize.
- `decision-plane` before `execution-ledger` when proposals feed orders.

## Done when

- Phase acceptance criteria for the scoped work are met or blockers are explicit
- `verifier` has run for non-trivial changes
- `LIMITATIONS.md` / docs updated when behavior stays unverified
