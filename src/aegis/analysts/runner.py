"""Explicit bounded orchestrator for specialized analysts (no LangGraph)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from aegis.analysts.analytical_risk import analyze_analytical_risk
from aegis.analysts.news import NewsItem, analyze_news_sentiment
from aegis.analysts.quantitative import analyze_quantitative
from aegis.analysts.strategy_researcher import StrategyHypothesis, analyze_strategy_researcher
from aegis.analysts.technical import analyze_technical
from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import AnalystEvidence, AnalystType
from aegis.schemas.features import FeatureSnapshot
from aegis.schemas.market import Candle

DEFAULT_ANALYST_TIMEOUT_SECONDS = 2.0
DEFAULT_ANALYST_CONCURRENCY = 5


def _error_evidence(
    analyst_type: AnalystType,
    evidence_time: datetime,
    detail: str,
) -> AnalystEvidence:
    return AnalystEvidence(
        analyst_type=analyst_type,
        status=EvidenceStatus.ERROR,
        payload={"reason": detail},
        evidence_time=evidence_time,
        sources=[],
        notes=detail,
    )


async def _run_one(
    name: AnalystType,
    fn: Callable[[], AnalystEvidence],
    *,
    evidence_time: datetime,
    semaphore: asyncio.Semaphore,
    timeout_seconds: float,
) -> AnalystEvidence:
    async with semaphore:

        def _call() -> AnalystEvidence:
            return fn()

        try:
            return await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout_seconds)
        except TimeoutError:
            return _error_evidence(name, evidence_time, "timeout")
        except Exception as exc:  # noqa: BLE001 — isolate analyst failures
            return _error_evidence(name, evidence_time, f"error: {exc}")


async def _run_awaitable_one(
    name: AnalystType,
    coro_factory: Callable[[], Awaitable[AnalystEvidence]],
    *,
    evidence_time: datetime,
    semaphore: asyncio.Semaphore,
    timeout_seconds: float,
) -> AnalystEvidence:
    async with semaphore:
        try:
            return await asyncio.wait_for(coro_factory(), timeout=timeout_seconds)
        except TimeoutError:
            return _error_evidence(name, evidence_time, "timeout")
        except Exception as exc:  # noqa: BLE001
            return _error_evidence(name, evidence_time, f"error: {exc}")


async def run_analysts(
    snapshot: FeatureSnapshot,
    candles: Sequence[Candle],
    *,
    evidence_time: datetime,
    news_items: list[NewsItem] | None = None,
    hypothesis: StrategyHypothesis | None = None,
    spread: Decimal | None = None,
    funding_rate: Decimal | None = None,
    timeout_seconds: float = DEFAULT_ANALYST_TIMEOUT_SECONDS,
    concurrency: int = DEFAULT_ANALYST_CONCURRENCY,
    overrides: dict[AnalystType, Callable[[], Awaitable[AnalystEvidence]]] | None = None,
) -> list[AnalystEvidence]:
    """Run all five analysts with bounded concurrency and per-analyst timeouts.

    A timeout or error on one analyst yields ERROR for that analyst only; others continue.
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))
    overrides = overrides or {}

    jobs: list[Awaitable[AnalystEvidence]] = []

    if AnalystType.TECHNICAL in overrides:
        jobs.append(
            _run_awaitable_one(
                AnalystType.TECHNICAL,
                overrides[AnalystType.TECHNICAL],
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )
    else:
        jobs.append(
            _run_one(
                AnalystType.TECHNICAL,
                lambda: analyze_technical(snapshot, evidence_time=evidence_time),
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )

    if AnalystType.QUANTITATIVE in overrides:
        jobs.append(
            _run_awaitable_one(
                AnalystType.QUANTITATIVE,
                overrides[AnalystType.QUANTITATIVE],
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )
    else:
        jobs.append(
            _run_one(
                AnalystType.QUANTITATIVE,
                lambda: analyze_quantitative(snapshot, candles, evidence_time=evidence_time),
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )

    if AnalystType.NEWS_SENTIMENT in overrides:
        jobs.append(
            _run_awaitable_one(
                AnalystType.NEWS_SENTIMENT,
                overrides[AnalystType.NEWS_SENTIMENT],
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )
    else:
        jobs.append(
            _run_one(
                AnalystType.NEWS_SENTIMENT,
                lambda: analyze_news_sentiment(news_items, evidence_time=evidence_time),
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )

    if AnalystType.ANALYTICAL_RISK in overrides:
        jobs.append(
            _run_awaitable_one(
                AnalystType.ANALYTICAL_RISK,
                overrides[AnalystType.ANALYTICAL_RISK],
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )
    else:
        jobs.append(
            _run_one(
                AnalystType.ANALYTICAL_RISK,
                lambda: analyze_analytical_risk(
                    snapshot,
                    evidence_time=evidence_time,
                    spread=spread,
                    funding_rate=funding_rate,
                ),
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )

    if AnalystType.STRATEGY_RESEARCHER in overrides:
        jobs.append(
            _run_awaitable_one(
                AnalystType.STRATEGY_RESEARCHER,
                overrides[AnalystType.STRATEGY_RESEARCHER],
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )
    else:
        jobs.append(
            _run_one(
                AnalystType.STRATEGY_RESEARCHER,
                lambda: analyze_strategy_researcher(
                    snapshot, hypothesis, evidence_time=evidence_time
                ),
                evidence_time=evidence_time,
                semaphore=semaphore,
                timeout_seconds=timeout_seconds,
            )
        )

    results = await asyncio.gather(*jobs)
    return list(results)


def run_analysts_sync(
    snapshot: FeatureSnapshot,
    candles: Sequence[Candle],
    **kwargs: Any,
) -> list[AnalystEvidence]:
    return asyncio.run(run_analysts(snapshot, candles, **kwargs))
