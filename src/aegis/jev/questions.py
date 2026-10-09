"""Build the internal Aegis System One question set."""

from __future__ import annotations

from typing import Any

from aegis.jev.constants import (
    ALIGNMENT_OPTIONS,
    EVIDENCE_ADEQUACY_LEVELS,
    QUESTION_ALIGNMENT,
    QUESTION_EVIDENCE_ADEQUACY,
    QUESTION_SUMMARY,
)


def build_systemone_questions() -> dict[str, Any]:
    """Return questions map for POST /v1/systemone (choice / score / noul)."""
    return {
        QUESTION_ALIGNMENT: {
            "type": "choice",
            "options": list(ALIGNMENT_OPTIONS),
        },
        QUESTION_EVIDENCE_ADEQUACY: {
            "type": "score",
            "levels": EVIDENCE_ADEQUACY_LEVELS,
        },
        QUESTION_SUMMARY: {
            "type": "noul",
        },
    }
