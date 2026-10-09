"""Official TypeSafe System One constants (verified in docs/API_CONTRACTS.md)."""

from __future__ import annotations

SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"

# Internal Aegis question keys for System One.
QUESTION_ALIGNMENT = "alignment"
QUESTION_EVIDENCE_ADEQUACY = "evidence_adequacy"
QUESTION_SUMMARY = "summary"

ALIGNMENT_OPTIONS = (
    "aligned_long",
    "aligned_short",
    "mixed",
    "insufficient",
)

# Score levels for evidence_adequacy (verified range: 2–10; Aegis uses 5).
EVIDENCE_ADEQUACY_LEVELS = 5

DEFAULT_JEV_MODEL = "jev-latest"

CONFIDENCE_NOTES = (
    "Jev confidence is distribution concentration, not calibrated probability of profit."
)
