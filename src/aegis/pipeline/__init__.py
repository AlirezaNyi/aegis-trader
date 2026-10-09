"""Explicit paper decision pipeline (ADR 0002 — no agent tool loop)."""

from __future__ import annotations

from aegis.pipeline.cycle import PaperCycleDeps, PaperCycleResult, run_paper_cycle

__all__ = ["PaperCycleDeps", "PaperCycleResult", "run_paper_cycle"]
