"""AX-123 concurrency proof for durable revalidation idempotency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
)
from agentx.core.knowledge_assurance import (
    KnowledgeRevalidation,
    KnowledgeRevalidationOutcome,
    KnowledgeRevalidationRequest,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.knowledge_assurance_ledger import (
    DuplicateKnowledgeRevalidationError,
    KnowledgeAssuranceLedger,
)
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def test_concurrent_duplicate_completion_commits_exactly_once(tmp_path: Path) -> None:
    database = SQLiteDatabase((tmp_path / "agentx.sqlite3").resolve())
    store = KnowledgeStore(database)
    record = KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content="claim",
        created_at=_T0,
        status=KnowledgeStatus.UNVERIFIED,
    )
    store.insert(record)
    ledger = KnowledgeAssuranceLedger(
        journal=EventJournal(database),
        knowledge_store=store,
    )
    request = KnowledgeRevalidationRequest.create(
        knowledge_id=record.knowledge_id,
        source="verifier",
        requested_at=_T0,
    )
    ledger.request_revalidation(request)
    result = KnowledgeRevalidation(
        revalidation_id=request.revalidation_id,
        knowledge_id=record.knowledge_id,
        requested_at=request.requested_at,
        completed_at=_T0,
        source=request.source,
        outcome=KnowledgeRevalidationOutcome.SUCCESS,
        evidence=(
            EvidenceReference(
                kind=EvidenceKind.OBSERVATION,
                reference="proof",
                provenance=ProvenanceReference(
                    kind=ProvenanceKind.SYSTEM,
                    reference="verifier",
                ),
                observed_at=_T0,
            ),
        ),
    )

    def complete() -> str:
        try:
            ledger.record_revalidation(result)
        except DuplicateKnowledgeRevalidationError:
            return "duplicate"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _: complete(), range(2)))

    assert outcomes == ["committed", "duplicate"]
    assert ledger.list_revalidations(record.knowledge_id) == (result,)
