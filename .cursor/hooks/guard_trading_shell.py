#!/usr/bin/env python3
"""Gate risky trading/ops shell commands. Exit 0 with JSON permission decision."""

from __future__ import annotations

import json
import re
import sys

_DENY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\balembic\s+downgrade\b", re.IGNORECASE),
        "alembic downgrade requires explicit owner approval.",
    ),
    (
        re.compile(r"\bdocker\s+compose\s+down\b.*\s-v\b", re.IGNORECASE),
        "docker compose down -v destroys volumes; blocked without owner approval.",
    ),
    (
        re.compile(r"\bdocker-compose\s+down\b.*\s-v\b", re.IGNORECASE),
        "docker-compose down -v destroys volumes; blocked without owner approval.",
    ),
    (
        re.compile(r"account/withdraw", re.IGNORECASE),
        "Withdrawal endpoints are forbidden in Aegis.",
    ),
    (
        re.compile(
            r"(?:^|[\s;&|])(?:cat|less|more|head|tail|bat)\s+[^\n]*\.env(?:\.local)?(?:\s|$)",
            re.IGNORECASE,
        ),
        "Shell reads of .env are blocked. Use .env.example only.",
    ),
    (
        re.compile(r"(?:^|[\s;&|])(?:source|\.)\s+\.env(?:\.local)?(?:\s|$)", re.IGNORECASE),
        "Sourcing .env in agent shells is blocked.",
    ),
]

_ASK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"AEGIS_TRADING_MODE\s*=\s*live", re.IGNORECASE),
        "Command sets AEGIS_TRADING_MODE=live. Confirm before continuing.",
    ),
    (
        re.compile(r"AEGIS_LIVE_ARMED\s*=\s*true", re.IGNORECASE),
        "Command sets AEGIS_LIVE_ARMED=true. Confirm before continuing.",
    ),
]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({"permission": "deny", "user_message": "Invalid hook input JSON."}))
        return 0

    command = str(payload.get("command") or "")

    for pattern, message in _DENY_PATTERNS:
        if pattern.search(command):
            print(
                json.dumps(
                    {
                        "permission": "deny",
                        "user_message": message,
                        "agent_message": message,
                    }
                )
            )
            return 0

    for pattern, message in _ASK_PATTERNS:
        if pattern.search(command):
            print(
                json.dumps(
                    {
                        "permission": "ask",
                        "user_message": message,
                        "agent_message": message,
                    }
                )
            )
            return 0

    print(json.dumps({"permission": "allow"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
