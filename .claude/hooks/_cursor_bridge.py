"""Shared helpers for Claude Code PreToolUse adapters around the Cursor guard scripts.

The deny/ask patterns live in `.cursor/hooks/*.py` (single source of truth for both tools).
Claude Code treats a hook crash (non-zero exit other than 2) as non-blocking, so every
adapter must catch all errors and emit an explicit deny to preserve Cursor's failClosed.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

_CURSOR_HOOKS = Path(__file__).resolve().parents[2] / ".cursor" / "hooks"


def load_cursor_guard(filename: str) -> ModuleType:
    path = _CURSOR_HOOKS / filename
    spec = importlib.util.spec_from_file_location(f"_aegis_cursor_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def emit(decision: str, reason: str) -> None:
    """Emit a PreToolUse decision. Only 'deny' or 'ask'; never 'allow' (would skip prompts)."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
