"""Official TypeSafe System One constants (verified in docs/API_CONTRACTS.md)."""

from __future__ import annotations

SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"

# Internal Aegis question keys for System One (ids are never shown to the model).
QUESTION_ALIGNMENT = "alignment"
QUESTION_EVIDENCE_ADEQUACY = "evidence_adequacy"
QUESTION_LONG_ENTRY = "long_entry_supported"

# Choice: option name -> description (docs.typesafe.ai/primitives/choice).
ALIGNMENT_CRITERIA: dict[str, str] = {
    "aligned_long": "Technical and quantitative evidence both point to upward price movement.",
    "aligned_short": "Technical and quantitative evidence both point to downward price movement.",
    "mixed": "The evidence points in different directions.",
    "insufficient": "There is too little or too poor evidence to judge direction.",
}
ALIGNMENT_OPTIONS = tuple(ALIGNMENT_CRITERIA)

# Score: ordered low -> high; level number = 0-based index
# (docs.typesafe.ai/primitives/score; API accepts 2-10 levels).
EVIDENCE_ADEQUACY_CRITERIA: tuple[str, ...] = (
    "No usable evidence.",
    "Weak evidence: few signals, mostly unavailable or conflicting.",
    "Moderate evidence: some aligned signals with notable gaps.",
    "Good evidence: most signals available and consistent.",
    "Strong evidence: complete, fresh, and consistent signals.",
)
EVIDENCE_ADEQUACY_LEVELS = len(EVIDENCE_ADEQUACY_CRITERIA)

DEFAULT_JEV_MODEL = "jev-latest"

CONFIDENCE_NOTES = (
    "Jev confidence is distribution concentration, not calibrated probability of profit."
)
