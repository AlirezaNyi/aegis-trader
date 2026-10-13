# Migration Status

Last updated: 2026-10-13. Nothing committed.

## Migrated

| Item | Target | Verified |
|---|---|---|
| AGENTS.md | `CLAUDE.md` (`@AGENTS.md`) | file created |
| 3 rules | `.claude/rules/` | frontmatter/paths parsed |
| 6 agents | `.claude/agents/` | frontmatter parsed; tool lists set |
| 4 skills | `.claude/skills/` | frontmatter parsed |
| Shell guard adapter | `.claude/hooks/guard_trading_shell_claude.py` + `_cursor_bridge.py` | 10/10 parity with Cursor script |
| Secret-read guard adapter | `.claude/hooks/guard_secret_reads_claude.py` | 8/8 parity + Grep/Glob/malformed deny |
| Hooks + deny backstop | `.claude/settings.json` | valid JSON; live `/hooks` check pending |

## Not applied (by decision)

D6 repo hygiene (`.dockerignore`, `.serena/`, `.gitignore`), D7 external MCP servers, D8 commit.

## Unverified

Live-session checks (VALIDATION_CHECKLIST §3) and interaction with the NotchBuddy user hook.

## Cutover checklist

- [x] Manual setup §1 files exist
- [ ] `/hooks` shows both guards in a new session
- [ ] VALIDATION_CHECKLIST §2–§4 all pass
- [ ] Decide on D6 / D7 / D8
- [ ] Use Claude Code for daily work. Cursor stays usable (`.cursor/` untouched)
- [ ] Rollback: remove `CLAUDE.md` and `.claude/`. **Keep `.cursor/hooks/`**, because the Claude adapters import it
