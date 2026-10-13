# Claude Code Migration Plan

Additive migration: `.cursor/` and `AGENTS.md` are untouched, so Cursor keeps working. No application code, dependencies, Compose files, schemas or migrations change.

## Target layout

```
CLAUDE.md                         @AGENTS.md import + Claude-specific deltas
.claude/
  settings.json                   PreToolUse hooks + permissions.deny backstop
  hooks/_cursor_bridge.py         loads .cursor/hooks/*.py, emits PreToolUse JSON
  hooks/guard_trading_shell_claude.py
  hooks/guard_secret_reads_claude.py
  rules/aegis-{safety,python,persistence}.md
  agents/{market-data,research-pipeline,decision-plane,execution-ledger,verifier,safety-reviewer}.md
  skills/{agent-orchestration,phase-change,bug-fix,safety-change}/SKILL.md
docs/tooling-migration/           these reports
```

## Dependency map

```
.claude/settings.json ──PreToolUse──▶ .claude/hooks/*_claude.py ──imports──▶ .cursor/hooks/*.py (patterns)
CLAUDE.md ──@import──▶ AGENTS.md ──refs──▶ docs/, Phases/, .claude/{agents,skills}
skills/agent-orchestration ◀── phase-change, bug-fix, safety-change
agents (implementers) ──hand off──▶ verifier ──▶ safety-reviewer
```

## Stages

| Stage | Content | State |
|---|---|---|
| A Discovery | Inventory, matrix, dependency map, inaccessible sources | Done |
| B Backup | Additive only. Optional: `git diff > ~/aegis-wip-<date>.patch` before committing WIP | Recommended, user-run |
| C Draft conversion | Rules, agents, skills, CLAUDE.md, hook adapters | Done (except the 2 files below) |
| D Controlled implementation | `.claude/settings.json` and `guard_secret_reads_claude.py` were **blocked by the auto-mode safety classifier** (self-modification). They are given in [MANUAL_SETUP.md](MANUAL_SETUP.md) for the user to review and create | Done (files created after user approval) |
| E Validation | [VALIDATION_CHECKLIST.md](VALIDATION_CHECKLIST.md) | Partly done |
| F Cutover | Checklist in [MIGRATION_STATUS.md](MIGRATION_STATUS.md) | After E |

## Decisions applied

D1 import AGENTS.md · D2 commit only when asked + BEM format · D3/D4 hooks + deny backstop (active) · D5 tool-list read-only, Bash kept · D6/D7 not applied · D8 not committed.

## Maintenance

When editing a rule, agent, skill or guard pattern, update both `.cursor/` and `.claude/` copies. Guard *patterns* only need editing in `.cursor/hooks/*.py`, because the Claude adapters import them.
