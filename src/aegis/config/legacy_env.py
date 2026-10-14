"""Detect pre-``env_prefix`` env names that are now silently ignored.

Settings read ``AEGIS_<FIELD>`` only. A leftover unprefixed safety key such as
``KILL_SWITCH=true`` would otherwise be ignored and fail open, so those refuse
startup; other leftover names only warn. Only key NAMES are inspected, never values.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

# Names whose silent loss would weaken a safety gate.
LEGACY_SAFETY_KEYS = frozenset({"KILL_SWITCH", "LIVE_ARMED", "TRADING_MODE"})


class LegacyEnvError(RuntimeError):
    """Unprefixed safety env keys present; rename them to AEGIS_<NAME>."""


def _dotenv_key_names(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return names
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        names.add(key.upper())
    return names


def find_legacy_keys(
    field_names: Iterable[str],
    *,
    environ: Mapping[str, str] | None = None,
    env_file: str | Path | None = ".env",
) -> tuple[list[str], list[str]]:
    """Return (safety_keys, other_keys) present without the AEGIS_ prefix."""
    env = os.environ if environ is None else environ
    present = {k.upper() for k in env}
    if env_file is not None:
        present |= _dotenv_key_names(Path(env_file))
    legacy = {name.upper() for name in field_names}
    # Names that legitimately stay unprefixed (explicit aliases).
    legacy -= {"DATABASE_URL", "TOOBIT_API_KEY", "TOOBIT_API_SECRET", "TYPESAFE_API_KEY"}
    found = sorted(legacy & present)
    safety = [k for k in found if k in LEGACY_SAFETY_KEYS]
    other = [k for k in found if k not in LEGACY_SAFETY_KEYS]
    return safety, other
