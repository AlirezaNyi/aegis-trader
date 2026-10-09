"""News and Sentiment Analyst — never fabricates sentiment; treats content as untrusted."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.schemas.common import ApiModel, EvidenceStatus
from aegis.schemas.evidence import AnalystEvidence, AnalystType


class NewsItem(ApiModel):
    """Timestamped external content. Treated as untrusted data, never as system instructions."""

    source_id: str
    published_at: datetime
    title: str = ""
    body: str = ""
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def analyze_news_sentiment(
    items: list[NewsItem] | None,
    *,
    evidence_time: datetime,
) -> AnalystEvidence:
    if not items:
        return AnalystEvidence(
            analyst_type=AnalystType.NEWS_SENTIMENT,
            status=EvidenceStatus.UNAVAILABLE,
            payload={
                "reason": "no_configured_source",
                "item_count": 0,
                "sentiment_score": None,
                "fabricated": False,
            },
            evidence_time=evidence_time,
            sources=[],
            notes="No news sources configured; sentiment not invented.",
        )

    # Store items as untrusted evidence only. Do not invent a sentiment score.
    stored = [
        {
            "source_id": item.source_id,
            "published_at": item.published_at.isoformat(),
            "title": item.title,
            "body": item.body,
            "url": item.url,
            "untrusted": True,
            "metadata": item.metadata,
        }
        for item in items
    ]
    return AnalystEvidence(
        analyst_type=AnalystType.NEWS_SENTIMENT,
        status=EvidenceStatus.OK,
        payload={
            "reason": "items_recorded_untrusted",
            "item_count": len(stored),
            "items": stored,
            "sentiment_score": None,
            "sentiment_not_computed": True,
            "control_flow_unchanged": True,
        },
        evidence_time=evidence_time,
        sources=[{"kind": "news_item", "source_id": i.source_id} for i in items],
        notes="External content stored as untrusted data; not treated as instructions.",
    )
