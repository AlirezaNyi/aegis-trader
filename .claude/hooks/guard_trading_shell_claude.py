#!/usr/bin/env python3
"""Claude Code PreToolUse adapter for .cursor/hooks/guard_trading_shell.py (Bash)."""

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
                        "permissionDecisionReason": "Trading shell guard failed to load; denied.",
                    }
                }
            )
        )
        return 0

    try:
        payload = json.load(sys.stdin)
        command = str((payload.get("tool_input") or {}).get("command") or "")
        guard = load_cursor_guard("guard_trading_shell.py")
        for pattern, message in guard._DENY_PATTERNS:
            if pattern.search(command):
                emit("deny", message)
                return 0
        for pattern, message in guard._ASK_PATTERNS:
            if pattern.search(command):
                emit("ask", message)
                return 0
    except Exception:  # noqa: BLE001 - fail closed, matching Cursor failClosed: true
        emit("deny", "Trading shell guard error or invalid hook input; denied.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
