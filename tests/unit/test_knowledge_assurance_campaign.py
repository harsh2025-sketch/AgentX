"""AX-120..123 completion tests for knowledge relationships and assurance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    KnowledgeAssuranceMetadata,
    KnowledgeConfidenceState,
    KnowledgeRevalidation,
    KnowledgeRevalidationOutcome,
    KnowledgeRevalidationRequest,
)
from agentx.core.knowledge_integrity import KnowledgeContradiction, KnowledgeSupersession
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.knowledge_assurance_ledger import (
    DuplicateKnowledgeRevalidationError,
    KnowledgeAssuranceHistoryTooLargeError,
    KnowledgeAssuranceLedger,
    KnowledgeRevalidationNotRequestedError,
)
from agentx.infrastructure.knowledge_relationships import (
    KnowledgeRelationshipQuery,
    SupersessionDirection,
)
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=10)
_T2 = _T1 + timedelta(minutes=10)
_T3 = _T2 + timedelta(minutes=10)


def _db(tmp_path: Path) -> SQLiteDatabase:
    return SQLiteDatabase((tmp_path / "agentx.sqlite3").resolve())


def _record(
    content: str,
    *,
    status: KnowledgeStatus = KnowledgeStatus.UNVERIFIED,
    source: str = "source-a",
) -> KnowledgeRecord:
    return KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content=content,
        created_at=_T0,
        status=status,
        provenance=ProvenanceReference(kind=ProvenanceKind.DOCUMENT, reference=source),
        verified_at=_T1 if status is KnowledgeStatus.VERIFIED else None,
    )


def _evidence(reference: str = "artifact-1") -> EvidenceReference:
    return EvidenceReference(
        kind=EvidenceKind.ARTIFACT,
        reference=reference,
        provenance=ProvenanceReference(
            kind=ProvenanceKind.REPOSITORY,
            reference=f"repo:{reference}",
        ),
        observed_at=_T1,
    )


def _ledger(tmp_path: Path) -> tuple[KnowledgeStore, KnowledgeAssuranceLedger]:
    database = _db(tmp_path)
    store = KnowledgeStore(database)
    return store, KnowledgeAssuranceLedger(
        journal=EventJournal(database),
        knowledge_store=store,
    )


def _request_and_result(
    record: KnowledgeRecord,
    *,
    outcome: KnowledgeRevalidationOutcome,
    related: KnowledgeId | None = None,
    suffix: str = "one",
) -> tuple[KnowledgeRevalidationRequest, KnowledgeRevalidation]:
    request = KnowledgeRevalidationRequest.create(
        knowledge_id=record.knowledge_id,
        source="independent-verifier",
        requested_at=_T1,
    )
    result = KnowledgeRevalidation(
        revalidation_id=request.revalidation_id,
        knowledge_id=record.knowledge_id,
        requested_at=request.requested_at,
        completed_at=_T2,
        source=request.source,
        outcome=outcome,
        evidence=(_evidence(suffix),),
        related_knowledge_id=related,
        environment_reference="env:test",
    )
    return request, result


def test_contradiction_query_preserves_both_claims_and_provenance(tmp_path: Path) -> None:
    store, _ = _ledger(tmp_path)
    verified = _record("port open", status=KnowledgeStatus.VERIFIED, source="scan-a")
    unverified = _record("port closed", source="scan-b")
    store.insert(verified)
    store.insert(unverified)
    relation = KnowledgeContradiction(
        first_knowledge_id=verified.knowledge_id,
        second_knowledge_id=unverified.knowledge_id,
    )
    store.record_contradiction(relation)

    views = KnowledgeRelationshipQuery(store).contradiction_views(verified.knowledge_id)

    assert len(views) == 1
    assert {views[0].first.content, views[0].second.content} == {"port open", "port closed"}
    assert views[0].first.provenance is not None
    assert views[0].second.provenance is not None
    assert {views[0].first.provenance.reference, views[0].second.provenance.reference} == {
        "scan-a",
        "scan-b",
    }
    assert {views[0].first.status, views[0].second.status} == {
        KnowledgeStatus.VERIFIED,
        KnowledgeStatus.UNVERIFIED,
    }


def test_relationship_queries_survive_restart_and_traverse_supersession_chain(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    store = KnowledgeStore(database)
    first = _record("A")
    second = _record("B")
    third = _record("C")
    for record in (first, second, third):
        store.insert(record)
    store.apply_supersession(
        KnowledgeSupersession(
            replacement_knowledge_id=second.knowledge_id,
            superseded_knowledge_id=first.knowledge_id,
        )
    )
    store.apply_supersession(
        KnowledgeSupersession(
            replacement_knowledge_id=third.knowledge_id,
            superseded_knowledge_id=second.knowledge_id,
        )
    )

    restarted = KnowledgeRelationshipQuery(KnowledgeStore(_db(tmp_path)))

    forward = restarted.traverse_supersession(
        first.knowledge_id,
        direction=SupersessionDirection.HISTORY_TO_REPLACEMENT,
    )
    backward = restarted.traverse_supersession(
        third.knowledge_id,
        direction=SupersessionDirection.REPLACEMENT_TO_HISTORY,
    )
    assert [item.content for item in forward] == ["A", "B", "C"]
    assert [item.content for item in backward] == ["C", "B", "A"]


def test_successful_revalidation_is_durable_and_deterministic(tmp_path: Path) -> None:
    store, ledger = _ledger(tmp_path)
    record = _record("claim")
    store.insert(record)
    seed = KnowledgeAssuranceMetadata(
        knowledge_id=record.knowledge_id,
        source_observed_at=_T0,
        fresh_until=_T3,
        evidence=(_evidence("research"),),
    )
    ledger.record_seed(seed, recorded_at=_T0)

    request, result = _request_and_result(
        record,
        outcome=KnowledgeRevalidationOutcome.SUCCESS,
    )
    ledger.request_revalidation(request)
    ledger.record_revalidation(result)

    current = ledger.assurance_for(record.knowledge_id, now=_T2)
    assert current.verification_count == 1
    assert current.failure_count == 0
    assert current.environment_valid is True
    assert current.confidence_state is KnowledgeConfidenceState.SUPPORTED
    assert len(current.evidence) == 2

    restarted = KnowledgeAssuranceLedger(
        journal=EventJournal(_db(tmp_path)),
        knowledge_store=KnowledgeStore(_db(tmp_path)),
    )
    assert restarted.assurance_for(record.knowledge_id, now=_T2) == current
    assert restarted.list_revalidations(record.knowledge_id) == (result,)


def test_two_independent_successes_reach_verified_evidence_not_authority(tmp_path: Path) -> None:
    store, ledger = _ledger(tmp_path)
    record = _record("claim")
    store.insert(record)
    for index in range(2):
        request = KnowledgeRevalidationRequest.create(
            knowledge_id=record.knowledge_id,
            source=f"verifier-{index}",
            requested_at=_T1 + timedelta(minutes=index),
        )
        result = KnowledgeRevalidation(
            revalidation_id=request.revalidation_id,
            knowledge_id=record.knowledge_id,
            requested_at=request.requested_at,
            completed_at=_T2 + timedelta(minutes=index),
            source=request.source,
            outcome=KnowledgeRevalidationOutcome.SUCCESS,
            evidence=(_evidence(f"success-{index}"),),
        )
        ledger.request_revalidation(request)
        ledger.record_revalidation(result)

    current = ledger.assurance_for(record.knowledge_id, now=_T3)
    assert current.verification_count == 2
    assert current.confidence_state is KnowledgeConfidenceState.VERIFIED_EVIDENCE
    stored = store.get(record.knowledge_id)
    assert stored is not None and stored.status is KnowledgeStatus.UNVERIFIED


def test_failed_environment_and_inconclusive_revalidation_do_not_promote(tmp_path: Path) -> None:
    store, ledger = _ledger(tmp_path)
    record = _record("claim")
    store.insert(record)

    for outcome, suffix in (
        (KnowledgeRevalidationOutcome.INCONCLUSIVE, "inconclusive"),
        (KnowledgeRevalidationOutcome.ENVIRONMENT_MISMATCH, "environment"),
    ):
        request, result = _request_and_result(record, outcome=outcome, suffix=suffix)
        ledger.request_revalidation(request)
        ledger.record_revalidation(result)

    current = ledger.assurance_for(record.knowledge_id, now=_T3)
    assert current.verification_count == 0
    assert current.failure_count == 1
    assert current.environment_valid is False
    assert current.confidence_state is KnowledgeConfidenceState.DEGRADED


def test_staleness_decays_assurance_state_without_mutating_record(tmp_path: Path) -> None:
    store, ledger = _ledger(tmp_path)
    record = _record("claim", status=KnowledgeStatus.VERIFIED)
    store.insert(record)
    ledger.record_seed(
        KnowledgeAssuranceMetadata(
            knowledge_id=record.knowledge_id,
            source_observed_at=_T0,
            fresh_until=_T1,
        ),
        recorded_at=_T0,
    )

    current = ledger.assurance_for(record.knowledge_id, now=_T2)
    assert current.confidence_state is KnowledgeConfidenceState.STALE
    assert store.get(record.knowledge_id) == record


def test_relationship_state_overrides_confidence_without_selecting_winner(tmp_path: Path) -> None:
    store, ledger = _ledger(tmp_path)
    first = _record("A", status=KnowledgeStatus.VERIFIED)
    second = _record("not A", status=KnowledgeStatus.VERIFIED)
    store.insert(first)
    store.insert(second)
    store.record_contradiction(
        KnowledgeContradiction(
            first_knowledge_id=first.knowledge_id,
            second_knowledge_id=second.knowledge_id,
        )
    )

    assert (
        ledger.assurance_for(first.knowledge_id).confidence_state
        is KnowledgeConfidenceState.CONFLICTED
    )
    assert (
        ledger.assurance_for(second.knowledge_id).confidence_state
        is KnowledgeConfidenceState.CONFLICTED
    )
    first_stored = store.get(first.knowledge_id)
    second_stored = store.get(second.knowledge_id)
    assert first_stored is not None and first_stored.status is KnowledgeStatus.VERIFIED
    assert second_stored is not None and second_stored.status is KnowledgeStatus.VERIFIED


def test_revalidation_requires_request_and_duplicate_completion_is_explicit(tmp_path: Path) -> None:
    store, ledger = _ledger(tmp_path)
    record = _record("claim")
    store.insert(record)
    request, result = _request_and_result(record, outcome=KnowledgeRevalidationOutcome.FAILURE)

    with pytest.raises(KnowledgeRevalidationNotRequestedError):
        ledger.record_revalidation(result)

    ledger.request_revalidation(request)
    ledger.record_revalidation(result)
    with pytest.raises(DuplicateKnowledgeRevalidationError):
        ledger.record_revalidation(result)


def test_bounded_replay_fails_closed_instead_of_returning_partial_assurance(tmp_path: Path) -> None:
    store, ledger = _ledger(tmp_path)
    record = _record("claim")
    store.insert(record)
    request, result = _request_and_result(record, outcome=KnowledgeRevalidationOutcome.SUCCESS)
    ledger.request_revalidation(request)
    ledger.record_revalidation(result)

    bounded = KnowledgeAssuranceLedger(
        journal=EventJournal(_db(tmp_path)),
        knowledge_store=KnowledgeStore(_db(tmp_path)),
        max_replay_events=1,
    )
    with pytest.raises(KnowledgeAssuranceHistoryTooLargeError):
        bounded.assurance_for(record.knowledge_id)
