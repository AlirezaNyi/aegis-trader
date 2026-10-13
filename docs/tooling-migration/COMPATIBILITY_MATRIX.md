# Compatibility Matrix

Baseline: Claude Code 2.1.56. Full item list: [CURSOR_INVENTORY.md](CURSOR_INVENTORY.md).

| Cursor item | Claude Code target | Status | Behavioral differences |
|---|---|---|---|
| `AGENTS.md` | `CLAUDE.md` → `@AGENTS.md` import | DIRECTLY PORTABLE | None. One file is shared by both tools. "main Cursor Agent" / `explore` are mapped in CLAUDE.md |
| `rules/aegis-safety.mdc` (`alwaysApply: true`) | `.claude/rules/aegis-safety.md` (no `paths`) | ADAPTABLE | Body is verbatim. Loaded at session start, like alwaysApply |
| `rules/aegis-python.mdc` (`globs: src/**/*.py,tests/**/*.py`) | `.claude/rules/aegis-python.md` (`paths:` list) | ADAPTABLE | Same globs. Claude loads it when it works with matching files; Cursor attaches it when matching files are in context. Close, not identical |
| `rules/aegis-persistence.mdc` (`globs: alembic/**/*.py,src/aegis/db/**/*.py`) | `.claude/rules/aegis-persistence.md` | ADAPTABLE | Same as above |
| `~/.cursor/rules/git-commit-workflow.mdc` (user, auto-commit) | CLAUDE.md "Commits" line (format only) | ADAPTABLE (partial, by decision D2) | **Auto-commit after each task is intentionally NOT carried over**. The project rule "commit only when asked" wins. The BEM message format is kept |
| `agents/*.md` (6, `model: inherit`) | `.claude/agents/*.md` | ADAPTABLE | Added an explicit `tools:` list. Specialists have no `Agent` tool, so "never launch subagents" is now *enforced*, not just instructed |
| `agents/safety-reviewer.md` `readonly: true` | `tools: Read, Grep, Glob, Bash` + `disallowedTools: Edit, Write, NotebookEdit` | ADAPTABLE (gap) | Claude has no readonly flag. Bash remains (needed for `git diff`), so read-only for shell is by instruction only (D5) |
| `agents/verifier.md` | same tool restriction | ADAPTABLE | Cursor relied on instructions; Claude also enforces no Edit/Write |
| `skills/*/SKILL.md` (4) | `.claude/skills/*/SKILL.md` | ADAPTABLE | Same frontmatter (`name`, `description`). Paths are rewritten to `.claude/skills/`, and `explore` becomes the `Explore` subagent |
| `hooks.json` → `guard_trading_shell.py` (`beforeShellExecution`, failClosed) | PreToolUse `Bash` → `.claude/hooks/guard_trading_shell_claude.py` | ADAPTABLE | Same patterns (imported). Cursor `allow` maps to *no decision*, so normal Claude permission prompts still apply. A crash inside the adapter becomes deny. **If `python3` itself is missing, Claude treats the hook error as non-blocking (fails open)** |
| `hooks.json` → `guard_secret_reads.py` (`beforeReadFile`, failClosed) | PreToolUse `Read\|Grep\|Glob` → `.claude/hooks/guard_secret_reads_claude.py` + `permissions.deny` | ADAPTABLE | Also checks Grep/Glob `path`/`pattern`/`glob`. The `permissions.deny` rules are a fail-closed backstop that is independent of the hook (slightly stronger than Cursor) |
| MCP `serena` (user) | Already in `~/.claude.json` (user) | DIRECTLY PORTABLE | `--context=claude-code` vs `--context=ide`; same `.serena/project.yml` |
| MCP `orbit_mcp` (user, private URL + auth headers) | none (optional user-scope add) | MANUAL SETUP REQUIRED | Not copied, because it holds credentials |
| MCP `console-ninja`, Vercel, Prisma, Supabase, Postman | none | Not used by Aegis | Skipped. OAuth tokens never copied |
| DBCode (extension MCP) | read-only `psql` via Bash | CURSOR-SPECIFIC | Agent text updated |
| `cursor-ide-browser`, `cursor-origin`, `~/.cursor/skills-cursor/*` | Built-ins (`/code-review`, `/security-review`, `/init`, `/loop`, WebFetch) | CURSOR-SPECIFIC | Closest alternatives only |
| Context7 (referenced, unconfigured) | none | UNKNOWN | Agents say "only if configured" |
| `.gitignore`, `.dockerignore`, `.serena/` | unchanged | DIRECTLY PORTABLE | `.dockerignore` doesn't exclude `.claude/` / `CLAUDE.md` yet (D6, not applied) |
| Cursor editor settings (`extraPaths`), workspace DB, chat transcripts | none | CURSOR-SPECIFIC | Not needed / not migratable |
