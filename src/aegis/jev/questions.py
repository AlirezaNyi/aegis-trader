"""Build the internal Aegis System One question set.

Format per docs.typesafe.ai/primitives (checked 2026-10-13):
- choice: ``instructions`` + ``criteria`` map of option name -> description
- score: ``instructions`` + ``criteria`` ordered array of level descriptions (0-indexed)
- noul: ``instructions`` (+ optional ``criteria`` with ``true``/``false``); answer is P(yes)
"""

from __future__ import annotations

from typing import Any

from aegis.jev.constants import (
    ALIGNMENT_CRITERIA,
    EVIDENCE_ADEQUACY_CRITERIA,
    QUESTION_ALIGNMENT,
    QUESTION_EVIDENCE_ADEQUACY,
    QUESTION_LONG_ENTRY,
)


def build_systemone_questions() -> dict[str, Any]:
    """Return questions map for POST /v1/systemone (choice / score / noul)."""
    return {
        QUESTION_ALIGNMENT: {
            "type": "choice",
            "instructions": (
                "Given the analyst evidence for this instrument and timeframe, "
                "which statement best describes the direction the evidence points to?"
            ),
            "criteria": dict(ALIGNMENT_CRITERIA),
        },
        QUESTION_EVIDENCE_ADEQUACY: {
            "type": "score",
            "instructions": (
                "How complete, fresh, and internally consistent is this evidence "
                "for making a short-term trading judgement?"
            ),
            "criteria": list(EVIDENCE_ADEQUACY_CRITERIA),
        },
        QUESTION_LONG_ENTRY: {
            "type": "noul",
            "instructions": (
                "Does this evidence support opening a new long spot position "
                "on this instrument now?"
            ),
            "criteria": {
                "true": "The evidence is adequate and clearly favours upward movement.",
                "false": "The evidence is weak, mixed, stale, or favours downward movement.",
            },
        },
    }
