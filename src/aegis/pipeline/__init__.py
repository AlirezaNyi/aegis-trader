"""Explicit paper decision pipeline (ADR 0002 — no agent tool loop)."""

from __future__ import annotations

from aegis.pipeline.cycle import PaperCycleDeps, PaperCycleResult, run_paper_cycle
from aegis.pipeline.soak import PaperSoakRunner, SoakPollOutcome, run_soak_loop

__all__ = [
    "PaperCycleDeps",
    "PaperCycleResult",
    "PaperSoakRunner",
    "SoakPollOutcome",
    "run_paper_cycle",
    "run_soak_loop",
]
