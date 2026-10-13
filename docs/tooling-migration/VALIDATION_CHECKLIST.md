# Validation Checklist

Local, non-production checks only. Never place real orders, never read `.env`, and never set live-arming variables.

## 1. Static (run from repo root)

```bash
python3 -m json.tool .claude/settings.json >/dev/null && echo settings-ok
.venv/bin/python -I -c "import yaml,re,pathlib
for p in pathlib.Path('.claude').rglob('*.md'):
    m=re.match(r'---\n(.*?)\n---\n',p.read_text(),re.S); print(p, yaml.safe_load(m.group(1)) if m else 'always-on')"
git status --short   # expect only new: CLAUDE.md, .claude/, docs/tooling-migration/
```

- [x] Agent/skill/rule frontmatter parses (2026-10-13)
- [x] `settings.json` valid (2026-10-13)

## 2. Hook parity (synthetic input, compare to the Cursor script)

```bash
c(){ python3 -I -c 'import json,sys;print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1" | python3 -I .claude/hooks/guard_trading_shell_claude.py; echo " [$?]"; }
c 'alembic downgrade -1'; c 'docker compose down -v'; c 'curl x/api/v1/account/withdraw'
c 'cat .env'; c 'source .env'; c 'AEGIS_LIVE_ARMED=true echo'; c 'AEGIS_TRADING_MODE=live x'
c 'cat .env.example'; c 'pytest -q'
echo 'not json' | python3 -I .claude/hooks/guard_trading_shell_claude.py

r(){ printf '{"tool_name":"Read","tool_input":{"file_path":"%s"}}' "$1" | python3 -I .claude/hooks/guard_secret_reads_claude.py; echo " [$?]"; }
r .env; r .env.local; r secrets/.env/x; r key.pem; r id.key; r credentials.json; r .env.example; r src/aegis/main.py
echo 'not json' | python3 -I .claude/hooks/guard_secret_reads_claude.py
```

Expected: deny for downgrade/`down -v`/withdraw/`cat .env`/`source .env`/every secret path/malformed input. Ask for the two live-arming variables. **Empty output** (no decision; normal prompts apply) for `.env.example`, `pytest`, and normal source files.

- [x] Shell guard: 9/9 commands + malformed match the Cursor script (2026-10-13)
- [x] Secret-read guard: 8/8 paths match the Cursor script; Grep/Glob/malformed → deny (2026-10-13)

## 3. Live session (new Claude Code session in repo)

- [ ] `/hooks` lists both PreToolUse hooks from project settings
- [ ] `/agents` lists the 6 project agents. `safety-reviewer`/`verifier` have no Edit/Write
- [ ] Skills `agent-orchestration`, `phase-change`, `bug-fix`, `safety-change` appear in the skill list
- [ ] `/memory` shows `CLAUDE.md` (with AGENTS.md imported) and `aegis-safety` rule
- [ ] Ask Claude to edit a file in `src/aegis/` and confirm the `aegis-python` rule loads
- [ ] Ask Claude to read `.env`: **blocked**, and no content is shown
- [ ] Ask Claude to run `alembic downgrade -1`: **blocked** (with NotchBuddy hook active)
- [ ] Ask Claude to run `AEGIS_LIVE_ARMED=true echo test`: **permission prompt**
- [ ] Hand a trivial task to `verifier` and check it returns the 8-section report

## 4. Application unaffected

```bash
pytest -q && ruff check src tests && mypy src   # same results as before the migration
```

## Cannot verify locally

Orbit/Context7 authentication. How Cursor's glob-attached rules compare to Claude's `paths` loading in edge cases.
