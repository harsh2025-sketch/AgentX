"""AX-123 failure-injection proof: failed append cannot partially update assurance."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

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
from agentx.infrastructure.event_journal import EventJournal, EventJournalStorageError
from agentx.infrastructure.knowledge_assurance_ledger import KnowledgeAssuranceLedger
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def test_failed_result_append_leaves_only_request_and_no_confidence_increment(
    tmp_path: Path,
) -> None:
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
    journal = EventJournal(database)
    ledger = KnowledgeAssuranceLedger(journal=journal, knowledge_store=store)
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

    with (
        patch.object(
            EventJournal,
            "append",
            side_effect=EventJournalStorageError("injected durable write failure"),
        ),
        pytest.raises(EventJournalStorageError),
    ):
        ledger.record_revalidation(result)

    restarted = KnowledgeAssuranceLedger(
        journal=EventJournal(database),
        knowledge_store=KnowledgeStore(database),
    )
    assert restarted.list_requests(record.knowledge_id) == (request,)
    assert restarted.list_revalidations(record.knowledge_id) == ()
    assert restarted.assurance_for(record.knowledge_id).verification_count == 0
