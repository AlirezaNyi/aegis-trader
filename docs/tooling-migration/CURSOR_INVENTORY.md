# Cursor Configuration Inventory

Discovery date: 2026-10-13. Method: read-only inspection of the repo, `~/.cursor/`, Cursor user settings (key names only), `~/.claude/`, and `~/.claude.json` (server names only). `.env`, Cursor secret-store values and MCP auth headers were deliberately **not** read; no secret values appear here.

Status key: DIRECTLY PORTABLE · ADAPTABLE · MANUAL SETUP REQUIRED · CURSOR-SPECIFIC · UNKNOWN. Compatibility details and behavioral differences: [COMPATIBILITY_MATRIX.md](COMPATIBILITY_MATRIX.md).

## Repository state (verified)

- Branch `main`, HEAD `0045484`. The working tree is **dirty**: 15 modified tracked files (app code, tests, docs, `docker-compose.yml`) and the untracked `.serena/`. These are in-progress user changes; the migration must not touch them.
- There is no project Claude config yet: no `CLAUDE.md`, `.claude/`, or `.mcp.json`. The project auto-memory directory is empty.
- There is no `.cursorrules`, `.cursorignore`, `.cursorindexingignore`, `.vscode/`, `.github/` CI, pre-commit or Makefile (all verified absent).
- Project phase: Phases 1–8 are implemented, and the system is PAPER-TRADING READY. Live trading is not activated. Still open: SE-* thresholds are UNAPPROVED, account inventory reconcile is Unverified (audit M1), and the LLM provider and budget are owner-set ([docs/OWNER_PENDING_DECISIONS.md](docs/OWNER_PENDING_DECISIONS.md)).

## Inventory (Stage A) — 52 items

