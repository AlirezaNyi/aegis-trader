"""Explicit bounded orchestrator for specialized analysts (no LangGraph).

Analysts are declared once in an :class:`AnalystRegistry` as :class:`AnalystSpec`
entries (analyst type -> async factory + declared inputs). ``run_analysts`` iterates
the registry in registration order with bounded concurrency, a per-analyst timeout,
and optional per-type overrides. Adding an analyst only requires registering a spec.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aegis.analysts.analytical_risk import analyze_analytical_risk
from aegis.analysts.news import NewsItem, analyze_news_sentiment
from aegis.analysts.quantitative import analyze_quantitative
from aegis.analysts.strategy_researcher import StrategyHypothesis, analyze_strategy_researcher
from aegis.analysts.technical import analyze_technical
from aegis.logging import get_logger
from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import AnalystEvidence, AnalystType
from aegis.schemas.features import FeatureSnapshot
from aegis.schemas.market import Candle

DEFAULT_ANALYST_TIMEOUT_SECONDS = 2.0
DEFAULT_ANALYST_CONCURRENCY = 5

logger = get_logger("aegis.analysts.runner")


class AnalystInput(StrEnum):
    """Inputs an analyst may declare. ``evidence_time`` is always passed."""

    SNAPSHOT = "snapshot"
    CANDLES = "candles"
    NEWS = "news_items"
    HYPOTHESIS = "hypothesis"
    SPREAD = "spread"
    FUNDING = "funding_rate"


AnalystFactory = Callable[..., Awaitable[AnalystEvidence]]
"""Async factory called as ``factory(evidence_time=..., **declared_inputs)``."""


@dataclass(frozen=True)
class AnalystSpec:
    """One registered analyst: its type, async factory, and declared inputs.

    The factory receives only the declared inputs (keyword names are the
    :class:`AnalystInput` values) plus ``evidence_time``.
    """

    analyst_type: AnalystType
    factory: AnalystFactory
    inputs: frozenset[AnalystInput]


@dataclass(frozen=True)
class AnalystInputs:
    """All inputs available to analysts for one run."""

    snapshot: FeatureSnapshot
    candles: Sequence[Candle]
    news_items: list[NewsItem] | None
    hypothesis: StrategyHypothesis | None
    spread: Decimal | None
    funding_rate: Decimal | None

    def select(self, declared: frozenset[AnalystInput]) -> dict[str, Any]:
        # Iterate the enum (not the set) so keyword order is deterministic.
        return {item.value: getattr(self, item.value) for item in AnalystInput if item in declared}


class AnalystRegistry:
    """Ordered mapping AnalystType -> AnalystSpec. Order defines output order."""

    def __init__(self, specs: Sequence[AnalystSpec] = ()) -> None:
        self._specs: dict[AnalystType, AnalystSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: AnalystSpec, *, replace: bool = False) -> None:
        if not isinstance(spec.analyst_type, AnalystType):
            raise TypeError(f"analyst_type must be AnalystType, got {type(spec.analyst_type)!r}")
        unknown = [i for i in spec.inputs if not isinstance(i, AnalystInput)]
        if unknown:
            raise ValueError(f"unknown analyst inputs declared: {unknown!r}")
        if spec.analyst_type in self._specs and not replace:
            raise ValueError(f"analyst already registered: {spec.analyst_type.value}")
        self._specs[spec.analyst_type] = spec

    def unregister(self, analyst_type: AnalystType) -> None:
        del self._specs[analyst_type]

    def get(self, analyst_type: AnalystType) -> AnalystSpec | None:
        return self._specs.get(analyst_type)

    def types(self) -> list[AnalystType]:
        return list(self._specs)

    def copy(self) -> AnalystRegistry:
        return AnalystRegistry(list(self._specs.values()))

    def __iter__(self) -> Iterator[AnalystSpec]:
        return iter(list(self._specs.values()))

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, analyst_type: object) -> bool:
        return analyst_type in self._specs


def threaded(fn: Callable[..., AnalystEvidence]) -> AnalystFactory:
    """Wrap a synchronous (CPU-bound, pure) analyst as an async factory run in a thread."""

    async def _factory(**kwargs: Any) -> AnalystEvidence:
        return await asyncio.to_thread(fn, **kwargs)

    return _factory


def _technical(*, snapshot: FeatureSnapshot, evidence_time: datetime) -> AnalystEvidence:
    return analyze_technical(snapshot, evidence_time=evidence_time)


def _quantitative(
    *, snapshot: FeatureSnapshot, candles: Sequence[Candle], evidence_time: datetime
) -> AnalystEvidence:
    return analyze_quantitative(snapshot, candles, evidence_time=evidence_time)


def _news(*, news_items: list[NewsItem] | None, evidence_time: datetime) -> AnalystEvidence:
    return analyze_news_sentiment(news_items, evidence_time=evidence_time)


def _analytical_risk(
    *,
    snapshot: FeatureSnapshot,
    spread: Decimal | None,
    funding_rate: Decimal | None,
    evidence_time: datetime,
) -> AnalystEvidence:
    return analyze_analytical_risk(
        snapshot, evidence_time=evidence_time, spread=spread, funding_rate=funding_rate
    )


def _strategy_researcher(
    *,
    snapshot: FeatureSnapshot,
    hypothesis: StrategyHypothesis | None,
    evidence_time: datetime,
) -> AnalystEvidence:
    return analyze_strategy_researcher(snapshot, hypothesis, evidence_time=evidence_time)


def build_default_registry() -> AnalystRegistry:
    """The five Phase 3 analysts, in canonical output order."""
    return AnalystRegistry(
        [
            AnalystSpec(
                AnalystType.TECHNICAL,
                threaded(_technical),
                frozenset({AnalystInput.SNAPSHOT}),
            ),
            AnalystSpec(
                AnalystType.QUANTITATIVE,
                threaded(_quantitative),
                frozenset({AnalystInput.SNAPSHOT, AnalystInput.CANDLES}),
            ),
            AnalystSpec(
                AnalystType.NEWS_SENTIMENT,
                threaded(_news),
                frozenset({AnalystInput.NEWS}),
            ),
            AnalystSpec(
                AnalystType.ANALYTICAL_RISK,
                threaded(_analytical_risk),
                frozenset({AnalystInput.SNAPSHOT, AnalystInput.SPREAD, AnalystInput.FUNDING}),
            ),
            AnalystSpec(
                AnalystType.STRATEGY_RESEARCHER,
                threaded(_strategy_researcher),
                frozenset({AnalystInput.SNAPSHOT, AnalystInput.HYPOTHESIS}),
            ),
        ]
    )


DEFAULT_ANALYST_REGISTRY = build_default_registry()


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


async def _run_guarded(
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
            logger.warning(
                "analyst_timeout analyst_type=%s timeout_seconds=%s",
                name.value,
                timeout_seconds,
                extra={"event": "analyst_timeout", "component": "analysts.runner"},
            )
            return _error_evidence(name, evidence_time, "timeout")
        except Exception as exc:  # noqa: BLE001 — deliberate boundary: one analyst must not crash the cycle; maps to ERROR evidence
            logger.warning(
                "analyst_error analyst_type=%s exc_class=%s",
                name.value,
                type(exc).__name__,
                extra={"event": "analyst_error", "component": "analysts.runner"},
            )
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
    registry: AnalystRegistry | None = None,
) -> list[AnalystEvidence]:
    """Run every registered analyst with bounded concurrency and per-analyst timeouts.

    Output order is registry order. A timeout or error on one analyst yields ERROR for
    that analyst only; others continue. ``overrides`` replace the factory for a
    registered type (zero-arg coroutine factory); overrides for unregistered types are
    ignored. ``registry`` defaults to :data:`DEFAULT_ANALYST_REGISTRY`.
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))
    overrides = overrides or {}
    active = registry if registry is not None else DEFAULT_ANALYST_REGISTRY
    inputs = AnalystInputs(
        snapshot=snapshot,
        candles=candles,
        news_items=news_items,
        hypothesis=hypothesis,
        spread=spread,
        funding_rate=funding_rate,
    )

    def _bind(spec: AnalystSpec) -> Callable[[], Awaitable[AnalystEvidence]]:
        override = overrides.get(spec.analyst_type)
        if override is not None:
            return override
        kwargs = inputs.select(spec.inputs)

        def _call() -> Awaitable[AnalystEvidence]:
            return spec.factory(evidence_time=evidence_time, **kwargs)

        return _call

    jobs = [
        _run_guarded(
            spec.analyst_type,
            _bind(spec),
            evidence_time=evidence_time,
            semaphore=semaphore,
            timeout_seconds=timeout_seconds,
        )
        for spec in active
    ]
    results = await asyncio.gather(*jobs)
    return list(results)


def run_analysts_sync(
    snapshot: FeatureSnapshot,
    candles: Sequence[Candle],
    **kwargs: Any,
) -> list[AnalystEvidence]:
    return asyncio.run(run_analysts(snapshot, candles, **kwargs))
