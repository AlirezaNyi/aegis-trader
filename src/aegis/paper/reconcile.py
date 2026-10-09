"""Paper ledger restart recovery and equality reconcile."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from aegis.paper.ledger import PaperLedger
from aegis.paper.persist import load_paper_ledger, persist_paper_ledger


@dataclass(frozen=True)
class PaperReconcileResult:
    ok: bool
    expected_checksum: str
    loaded_checksum: str
    detail: str


def reconcile_paper_ledgers(
    expected: PaperLedger,
    loaded: PaperLedger,
) -> PaperReconcileResult:
    """Simple equality reconcile via deterministic checksum of balances + positions."""
    expected_cs = expected.checksum()
    loaded_cs = loaded.checksum()
    if expected_cs == loaded_cs:
        return PaperReconcileResult(
            ok=True,
            expected_checksum=expected_cs,
            loaded_checksum=loaded_cs,
            detail="paper balances and positions match",
        )
    return PaperReconcileResult(
        ok=False,
        expected_checksum=expected_cs,
        loaded_checksum=loaded_cs,
        detail="paper checksum mismatch after reload",
    )


def persist_and_reload(
    session: Session,
    ledger: PaperLedger,
    *,
    quote_asset: str | None = None,
) -> tuple[PaperLedger, PaperReconcileResult]:
    """Persist paper state, reload, and reconcile (restart recovery helper)."""
    persist_paper_ledger(session, ledger)
    session.flush()
    loaded = load_paper_ledger(
        session,
        quote_asset=quote_asset or ledger.quote_asset,
    )
    result = reconcile_paper_ledgers(ledger, loaded)
    return loaded, result


def recover_from_snapshot(snapshot: dict[str, object]) -> tuple[PaperLedger, PaperReconcileResult]:
    """In-memory restart path: snapshot → reload → checksum equality."""
    original_checksum = str(snapshot.get("checksum", ""))
    loaded = PaperLedger.from_snapshot(snapshot)
    expected = PaperLedger.from_snapshot(snapshot)
    # Rebuild expected from same snapshot so checksum compares reload fidelity
    result = reconcile_paper_ledgers(expected, loaded)
    if original_checksum and loaded.checksum() != original_checksum:
        return loaded, PaperReconcileResult(
            ok=False,
            expected_checksum=original_checksum,
            loaded_checksum=loaded.checksum(),
            detail="snapshot checksum drift on reload",
        )
    return loaded, result
