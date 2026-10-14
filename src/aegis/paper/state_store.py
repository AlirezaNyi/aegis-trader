"""Persist / restore the paper ledger + equity-period tracker across restarts.

Semantics (fail closed):

* ``database_url=None`` (and no backend): persistence disabled — in-memory mode for
  tests and backtests; ``restore`` is a no-op and ``save`` returns False.
* Enabled and the load fails, the stored state is malformed / inconsistent, or paper
  ledger rows exist without a stored tracker state: the tracker is marked
  ``state_lost`` (snapshot losses are None ⇒ Risk rejects). It never silently starts
  fresh, and ``save`` refuses to overwrite the stored state until a later
  ``restore`` succeeds.
* A failed ``save`` marks the tracker unavailable (``state_persist_failed``) so new
  trades are rejected until a save succeeds again.

Ledger rows use the existing ``orders`` / ``fills`` / ``positions`` /
``paper_balances`` tables (``ledger_kind='paper'``). Tracker state, protective
levels and the ledger checksum go to ``risk_period_state.state`` JSONB, written in the
same transaction. The database URL is not retained after the engine is bound; errors
are logged by exception class name only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aegis.db.models import PaperBalanceRow, RiskPeriodStateRow
from aegis.db.session import create_engine_from_url
from aegis.logging import get_logger
from aegis.paper.equity import PaperEquityTracker
from aegis.paper.ledger import PaperLedger
from aegis.paper.persist import load_paper_ledger, persist_paper_ledger
from aegis.paper.protection import ProtectiveLevels
from aegis.schemas.common import LedgerKind

logger = get_logger("aegis.paper.state_store")

STATE_VERSION = 1

REASON_STORE_UNAVAILABLE = "state_store_unavailable"
REASON_LOAD_FAILED = "state_load_failed"
REASON_STATE_INVALID = "state_invalid"
REASON_STATE_MISSING = "state_missing_for_existing_ledger"
REASON_PERSIST_FAILED = "state_persist_failed"


@dataclass(frozen=True)
class StoredPaperState:
    """Raw read result from a backend."""

    ledger: PaperLedger
    has_ledger_rows: bool
    state: Mapping[str, Any] | None


class PaperStateBackend(Protocol):
    def read(self) -> StoredPaperState: ...

    def write(self, ledger: PaperLedger, state: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class PaperStateLoad:
    """Outcome of ``PaperStateStore.restore``."""

    ok: bool
    fresh: bool
    persisted: bool
    detail: str


class SqlPaperStateBackend:
    """SQLAlchemy backend: ledger rows + ``risk_period_state`` in one transaction."""

    def __init__(self, session_factory: sessionmaker[Session], *, quote_asset: str) -> None:
        self._session_factory = session_factory
        self._quote_asset = quote_asset

    def read(self) -> StoredPaperState:
        with self._session_factory() as session:
            row = session.scalars(
                select(RiskPeriodStateRow).where(
                    RiskPeriodStateRow.ledger_kind == LedgerKind.PAPER.value
                )
            ).one_or_none()
            ledger = load_paper_ledger(session, quote_asset=self._quote_asset)
            balance_rows = session.scalar(
                select(func.count())
                .select_from(PaperBalanceRow)
                .where(PaperBalanceRow.ledger_kind == LedgerKind.PAPER.value)
            )
            has_rows = bool(balance_rows) or bool(
                ledger.orders or ledger.fills or ledger.positions
            )
            state = None if row is None else dict(row.state)
            return StoredPaperState(ledger=ledger, has_ledger_rows=has_rows, state=state)

    def write(self, ledger: PaperLedger, state: dict[str, Any]) -> None:
        with self._session_factory.begin() as session:
            persist_paper_ledger(session, ledger)
            row = session.scalars(
                select(RiskPeriodStateRow).where(
                    RiskPeriodStateRow.ledger_kind == LedgerKind.PAPER.value
                )
            ).one_or_none()
            if row is None:
                row = RiskPeriodStateRow(id=uuid4(), ledger_kind=LedgerKind.PAPER.value)
                session.add(row)
            row.state = state
            row.updated_at = datetime.now(tz=UTC)


def build_state_payload(ledger: PaperLedger, tracker: PaperEquityTracker) -> dict[str, Any]:
    """JSONB payload for ``risk_period_state.state`` (no secrets, Decimal as str)."""
    return {
        "version": STATE_VERSION,
        "ledger_kind": LedgerKind.PAPER.value,
        "quote_asset": ledger.quote_asset,
        "ledger_checksum": ledger.checksum(),
        "tracker": tracker.to_state(),
        "protections": {k: v.to_json() for k, v in sorted(ledger.protections.items())},
    }


def _parse_payload(
    stored: StoredPaperState,
) -> tuple[dict[str, ProtectiveLevels], Mapping[str, Any]]:
    """Validate a stored payload against the loaded ledger. Raises ValueError."""
    state = stored.state
    if state is None:
        raise ValueError("no stored state")
    if state.get("version") != STATE_VERSION:
        raise ValueError(f"unsupported state version {state.get('version')!r}")
    if state.get("ledger_kind") != LedgerKind.PAPER.value:
        raise ValueError("stored state is not ledger_kind=paper")
    if state.get("quote_asset") != stored.ledger.quote_asset:
        raise ValueError("stored quote asset does not match the ledger")
    if state.get("ledger_checksum") != stored.ledger.checksum():
        raise ValueError("ledger checksum mismatch between rows and stored state")
    tracker_state = state.get("tracker")
    if not isinstance(tracker_state, Mapping):
        raise ValueError("tracker state missing")
    raw_protections = state.get("protections") or {}
    if not isinstance(raw_protections, Mapping):
        raise ValueError("protections must be an object")
    protections: dict[str, ProtectiveLevels] = {}
    for key, raw in raw_protections.items():
        position = stored.ledger.positions.get(str(key))
        if position is None or position.quantity <= 0:
            raise ValueError("protective levels recorded for a position that is not open")
        protections[str(key)] = ProtectiveLevels.from_json(dict(raw))
    return protections, tracker_state


def _signature(ledger: PaperLedger, payload: dict[str, Any]) -> str:
    body = json.dumps(
        {"ledger": ledger.to_snapshot(), "state": payload},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class PaperStateStore:
    """Load / save paper ledger + equity tracker state (paper ledger only)."""

    def __init__(
        self,
        database_url: str | None,
        *,
        quote_asset: str = "USDT",
        backend: PaperStateBackend | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.last_error: str | None = None
        self._lock = RLock()
        self._quote_asset = quote_asset
        self._backend: PaperStateBackend | None = None
        self._restored = False
        self._last_signature: str | None = None
        self._enabled = bool(database_url) or backend is not None or session_factory is not None
        if backend is not None:
            self._backend = backend
        elif session_factory is not None:
            self._backend = SqlPaperStateBackend(session_factory, quote_asset=quote_asset)
        elif database_url:
            # The URL may contain a password; it is not retained after engine bind.
            try:
                engine = create_engine_from_url(database_url)
                self._backend = SqlPaperStateBackend(
                    sessionmaker(bind=engine, expire_on_commit=False),
                    quote_asset=quote_asset,
                )
            except Exception as exc:  # noqa: BLE001 — DB boundary: driver/URL errors vary (ImportError, SQLAlchemyError, ValueError); logged by class and reported as unavailable state
                self._record_error("engine_init", exc)
                self._backend = None

    @property
    def enabled(self) -> bool:
        """True when persistence was requested (even if the backend is broken)."""
        return self._enabled

    @property
    def restored(self) -> bool:
        """True once ``restore`` succeeded (or found a fresh store); saves allowed."""
        return self._restored

    def _record_error(self, op: str, exc: BaseException) -> None:
        self.last_error = f"{op}:{type(exc).__name__}"
        logger.warning(
            "paper_state_%s_failed err=%s",
            op,
            type(exc).__name__,
            extra={"event": f"paper_state_{op}_failed", "component": "paper_state_store"},
        )

    def _fail_restore(
        self, tracker: PaperEquityTracker, reason: str, detail: str
    ) -> PaperStateLoad:
        self._restored = False
        tracker.mark_unavailable(reason, state_lost=True)
        logger.error(
            "paper_state_restore_failed reason=%s",
            reason,
            extra={"event": "paper_state_restore_failed", "component": "paper_state_store"},
        )
        return PaperStateLoad(ok=False, fresh=False, persisted=self._enabled, detail=detail)

    def restore(self, ledger: PaperLedger, tracker: PaperEquityTracker) -> PaperStateLoad:
        """Load persisted state into ``ledger`` / ``tracker`` in place.

        Call before the first cycle. May be called again after a failure (e.g. once
        Postgres is reachable). A fresh store keeps the caller's initial ledger,
        resets the tracker, and immediately saves that initial state.
        """
        with self._lock:
            return self._restore(ledger, tracker)

    def _restore(self, ledger: PaperLedger, tracker: PaperEquityTracker) -> PaperStateLoad:
        if not self._enabled:
            self._restored = True
            return PaperStateLoad(
                ok=True, fresh=True, persisted=False, detail="persistence disabled (in-memory)"
            )
        if ledger.quote_asset != self._quote_asset:
            return self._fail_restore(
                tracker, REASON_STATE_INVALID, "ledger quote asset does not match the store"
            )
        if self._backend is None:
            return self._fail_restore(
                tracker, REASON_STORE_UNAVAILABLE, "state store engine unavailable"
            )
        try:
            stored = self._backend.read()
        except Exception as exc:  # noqa: BLE001 — DB fail-closed boundary: any read failure ⇒ restore refused (logged by class name only)
            self._record_error("load", exc)
            return self._fail_restore(tracker, REASON_LOAD_FAILED, "state load failed")

        if stored.state is None:
            if stored.has_ledger_rows:
                return self._fail_restore(
                    tracker,
                    REASON_STATE_MISSING,
                    "paper ledger rows exist without risk_period_state",
                )
            # Genuinely fresh store: keep the caller's initial ledger, fresh tracker.
            tracker.reset()
            self._restored = True
            self._last_signature = None
            saved = self._save(ledger, tracker, force=True)
            return PaperStateLoad(
                ok=True,
                fresh=True,
                persisted=True,
                detail="fresh state saved" if saved else "fresh state; initial save failed",
            )

        try:
            protections, tracker_state = _parse_payload(stored)
            restored_tracker = PaperEquityTracker.from_state(tracker_state)
        except Exception as exc:  # noqa: BLE001 — parse fail-closed boundary: malformed / inconsistent state ⇒ restore refused (logged by class name only)
            self._record_error("parse", exc)
            return self._fail_restore(tracker, REASON_STATE_INVALID, "stored state invalid")

        stored.ledger.protections = protections
        ledger.replace_contents(stored.ledger)
        tracker.restore_state(restored_tracker.to_state())
        self._restored = True
        self._last_signature = _signature(ledger, build_state_payload(ledger, tracker))
        return PaperStateLoad(ok=True, fresh=False, persisted=True, detail="state restored")

    def save(
        self,
        ledger: PaperLedger,
        tracker: PaperEquityTracker,
        *,
        force: bool = False,
    ) -> bool:
        """Persist ledger + tracker atomically. True when stored (or unchanged)."""
        with self._lock:
            return self._save(ledger, tracker, force=force)

    def _save(
        self,
        ledger: PaperLedger,
        tracker: PaperEquityTracker,
        *,
        force: bool,
    ) -> bool:
        if not self._enabled:
            return False
        if not self._restored or self._backend is None or tracker.state_lost:
            # Never overwrite state we could not read.
            self.last_error = "save:refused_not_restored"
            return False
        payload = build_state_payload(ledger, tracker)
        signature = _signature(ledger, payload)
        if not force and signature == self._last_signature:
            return True
        try:
            self._backend.write(ledger, payload)
        except Exception as exc:  # noqa: BLE001 — DB fail-closed boundary: any write failure ⇒ tracker marked unavailable (logged by class name only)
            self._record_error("save", exc)
            tracker.mark_unavailable(REASON_PERSIST_FAILED)
            return False
        self._last_signature = signature
        if tracker.unavailable_reason == REASON_PERSIST_FAILED:
            tracker.clear_unavailable()
        return True