| # | Item | Scope / tracked | Purpose | Sensitive | Status | Claude Code target |
|---|---|---|---|---|---|---|
| **Instructions & rules (8)** |
| 1 | `AGENTS.md` | project / tracked | Always-on guide: phases, 12 invariants, commands, delegation, orchestration | no | DIRECTLY PORTABLE | `CLAUDE.md` imports `@AGENTS.md` (single source; Cursor keeps reading it) |
| 2 | `.cursor/rules/aegis-safety.mdc` (alwaysApply) | project / tracked | Live gates, credential plane, forbidden patterns, Risk authority | no | ADAPTABLE | `.claude/rules/aegis-safety.md` with **no `paths:`** (always loaded) |
| 3 | `.cursor/rules/aegis-python.mdc` globs `src/**/*.py,tests/**/*.py` | project / tracked | Py3.12, `__future__`, Pydantic v2, Decimal, test/lint policy | no | ADAPTABLE | `.claude/rules/aegis-python.md`, `paths: ["src/**/*.py","tests/**/*.py"]` |
| 4 | `.cursor/rules/aegis-persistence.mdc` globs `alembic/**/*.py,src/aegis/db/**/*.py` | project / tracked | Alembic/SQLAlchemy rules, no downgrade, ledger_kind, append-only audit | no | ADAPTABLE | `.claude/rules/aegis-persistence.md`, same globs |
| 5 | `~/.cursor/rules/git-commit-workflow.mdc` (alwaysApply) | **user** / n/a | Commit after each task, BEM-style messages | no | ADAPTABLE, but **conflicts** with AGENTS.md ("commit only when asked") | Decision D2 |
| 6 | Cursor User Rules (`aicontext.personalContext`) | user / n/a | Global user rules | n/a | Verified **empty**, nothing to migrate | — |
| 7 | `README.md`, `docs/*` (18), `docs/DECISIONS/` ADR 0001–0007, `Phases/` (10), module `LIMITATIONS.md` | project / tracked | Product/architecture source of truth | no | DIRECTLY PORTABLE (referenced, not copied) | Referenced from CLAUDE.md/rules |
| 8 | `pyproject.toml` tool config (pytest asyncio auto, ruff py312/100, mypy strict) | project / tracked | Lint/test conventions | no | DIRECTLY PORTABLE | Unchanged |
| **Subagents (6)**, all `model: inherit`, "never launch other subagents", 8-section return contract |
| 9 | `.cursor/agents/market-data.md` | project / tracked | Public Toobit MD owner | no | ADAPTABLE | `.claude/agents/market-data.md` |
| 10 | `.cursor/agents/research-pipeline.md` | ″ | Features/analysts/evidence owner | no | ADAPTABLE | `.claude/agents/research-pipeline.md` |
| 11 | `.cursor/agents/decision-plane.md` | ″ | Jev/Supervisor/Risk/guards (Ph 4–5) | no | ADAPTABLE | `.claude/agents/decision-plane.md` |
| 12 | `.cursor/agents/execution-ledger.md` | ″ | Orders/paper/backtest/reconcile/exchange (Ph 6–7) | no | ADAPTABLE | `.claude/agents/execution-ledger.md` |
| 13 | `.cursor/agents/verifier.md` | ″ | Runs pytest/ruff/mypy, no edits | no | ADAPTABLE | `tools:` without Edit/Write |
| 14 | `.cursor/agents/safety-reviewer.md` (`readonly: true`) | ″ | Read-only safety audit | no | ADAPTABLE, semantic gap: no `readonly` flag in Claude | `tools: Read, Grep, Glob, Bash` + `disallowedTools: Edit, Write, NotebookEdit`; Bash can still mutate, see D5 |
| **Skills (4 project + 2 user groups)** |
| 15 | `.cursor/skills/agent-orchestration/SKILL.md` | project / tracked | Classify → risk → delegate → verify → escalate | no | ADAPTABLE | `.claude/skills/agent-orchestration/SKILL.md` |
| 16 | `.cursor/skills/phase-change/SKILL.md` | ″ | Phase work procedure | no | ADAPTABLE | `.claude/skills/phase-change/` |
| 17 | `.cursor/skills/bug-fix/SKILL.md` | ″ | Bug procedure, max 2 loops | no | ADAPTABLE | `.claude/skills/bug-fix/` |
| 18 | `.cursor/skills/safety-change/SKILL.md` | ″ | HIGH-risk procedure | no | ADAPTABLE | `.claude/skills/safety-change/` |
| 19 | `~/.cursor/skills/orbit/` | user | Orbit MCP coordination manual | maybe (refs private MCP) | MANUAL SETUP / out of scope | Not migrated unless D7 |
| 20 | `~/.cursor/skills-cursor/*` (24 Cursor-bundled skills: create-rule, review-bugbot, …) | user / Cursor-managed | Cursor product features | no | CURSOR-SPECIFIC | Closest: built-in `/code-review`, `/security-review`, `/init`, `/loop`, `skill-creator` |
| **Hooks (3 files, 2 behaviors)** |
| 21 | `.cursor/hooks.json` (`version:1`, `failClosed:true`) | project / tracked | Registers both guards | no | ADAPTABLE | `.claude/settings.json` `hooks.PreToolUse` |
| 22 | `.cursor/hooks/guard_secret_reads.py` (`beforeReadFile`) | ″ | **Deny** reads of `.env`, `.env.local`, any path segment `.env`, `*.pem`, `*.key`, `credentials.json`; allows `.env.example`; bad JSON → deny | no | ADAPTABLE, see timing/fail notes below | PreToolUse matcher `Read\|Grep\|Glob\|NotebookRead` + permissions.deny backstop |
| 23 | `.cursor/hooks/guard_trading_shell.py` (`beforeShellExecution`) | ″ | **Deny** `alembic downgrade`, `docker compose down … -v`, `account/withdraw`, `cat/less/head/tail/bat … .env`, `source .env`. **Ask** on `AEGIS_TRADING_MODE=live`, `AEGIS_LIVE_ARMED=true` | no | ADAPTABLE | PreToolUse matcher `Bash`, `permissionDecision: deny\|ask` |
| **MCP & integrations (11)**: none are project-scoped in Cursor |
| 24 | `serena` (`~/.cursor/mcp.json`, stdio `serena start-mcp-server --context=ide`) | user | Symbol-level code nav/edit | no | **Already present** in `~/.claude.json` (`--context=claude-code`) | No action; `.serena/project.yml` shared |
| 25 | `orbit_mcp` (user, remote URL with embedded ID + auth headers) | user | Cross-agent memory | **YES**: private URL and auth headers | MANUAL SETUP | Do not copy. Optional user-scope `claude mcp add` (D7) |
| 26 | `console-ninja` (user, `node ~/.console-ninja/mcp/`) | user | JS runtime logs | no | Not used by Aegis (Python) | Skip |
| 27–30 | Cursor plugin MCPs: Vercel, Prisma, Supabase, Postman (OAuth tokens in Cursor secret store) | user | Unrelated SaaS | **YES** (tokens) | Not used by Aegis | Skip; never copy tokens |
| 31 | DBCode (VS Code extension MCP), referenced in execution-ledger as "optional, read-only" | user ext | DB browsing | maybe | CURSOR-SPECIFIC | Use `psql`/SQLAlchemy via Bash in read-only mode; keep the "read-only unless owner approves" text |
| 32 | `cursor-ide-browser`, `cursor-origin` | Cursor built-in | Browser/IDE | no | CURSOR-SPECIFIC | Claude built-ins / VS Code extension |
| 33 | **Context7**, referenced by 4 agents, **configured in neither tool** | — | Library docs | no | UNKNOWN / MANUAL | Agents keep "if available" wording; optional setup (D7) |
| 34 | WebFetch (agents: official Toobit/Jev docs) | built-in | Docs fetch | no | DIRECTLY PORTABLE | Claude `WebFetch` tool |
| **Context, ignore, editor (7)** |
| 35 | `.gitignore` (ignores `.env`, `.env.local`, `.vscode/`, caches) | project / tracked | VCS exclusion | no | DIRECTLY PORTABLE | Claude respects it for search |
| 36 | `.dockerignore` (excludes `.cursor`, `.planning`, `agent-transcripts`) | project / tracked | Image context | no | ADAPTABLE | Add `.claude` and `CLAUDE.md`? (D6, touches build config) |
| 37 | `.serena/` (untracked; `project.yml`, own `.gitignore` for cache/local) | project / untracked | Serena project config | no | DIRECTLY PORTABLE | Track or ignore (D6) |
| 38 | Cursor user settings: `python.*/cursorpyright.analysis.extraPaths` | user | Editor import paths | no | CURSOR-SPECIFIC | Not needed (`pip install -e`) |
| 39 | Cursor workspace DB rules/MCP snapshots (`state.vscdb`) | user | Cursor UI cache of the above | maybe | CURSOR-SPECIFIC | Not inspected beyond key names |
| 40 | Cursor agent transcripts (10) under `~/.cursor/projects/…Ageis/` | user | Past chat history | maybe | Cannot migrate | Project state already captured in docs/AGENTS.md |
| 41 | Commands: venv/pip, `uvicorn …`, `docker compose up --build`, `alembic upgrade head`, `pytest`, `ruff check src tests`, `mypy src`, `scripts/*.sh/.py` | project | Dev/ops workflow | no | DIRECTLY PORTABLE | Listed via AGENTS.md import |
| **Existing Claude Code config (affects migration) (11)** |
| 42 | `~/.claude/CLAUDE.md` (graphify trigger) | user | — | no | keep | — |
| 43 | `~/.claude/settings.json` hooks: NotchBuddy on every event (incl. PreToolUse `--ask`), GSD SessionStart/PostToolUse, jcode | user | Notifications/GSD | no | coexist | Check they don't mask project hook decisions (Stage E) |
| 44 | Plugin `superpowers`, GSD agents/commands (11 agents, `/gsd:*`) | user | Generic workflows | no | coexist | Names don't collide with project agents |
| 45 | User MCP `MCP_DOCKER` (currently failing to connect) | user | Docker MCP gateway | no | n/a | Unrelated |
| 46–52 | Owner-pending state: SE-* thresholds, audit M1, LLM provider/budget, live not armed, risk policy v1.0, `ledger_kind`, paper soak flag default off | project docs | Ongoing context | no | DIRECTLY PORTABLE | Via AGENTS.md import |

**Inaccessible or not inspected:** Cursor cloud and team settings, Cursor secret store values (deliberately not read), `.env` (deliberately not read), Cursor's internal model and tool-permission UI settings, and the contents of the Orbit remote server.


## Dependencies

- Hooks: `python3` on PATH (stdlib only); `$CLAUDE_PROJECT_DIR` set by Claude Code. Claude adapters import `.cursor/hooks/*.py`, so **do not delete `.cursor/hooks/`** while Claude hooks are active.
- Serena: `serena` CLI installed (user-level, both tools).
- Agents: WebFetch (built-in); Context7 optional and unconfigured; DBCode is Cursor/VS Code only.
- No project environment variables are read by tooling config; live-gate variables (`AEGIS_TRADING_MODE`, `AEGIS_LIVE_ARMED`, `AEGIS_KILL_SWITCH`) are only *matched* by the shell guard.
