#!/usr/bin/env python3
"""Deny agent reads of secret files. Exit 0 with JSON permission decision."""

from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath, PureWindowsPath


def _basename(path: str) -> str:
    normalized = path.replace("\\", "/")
    return PurePosixPath(normalized).name


def _should_deny(file_path: str) -> str | None:
    name = _basename(file_path)
    lower = name.lower()

    if lower in {".env", ".env.local"}:
        return "Reading .env files is blocked. Use .env.example for placeholders only."

    if lower == ".env.example":
        return None

    if lower.endswith(".pem") or lower.endswith(".key"):
        return "Reading private key material is blocked."

    if lower == "credentials.json":
        return "Reading credentials.json is blocked."

    # Also catch paths like secrets/.env.production (exact name variants)
    posix = PurePosixPath(file_path.replace("\\", "/"))
    win = PureWindowsPath(file_path)
    for part in (*posix.parts, *win.parts):
        p = part.lower()
        if p in {".env", ".env.local"}:
            return "Reading .env files is blocked. Use .env.example for placeholders only."

    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({"permission": "deny", "user_message": "Invalid hook input JSON."}))
        return 0

    file_path = payload.get("file_path") or ""
    reason = _should_deny(str(file_path))
    if reason:
        print(
            json.dumps(
                {
                    "permission": "deny",
                    "user_message": reason,
                }
            )
        )
        return 0

    print(json.dumps({"permission": "allow"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
