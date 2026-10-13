# Manual Setup

No secrets belong in this file or in any `.claude/` file.

## 1. Activate the safety hooks (required)

Claude Code's auto-mode safety classifier blocked the agent from creating the two files below, because they change Claude's own permission behavior. Review them, then create them yourself (or explicitly tell Claude to create them). Until then, **the Cursor guards are not enforced in Claude Code**. Only the instructions in CLAUDE.md and the rules apply.

### `.claude/settings.json`

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "deny": [
      "Read(**/.env)",
      "Read(**/.env.local)",
      "Read(**/.env/**)",
      "Read(**/*.pem)",
      "Read(**/*.key)",
      "Read(**/credentials.json)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Grep|Glob",
        "hooks": [
          { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard_secret_reads_claude.py\"" }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard_trading_shell_claude.py\"" }
        ]
      }
    ]
  }
}
```

There are no `allow` rules, no `defaultMode` change and no bypass. `.env.example` stays readable.

### `.claude/hooks/guard_secret_reads_claude.py`

Mirror of the existing `guard_trading_shell_claude.py`. It loads `_should_deny` from `.cursor/hooks/guard_secret_reads.py`:

```python
#!/usr/bin/env python3
"""Claude Code PreToolUse adapter for .cursor/hooks/guard_secret_reads.py (Read|Grep|Glob)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    try:
        from _cursor_bridge import emit, load_cursor_guard
    except Exception:  # noqa: BLE001 - fail closed
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Secret-read guard failed to load; denied.",
                    }
                }
            )
        )
        return 0

    try:
        payload = json.load(sys.stdin)
        tool_input = payload.get("tool_input") or {}
        guard = load_cursor_guard("guard_secret_reads.py")
        for key in ("file_path", "notebook_path", "path", "pattern", "glob"):
            candidate = tool_input.get(key)
            if not candidate:
                continue
            reason = guard._should_deny(str(candidate))
            if reason:
                emit("deny", reason)
                return 0
    except Exception:  # noqa: BLE001 - fail closed, matching Cursor failClosed: true
        emit("deny", "Secret-read guard error or invalid hook input; denied.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

After creating them, start a new Claude Code session in the repo and run `/hooks`. Claude Code may ask you to review and approve the new project hooks.

## 2. Hook interaction with user-level hooks (check)

`~/.claude/settings.json` runs a NotchBuddy `PreToolUse` hook (including `--ask`) on every tool. Confirm that a project-hook **deny** still blocks when both run (see VALIDATION_CHECKLIST §3).

## 3. Optional MCP servers (user scope, not project)

| Server | Action | Notes |
|---|---|---|
| serena | none | Already configured in `~/.claude.json` |
| Orbit | `claude mcp add --scope user …` with your own URL/headers | The URL and headers are credentials; never put them in `.mcp.json` or the repo |
| Context7 | optional `claude mcp add` | Referenced by agents as "only if configured" |
| Vercel/Prisma/Supabase/Postman/console-ninja | skip | Not used by Aegis |

## 4. Optional repo hygiene (D6, not applied)

- Add `.claude` and `CLAUDE.md` to `.dockerignore` (build-context change).
- Decide whether to track `.serena/project.yml` (it already ignores its own cache/local files).
- Add `.claude/settings.local.json` to `.gitignore` if you create one.

## 5. Not migratable

Cursor chat transcripts, Cursor secret store and OAuth tokens, Cursor cloud/team settings, Cursor editor `extraPaths`.
