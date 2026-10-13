"""Decision audit store — persist evidence, Jev result, and proposal per cycle.

Fail-open like the paper journal: a database error is logged and counted, but
never changes a decision. Nothing here touches orders or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from sqlalchemy.orm import Session, sessionmaker

from aegis.db.session import create_engine_from_url
from aegis.evidence.persist import persist_evidence_package
from aegis.logging import get_logger
from aegis.schemas.evidence import EvidencePackage, JevResult
from aegis.schemas.proposal import TradeProposal
from aegis.supervisor.persist import persist_jev_result, persist_trade_proposal

logger = get_logger("aegis.pipeline.audit")


@dataclass
class DecisionAuditStore:
    database_url: str | None = None
    persisted_count: int = field(default=0, init=False)
    failed_count: int = field(default=0, init=False)
    last_error: str | None = field(default=None, init=False)
    _session_factory: sessionmaker[Session] | None = field(default=None, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    def __post_init__(self) -> None:
        url = self.database_url
        # Drop URL (may contain password) once the engine is bound.
        self.database_url = None
        if not url:
            return
        try:
            engine = create_engine_from_url(url)
            self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        except Exception as exc:  # noqa: BLE001 — fail open
            self.last_error = f"engine_init:{type(exc).__name__}"
            self._session_factory = None

    @property
    def enabled(self) -> bool:
        return self._session_factory is not None

    def record(
        self,
        package: EvidencePackage,
        jev_result: JevResult,
        proposal: TradeProposal,
    ) -> bool:
        """Persist one cycle's decision chain in a single transaction."""
        factory = self._session_factory
        if factory is None:
            return False
        try:
            with factory() as session, session.begin():
                persist_evidence_package(session, package, list(package.analyst_evidence))
                persist_jev_result(session, jev_result)
                persist_trade_proposal(session, proposal)
        except Exception as exc:  # noqa: BLE001 — fail open; decision already made
            with self._lock:
                self.failed_count += 1
                self.last_error = type(exc).__name__
            logger.warning(
                "decision_audit_persist_failed err=%s",
                type(exc).__name__,
                extra={"event": "decision_audit_persist_failed", "component": "audit"},
            )
            return False
        with self._lock:
            self.persisted_count += 1
        return True
