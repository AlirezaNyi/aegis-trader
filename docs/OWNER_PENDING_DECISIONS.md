# Aegis — Owner pending decisions (paper-evidence stage)

**Date:** 2026-10-09  
**Posture:** Paper default. Live remains disarmed.  
**Rule:** Do not invent numeric SE-* thresholds, LLM spend budgets, or ops channel values. Record status only.

This register documents decisions the owner must supply before a live-activation review can claim “evaluation criteria met.” Until then, paper soak is **system learning only**.

## 1. Strategy evaluation thresholds (SE-*)

Authoritative table: [STRATEGY_EVALUATION.md](STRATEGY_EVALUATION.md) §4.

| Parameter | Status | Value |
| --- | --- | --- |
| SE-PAPER-WINDOW | **UNAPPROVED** | — |
| SE-MIN-TRADES | **UNAPPROVED** | — |
| SE-MIN-INSTRUMENTS | **UNAPPROVED** | — |
| SE-MAX-DRAWDOWN-PAPER | **UNAPPROVED** | — |

**Stage implication:** Any paper soak run dated after this register is evidence of pipeline health only. It does **not** satisfy promotion or live-review sample criteria.

## 2. LLM / Supervisor spend

| Decision | Status | Notes |
| --- | --- | --- |
| LLM provider / model | **Owner-selectable** (`gemini` / `openrouter` / `groq`; empty → fail-closed) | Adapter in `supervisor/openai_compat.py`. Recommended personal free: Gemini `gemini-2.5-flash` (AI Studio key). Groq optional if account available. Key stays in owner `.env` only. |
| `AEGIS_LLM_TOKEN_BUDGET` | **Owner-set** (example in `.env.example`: 8000) | Empty ⇒ no real LLM call |
| `AEGIS_LLM_COST_BUDGET` | **Owner-set** (may be `0` for free tier) | Empty ⇒ fail-closed; `0` allowed when adapter reports cost 0 |
| `AEGIS_LLM_LATENCY_BUDGET_MS` | **Owner-set** (example: 15000) | Empty ⇒ fail-closed |
| Jev spend / TypeSafe key | Owner env; not recorded here | Never print secrets |

Until provider + all three budgets are set in owner env, soak still fail-closes Supervisor to NO_TRADE. Setting them enables real LLM calls on paper only — does **not** arm live.

## 3. Operations (M4-adjacent)

| Decision | Status | Notes |
| --- | --- | --- |
| On-call / notification channel | **UNRESOLVED** | Alert dry-run only until owner wires a channel |
| Backup retention count | **UNAPPROVED** | Scripts exist; retention policy owner-owned |
| Emergency auto cancel/close | **UNAPPROVED** | ADR 0006 — block-new only until owner decides |

## 4. How to approve later

1. Fill SE-* values in [STRATEGY_EVALUATION.md](STRATEGY_EVALUATION.md) and bump its status.  
2. Set LLM budgets in owner env (never commit secrets).  
3. Name notification channel and retention in [OPERATIONS.md](OPERATIONS.md) §10.  
4. Re-run paper soak for the approved window; attach [PAPER_EVALUATION_REPORT.md](PAPER_EVALUATION_REPORT.md).  
5. Live arming remains a **separate** checklist ([OPERATIONS.md](OPERATIONS.md) §7).
