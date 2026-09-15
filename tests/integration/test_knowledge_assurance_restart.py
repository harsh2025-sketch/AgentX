"""Restart integration proof for AX-120..123."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from agentx.core.knowledge_integrity import KnowledgeContradiction, KnowledgeSupersession
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.knowledge_assurance_ledger import KnowledgeAssuranceLedger
from agentx.infrastructure.knowledge_relationships import (
    KnowledgeRelationshipQuery,
    SupersessionDirection,
)
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _record(content: str) -> KnowledgeRecord:
    return KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content=content,
        created_at=_T0,
        status=KnowledgeStatus.UNVERIFIED,
    )


def test_relationship_and_revalidation_history_survive_fresh_store_instances(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "agentx.sqlite3").resolve()
    database = SQLiteDatabase(path)
    store = KnowledgeStore(database)
    old = _record("old")
    current = _record("current")
    conflict = _record("not current")
    for record in (old, current, conflict):
        store.insert(record)
    store.apply_supersession(
        KnowledgeSupersession(
            replacement_knowledge_id=current.knowledge_id,
            superseded_knowledge_id=old.knowledge_id,
        )
    )
    store.record_contradiction(
        KnowledgeContradiction(
            first_knowledge_id=current.knowledge_id,
            second_knowledge_id=conflict.knowledge_id,
        )
    )

    ledger = KnowledgeAssuranceLedger(
        journal=EventJournal(database),
        knowledge_store=store,
    )
    request = KnowledgeRevalidationRequest.create(
        knowledge_id=current.knowledge_id,
        source="restart-test-verifier",
        requested_at=_T0,
    )
    result = KnowledgeRevalidation(
        revalidation_id=request.revalidation_id,
        knowledge_id=current.knowledge_id,
        requested_at=request.requested_at,
        completed_at=_T0 + timedelta(minutes=1),
        source=request.source,
        outcome=KnowledgeRevalidationOutcome.SUCCESS,
        evidence=(
            EvidenceReference(
                kind=EvidenceKind.OBSERVATION,
                reference="restart-observation",
                provenance=ProvenanceReference(
                    kind=ProvenanceKind.SYSTEM,
                    reference="restart-test-verifier",
                ),
                observed_at=_T0,
            ),
        ),
    )
    ledger.request_revalidation(request)
    ledger.record_revalidation(result)

    restarted_database = SQLiteDatabase(path)
    restarted_store = KnowledgeStore(restarted_database)
    restarted_ledger = KnowledgeAssuranceLedger(
        journal=EventJournal(restarted_database),
        knowledge_store=restarted_store,
    )
    relationships = KnowledgeRelationshipQuery(restarted_store)

    assert relationships.traverse_supersession(
        old.knowledge_id,
        direction=SupersessionDirection.HISTORY_TO_REPLACEMENT,
    )[-1].knowledge_id == current.knowledge_id
    assert relationships.contradiction_views(current.knowledge_id)[0].relation == (
        KnowledgeContradiction(
            first_knowledge_id=current.knowledge_id,
            second_knowledge_id=conflict.knowledge_id,
        )
    )
    assert restarted_ledger.list_revalidations(current.knowledge_id) == (result,)
    assert restarted_ledger.assurance_for(current.knowledge_id).verification_count == 1
