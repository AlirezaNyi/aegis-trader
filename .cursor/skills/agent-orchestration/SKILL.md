---
name: agent-orchestration
description: >-
  Orchestrates Aegis project subagents for multi-step engineering work. Use when
  classifying tasks, deciding whether to delegate, building a dependency graph,
  parallelizing specialists, verifying completions, or escalating to the human.
  Applies to features, bugs, refactors, schema changes, safety-sensitive edits,
  and phase work across market-data, research-pipeline, decision-plane, and
  execution-ledger.
---

# Aegis agent orchestration

The **main Cursor Agent** is the coordinator. Do not spawn an orchestrator subagent. Specialists never launch other subagents.

Read `AGENTS.md` for phase map and invariants. Use this skill for non-trivial work.

## 1. Classify the task

Assign one or more labels:

| Label | Aegis examples |
| --- | --- |
| Feature | New analyst field, new kline quality rule, Phase 4–7 module behavior |
| Bug | Wrong candle finalization, feature look-ahead, flaky test |
| Refactor | Extract parser, simplify runner without behavior change |
| Database | Alembic revision for features/evidence/orders tables |
| Infrastructure | Docker Compose, Dockerfile, dependency pins |
| Security | Live gates, secrets plane, withdraw deny, credential isolation |
| Performance | WS reconnect spam, analyst concurrency, query cost |
| Investigation | Where is X? Impact of changing Y? |
| Review | Verify claim, PR-style review, safety audit |

## 2. Assign risk

| Level | When |
| --- | --- |
| LOW | Docs typo, comment, isolated non-trading helper, formatting |
| MEDIUM | Normal module feature/bug within one ownership set |
| HIGH | Live gates, settings validators, exchange adapter, risk engine, secrets, withdrawals, policy numbers, destructive migrate/`docker compose down -v`, large cross-plane architecture |

HIGH always requires `verifier` and usually `safety-reviewer`.

## 3. Decide whether to delegate

**Main agent works directly** when:

- Rename one symbol, fix a typo, one import, tiny doc edit
- Change is fully understood and fits in a few lines under one file
- No specialized exchange/risk knowledge required

**Delegate** when:

- Specialized module knowledge matters (Toobit MD, features/look-ahead, risk/Jev, orders)
- Context isolation helps (noisy exploration or long test runs)
- Multiple non-overlapping ownership sets can run in parallel
- Independent verification is needed (MEDIUM+ implementations)

## 4. Capability matrix

| Agent | May modify | Never modify | Parallel edits OK with |
| --- | --- | --- | --- |
| `market-data` | `src/aegis/market_data/`, MD schemas/interfaces, MD tests, MD `LIMITATIONS.md` | secrets, orders, features, risk, jev, supervisor | `research-pipeline` if file sets disjoint |
| `research-pipeline` | `features/`, `analysts/`, `evidence/`, feature/evidence schemas, `docs/FEATURES.md`, related tests/migrations | jev, supervisor, risk, orders, exchange | `market-data` if file sets disjoint |
| `decision-plane` | `jev/`, `supervisor/`, `risk/`, `guards/`, proposal/risk schemas (Phases 4–5 when assigned) | risk-policy **values**, exchange submit, live arming | analysis-only peers; **not** `execution-ledger` on shared contracts |
| `execution-ledger` | `orders/`, `paper/`, `backtest/`, `reconcile/`, `exchange/`, orders schema, execution interface (Phases 6–7 when assigned) | APPROVE logic, policy numbers, live flags | not with `decision-plane` on shared schemas |
| `verifier` | nothing in app/docs/config | all product code | after implementers finish |
| `safety-reviewer` | nothing (`readonly`) | everything | after verifier, or readonly parallel with verifier |

**Main agent retains:** phase selection, ADRs, owner policy numbers, `main.py` wiring, Compose/deps, non-module docs, final user reply. Commit only when the user asks.

Built-ins: use `explore` for discovery; use shell for verbose command series. Do not ask every specialist to rediscover the same architecture.

## 5. Discover → summarize → delegate

```text
DISCOVER (explore once)
   → SUMMARIZE (paths, symbols, constraints)
   → DELEGATE (minimal handoff)
   → IMPLEMENT
   → VERIFY
```

Never dump the whole conversation or repo into a subagent prompt.

### Handoff payload (prefer this shape)

```json
{
  "task": "one-sentence goal",
  "phase": "2|3|4|5|6|7|n/a",
  "scope": ["market-data"],
  "relevant_files": ["src/aegis/..."],
  "findings": ["short bullets only"],
  "constraints": ["no invented Toobit fields", "paper default"],
  "dependencies": [],
  "verification_required": true
}
```

Pass **contracts and findings**, not full investigation transcripts. Example: give `execution-ledger` the RiskDecision fields it must honor, not the entire risk-engine draft.

## 6. Dependency and parallel rules

1. Map files → owning agent(s).
2. If shared schema / `main.py` / settings: land the shared change first (main or one specialist), then parallelize the rest.
3. Pipeline order when both apply: `decision-plane` before `execution-ledger`.
4. Parallelize **analysis** freely; parallelize **edits** only when ownership sets do not overlap.
5. Conflicting file edits → serialize (or isolated worktrees only if the user asks).

## 7. Verification gates

After any non-trivial implementation:

1. Run `verifier` with claimed scope + file list + suggested tests.
2. If HIGH **or** diff touches guards, settings, exchange, risk, orders, logging, or env examples → run `safety-reviewer`.
3. On verify fail → return to the owning implementer with the failure evidence only.
4. **Max 2** implement↔verify loops. Then stop and ask the human.

Do not mark done because an implementer said it was done.

## 8. Human escalation (ask; do not guess)

- Ambiguous product requirements
- Two architectures with materially different consequences
- Owner-only: risk-policy numbers, allowlists, live arming, emergency cancel/close
- ADR conflict or request to overturn ADRs 0001–0007
- Destructive DB/ops (`alembic downgrade`, volume wipe)
- Need for real secrets/credentials
- Repeated verify failures
- Unexplained behavior that could affect live safety

Do not ask questions the repo docs already answer.

## 9. Standard specialist return contract

Expect (and ask for) this structure from every subagent:

- **Summary**
- **Findings**
- **Files** (changed or inspected)
- **Decisions**
- **Risks**
- **Verification** (commands actually run)
- **Recommendations** (next parent step)
- **Blockers**

## 10. Workflow skills

| Situation | Also follow |
| --- | --- |
| Phase/module feature | `.cursor/skills/phase-change/SKILL.md` |
| Bug / test failure | `.cursor/skills/bug-fix/SKILL.md` |
| Safety-sensitive / HIGH | `.cursor/skills/safety-change/SKILL.md` |
