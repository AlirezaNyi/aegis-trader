---
name: bug-fix
description: >-
  Fixes Aegis bugs with ownership-aware delegation and verification. Use when
  tests fail, runtime errors occur, candle/feature/analyst logic is wrong, or
  a regression appears in market data, research pipeline, decision plane, or
  execution modules.
---

# Bug fix

Follow `.cursor/skills/agent-orchestration/SKILL.md` first.

## Steps

1. Reproduce from evidence: failing pytest node id, traceback, or observed wrong output. Prefer fixtures/mocks — never place real exchange orders.
2. Classify owning module. Trivial one-liners → main agent. Otherwise one specialist.
3. Root-cause with minimal scope (`explore` / targeted reads). Do not rewrite unrelated modules.
4. Implement the smallest correct fix. Update or add a deterministic test that would have caught it.
5. Run `verifier` with the focused test set. Retry implement↔verify at most **twice**.
6. If the bug touched guards, settings, exchange, risk, orders, logging, or env examples → `safety-reviewer`.
7. Escalate if the failure is unexplained after two loops or requires owner policy decisions.

## Anti-patterns

- Broad refactors “while here”
- Inventing exchange fields to make a parser “work”
- Skipping tests because “obvious”
- Weakening live gates to silence a startup error
