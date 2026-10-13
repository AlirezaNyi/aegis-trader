# Aegis — Claude Code

@AGENTS.md

## Claude Code specifics

`AGENTS.md` above is the shared source of truth for Cursor and Claude Code. Where it says "main Cursor Agent", read "main Claude Code session"; where it says `explore`, use the `Explore` subagent.

- **Subagents:** `.claude/agents/` (mirrors `.cursor/agents/`). Specialists have no `Agent` tool, so they cannot launch subagents. `verifier` and `safety-reviewer` have no edit tools.
- **Skills:** `.claude/skills/` — `agent-orchestration`, `phase-change`, `bug-fix`, `safety-change`.
- **Rules:** `.claude/rules/` — `aegis-safety` (always on), `aegis-python` (`src/`, `tests/`), `aegis-persistence` (`alembic/`, `src/aegis/db/`).
- **Guards:** `.claude/hooks/` adapters reuse the deny/ask patterns in `.cursor/hooks/*.py`. If a guard denies or asks, do not work around it; report to the user.
- **Commits:** only when the user asks. Message format: `<block>: <description>` (optionally `<block>__<element>` / `<block>--<modifier>`), lowercase, imperative, specific.
- **Migration notes:** `docs/tooling-migration/`. Keep `.cursor/` and `.claude/` copies in sync when changing rules, agents, or skills.
