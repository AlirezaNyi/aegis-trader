---
name: safety-change
description: >-
  Handles high-risk Aegis trading-safety changes. Use when editing live gates,
  settings validators, exchange adapter, risk engine, order submission,
  secrets handling, kill switch, ledger_kind, withdrawals, or threat-model
  boundaries; also for security reviews of those areas.
---

# Safety-sensitive change

Follow `.cursor/skills/agent-orchestration/SKILL.md`. Treat risk as **HIGH**.

## Rules

- Paper is default. Do not arm live mode or invent risk-policy numbers.
- Toobit secrets stay in the exchange/credential plane only.
- Never call withdrawal endpoints or add Agent Trade Kit / LangGraph tool loops.
- Prefer REJECT / NO_TRADE when critical state is missing or unapproved.

## Steps

1. Identify trust boundary touched (`docs/THREAT_MODEL.md`, INV-01–08 in `docs/TEST_PLAN.md`).
2. If requirements need owner values (leverage, loss limits, allowlists, live arming) → **ask the human** before coding.
3. Delegate to `decision-plane` and/or `execution-ledger` by ownership; never let analysts/Jev/supervisor receive credentials or submit tools.
4. Implement with tests for the invariant under change.
5. Always run `verifier`, then `safety-reviewer` (readonly findings).
6. Main agent integrates; do not claim live-ready unless the owner approved gates and policy.

## Review-only requests

If the user asks only for a review: run `safety-reviewer` (and `verifier` if they claim completion). Do not patch unless asked.
