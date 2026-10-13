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