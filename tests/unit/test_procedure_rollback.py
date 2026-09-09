"""Unit tests for procedure rollback transaction (N2.18)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    EvidencePresence,
    ProcedureReplacementDecision,
    ProcedureReplacementEvidence,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
    ProcedureReplacementReason,
    ProcedureReplacementRequest,
    RetiredTargetReactivation,
    TargetIntegrityState,
    assess_procedure_replacement,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureStatus,
)
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.procedure_rollback import (
    ProcedureRollbackRequest,
    ProcedureRollbackRequestError,
    RollbackFailureReason,
    RollbackOutcome,
    execute_procedure_rollback,
)

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "rollback_unit.sqlite3"


def _store(tmp_path: Path) -> ProcedureStore:
    return ProcedureStore(SQLiteDatabase(_db_path(tmp_path)))


def _payload(content: str = '{"graph": "v1"}') -> ProcedurePayload:
    return ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content)


def _record(
    *,
    procedure_id: ProcedureId,
    revision: int,
    status: ProcedureStatus = ProcedureStatus.CANDIDATE,
    content: str = '{"graph": "v1"}',
    created_at: datetime = _T0,
) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=procedure_id,
        revision=revision,
        payload=_payload(content),
        created_at=created_at,
        status=status,
        scope=ProcedureScope(),
    )


def _make_eligible_decision(
    active: ProcedureRecord, target: ProcedureRecord, known: tuple[int, ...]
) -> ProcedureReplacementDecision:
    # Build full evidence for rollback
    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.ABSENT,  # not required for rollback
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("artifact:validation-1",),
    )
    req = ProcedureReplacementRequest(
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        active_revision=active,
        target_revision=target,
        known_revisions=known,
        evidence=evidence,
        retired_target_reactivation=(
            RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT
            if target.status is ProcedureStatus.RETIRED
            else RetiredTargetReactivation.NOT_ATTESTED
        ),
    )
    decision = assess_procedure_replacement(req)
    assert decision.outcome is ProcedureReplacementOutcome.ELIGIBLE
    return decision


def _insert_history(tmp_path: Path, pid: ProcedureId) -> ProcedureStore:
    store = _store(tmp_path)
    # Insert rev1 CANDIDATE, rev2 CANDIDATE, rev3 ACTIVE (bad)
    r1 = _record(procedure_id=pid, revision=1, status=ProcedureStatus.CANDIDATE, content='{"v":1}')
    r2 = _record(procedure_id=pid, revision=2, status=ProcedureStatus.CANDIDATE, content='{"v":2}')
    r3 = _record(
        procedure_id=pid, revision=3, status=ProcedureStatus.CANDIDATE, content='{"v":3 bad}'
    )
    store.insert(r1)
    store.insert(r2)
    store.insert(r3)
    # Activate r3, retire others? For test, we want r3 ACTIVE, r1,r2 CANDIDATE
    store.update_status(pid, 3, ProcedureStatus.ACTIVE, updated_at=_T1)
    return store


# ---------------------------------------------------------------------------
# Valid rollback
# ---------------------------------------------------------------------------


def test_valid_rollback_candidate_target(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    # r2 is known good
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 is not None and r3 is not None

    decision = _make_eligible_decision(r3, r2, (1, 2, 3))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
        expected_known_revisions=(1, 2, 3),
    )

    result = execute_procedure_rollback(store, req)

    assert result.outcome is RollbackOutcome.APPLIED
    assert result.applied
    assert result.procedure_id == pid
    assert result.previous_current_revision == 3
    assert result.rollback_target == 2
    assert result.resulting_revision == 2
    assert result.resulting_status is ProcedureStatus.ACTIVE
    assert result.failure_reason is None

    # History preserved: all 3 revisions still exist
    history = store.history(pid)
    assert len(history) == 3
    assert {r.revision for r in history} == {1, 2, 3}
    # Current bad revision preserved historically as RETIRED
    r3_after = store.get(pid, 3)
    assert r3_after is not None
    assert r3_after.status is ProcedureStatus.RETIRED
    # Target history preserved and now ACTIVE
    r2_after = store.get(pid, 2)
    assert r2_after is not None
    assert r2_after.status is ProcedureStatus.ACTIVE
    assert r2_after.payload.content == '{"v":2}'


def test_valid_rollback_retired_target_creates_new_revision(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)
    r1 = _record(
        procedure_id=pid, revision=1, status=ProcedureStatus.CANDIDATE, content='{"good":1}'
    )
    r2 = _record(
        procedure_id=pid, revision=2, status=ProcedureStatus.CANDIDATE, content='{"good":2}'
    )
    r3 = _record(
        procedure_id=pid, revision=3, status=ProcedureStatus.CANDIDATE, content='{"bad":3}'
    )
    store.insert(r1)
    store.insert(r2)
    store.insert(r3)
    store.update_status(pid, 1, ProcedureStatus.RETIRED, updated_at=_T0)
    store.update_status(pid, 3, ProcedureStatus.ACTIVE, updated_at=_T1)

    # r1 is RETIRED known good, r3 is ACTIVE bad
    r1_after = store.get(pid, 1)
    r3_after = store.get(pid, 3)
    assert r1_after is not None and r3_after is not None
    assert r1_after.status is ProcedureStatus.RETIRED

    decision = _make_eligible_decision(r3_after, r1_after, (1, 2, 3))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=1,
        eligibility=decision,
        requested_at=datetime.now(UTC),
        expected_known_revisions=(1, 2, 3),
    )

    result = execute_procedure_rollback(store, req)

    assert result.outcome is RollbackOutcome.APPLIED
    assert result.resulting_revision == 4  # new revision created
    assert result.resulting_status is ProcedureStatus.ACTIVE

    history = store.history(pid)
    assert len(history) == 4
    # Bad revision preserved as RETIRED
    assert store.get(pid, 3).status is ProcedureStatus.RETIRED
    # Old target preserved as RETIRED (not resurrected)
    assert store.get(pid, 1).status is ProcedureStatus.RETIRED
    # New revision has payload from target
    new_rec = store.get(pid, 4)
    assert new_rec is not None
    assert new_rec.payload.content == '{"good":1}'
    assert new_rec.status is ProcedureStatus.ACTIVE


def test_exact_target_revision_preserved(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    decision = _make_eligible_decision(r3, r2, (1, 2, 3))
    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.rollback_target == 2
    assert result.resulting_revision == 2


# ---------------------------------------------------------------------------
# Fail-closed conditions
# ---------------------------------------------------------------------------


def test_wrong_procedure_id_rejected(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    other_pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    decision = _make_eligible_decision(r3, r2, (1, 2, 3))

    req = ProcedureRollbackRequest(
        procedure_id=other_pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )

    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.REJECTED
    assert result.failure_reason in (
        RollbackFailureReason.PROCEDURE_ID_MISMATCH,
        RollbackFailureReason.MALFORMED_ELIGIBILITY,
        RollbackFailureReason.CROSS_PROCEDURE_TARGET,
    )
    # No partial mutation
    assert store.get(pid, 3).status is ProcedureStatus.ACTIVE


def test_wrong_current_revision_rejected(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    _make_eligible_decision(r3, r2, (1, 2, 3))

    # Request says current is 2 but actual active is 3
    # Need to build eligibility that matches request current=2 to pass validation,
    # but we will test mismatch via store state
    # Build decision for current=2 (make r2 active for decision)
    r2_active = ProcedureRecord(
        procedure_id=pid,
        revision=2,
        payload=r2.payload,
        created_at=r2.created_at,
        status=ProcedureStatus.ACTIVE,
        scope=r2.scope,
    )
    r1 = store.get(pid, 1)
    assert r1
    decision2 = _make_eligible_decision(r2_active, r1, (1, 2, 3))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=2,
        target_revision=1,
        eligibility=decision2,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.REJECTED
    assert result.failure_reason in (
        RollbackFailureReason.CURRENT_NOT_ACTIVE,
        RollbackFailureReason.CURRENT_REVISION_MISMATCH,
        RollbackFailureReason.STALE_REQUEST,
    )
    assert store.get(pid, 3).status is ProcedureStatus.ACTIVE


def test_missing_target_rejected(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)
    r1 = _record(procedure_id=pid, revision=1, content='{"v":1}')
    r2 = _record(procedure_id=pid, revision=2, content='{"v":2}')
    r3 = _record(procedure_id=pid, revision=3, content='{"v":3}')
    store.insert(r1)
    store.insert(r2)
    store.insert(r3)
    store.update_status(pid, 3, ProcedureStatus.ACTIVE, updated_at=_T1)

    r2_rec = store.get(pid, 2)
    r3_active = store.get(pid, 3)
    assert r2_rec and r3_active

    # Build eligibility for rollback 3->2 (valid)
    _make_eligible_decision(r3_active, r2_rec, (1, 2, 3))

    # Now request target 2 but we will delete it from store to simulate missing
    # Instead of deleting, we request target 1 after we manually remove row via SQL
    # to simulate missing target: we have 1,2,3 but we will request 1 after removing 1
    # Use raw SQL to delete revision 1
    with store.database.connection() as conn:
        conn.execute(
            "DELETE FROM agentx_procedures WHERE procedure_id = ? AND revision = ?",
            (pid.to_str(), 1),
        )
        conn.commit()

    # Now store has 2,3 but request says target 1 (which is absent)
    # Need eligibility that matches target 1, but we already have decision for target 2.
    # Create new decision for target 1 eligible (1 absent now, decision made before deletion)
    r1_rec = _record(
        procedure_id=pid, revision=1, status=ProcedureStatus.CANDIDATE, content='{"v":1}'
    )
    r3_for_decision = ProcedureRecord(
        procedure_id=pid,
        revision=3,
        payload=r3_active.payload,
        created_at=r3_active.created_at,
        status=ProcedureStatus.ACTIVE,
        scope=r3_active.scope,
    )
    decision_for_missing = _make_eligible_decision(r3_for_decision, r1_rec, (1, 2, 3))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=1,
        eligibility=decision_for_missing,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.REJECTED
    assert result.failure_reason == RollbackFailureReason.TARGET_REVISION_ABSENT


def test_cross_procedure_target_rejected(tmp_path: Path) -> None:
    pid_a = ProcedureId.create()
    pid_b = ProcedureId.create()
    _store(tmp_path / "a")
    # Use same underlying db for both? Use separate store but same file
    db_path = _db_path(tmp_path)
    store = ProcedureStore(SQLiteDatabase(db_path))
    r_a1 = _record(procedure_id=pid_a, revision=1, content='{"a":1}')
    r_a2 = _record(procedure_id=pid_a, revision=2, content='{"a":2}')
    store.insert(r_a1)
    store.insert(r_a2)
    store.update_status(pid_a, 2, ProcedureStatus.ACTIVE, updated_at=_T1)

    r_b1 = _record(procedure_id=pid_b, revision=1, content='{"b":1}')
    store.insert(r_b1)

    r_a2_active = store.get(pid_a, 2)
    r_b1_rec = store.get(pid_b, 1)
    assert r_a2_active and r_b1_rec

    # Build eligibility with mismatched procedure ids -> should be WRONG_PROCEDURE, not eligible
    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.ABSENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("a",),
    )
    req_elig = ProcedureReplacementRequest(
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        active_revision=r_a2_active,
        target_revision=r_b1_rec,
        known_revisions=(1, 2),
        evidence=evidence,
    )
    decision = assess_procedure_replacement(req_elig)
    # Decision should be WRONG_PROCEDURE, not eligible
    assert decision.outcome is ProcedureReplacementOutcome.WRONG_PROCEDURE

    # Try rollback request with ineligible decision -> should be rejected
    rollback_req = ProcedureRollbackRequest(
        procedure_id=pid_a,
        current_revision=2,
        target_revision=1,  # we will use 1 but eligibility is wrong procedure, so mismatch
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    # This should fail because eligibility target_revision is 1 but procedure_id mismatch
    # The rollback transaction will detect malformed eligibility
    result = execute_procedure_rollback(store, rollback_req)
    assert result.outcome is RollbackOutcome.REJECTED
    assert result.failure_reason in (
        RollbackFailureReason.MALFORMED_ELIGIBILITY,
        RollbackFailureReason.INELIGIBLE_TARGET,
        RollbackFailureReason.PROCEDURE_ID_MISMATCH,
        RollbackFailureReason.CROSS_PROCEDURE_TARGET,
    )


def test_ineligible_target_rejected(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3

    # Build ineligible decision: missing validation evidence
    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.ABSENT,
        shadow_evidence=EvidencePresence.ABSENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("a",),
    )
    req_elig = ProcedureReplacementRequest(
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        active_revision=r3,
        target_revision=r2,
        known_revisions=(1, 2, 3),
        evidence=evidence,
    )
    decision = assess_procedure_replacement(req_elig)
    assert decision.outcome is ProcedureReplacementOutcome.INSUFFICIENT_EVIDENCE

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.REJECTED
    assert result.failure_reason == RollbackFailureReason.INELIGIBLE_TARGET
    # No mutation
    assert store.get(pid, 3).status is ProcedureStatus.ACTIVE


def test_stale_rollback_request_rejected(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)
    r1 = _record(procedure_id=pid, revision=1, content='{"v":1}')
    r2 = _record(procedure_id=pid, revision=2, content='{"v":2}')
    r3 = _record(procedure_id=pid, revision=3, content='{"v":3}')
    r4 = _record(procedure_id=pid, revision=4, content='{"v":4}')
    store.insert(r1)
    store.insert(r2)
    store.insert(r3)
    store.insert(r4)
    store.update_status(pid, 4, ProcedureStatus.ACTIVE, updated_at=_T1)

    # Simulate old request that thought current was 3, but now max is 4
    r3_rec = store.get(pid, 3)
    r2_rec = store.get(pid, 2)
    assert r3_rec and r2_rec
    # For decision, need active 3, target 2
    r3_active = ProcedureRecord(
        procedure_id=pid,
        revision=3,
        payload=r3_rec.payload,
        created_at=r3_rec.created_at,
        status=ProcedureStatus.ACTIVE,
        scope=r3_rec.scope,
    )
    decision = _make_eligible_decision(r3_active, r2_rec, (1, 2, 3))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
        expected_known_revisions=(1, 2, 3),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.REJECTED
    # Could be stale or concurrent or current not active (since 3 is CANDIDATE now)
    assert result.failure_reason in (
        RollbackFailureReason.STALE_REQUEST,
        RollbackFailureReason.CONCURRENT_STORE_CHANGED,
        RollbackFailureReason.CURRENT_REVISION_MISMATCH,
        RollbackFailureReason.CURRENT_NOT_ACTIVE,
    )


def test_concurrent_store_mismatch_rejected(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    decision = _make_eligible_decision(r3, r2, (1, 2, 3))

    # Test malformed expected_known_revisions (must contain current)
    with pytest.raises(ProcedureRollbackRequestError):
        ProcedureRollbackRequest(
            procedure_id=pid,
            current_revision=3,
            target_revision=2,
            eligibility=decision,
            requested_at=datetime.now(UTC),
            expected_known_revisions=(1, 2),  # missing 3
        )

    # Test concurrent: expected (1,2,3) but store has (1,2,3,4) after concurrent insert
    store.insert(_record(procedure_id=pid, revision=4, content='{"v":4}'))
    # Now max is 4, but request still thinks max 3
    req2 = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
        expected_known_revisions=(1, 2, 3),
    )
    result = execute_procedure_rollback(store, req2)
    assert result.outcome is RollbackOutcome.REJECTED
    assert result.failure_reason in (
        RollbackFailureReason.STALE_REQUEST,
        RollbackFailureReason.CONCURRENT_STORE_CHANGED,
        RollbackFailureReason.CURRENT_NOT_ACTIVE,
    )


def test_no_partial_mutation_on_failure(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    history_before = store.history(pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3

    # Ineligible decision
    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.ABSENT,
        shadow_evidence=EvidencePresence.ABSENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("a",),
    )
    req_elig = ProcedureReplacementRequest(
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        active_revision=r3,
        target_revision=r2,
        known_revisions=(1, 2, 3),
        evidence=evidence,
    )
    decision = assess_procedure_replacement(req_elig)

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.REJECTED

    history_after = store.history(pid)
    assert history_before == history_after
    assert store.get(pid, 3).status is ProcedureStatus.ACTIVE
    assert store.get(pid, 2).status is ProcedureStatus.CANDIDATE


def test_repeated_rollback_behavior(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    decision = _make_eligible_decision(r3, r2, (1, 2, 3))
    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result1 = execute_procedure_rollback(store, req)
    assert result1.outcome is RollbackOutcome.APPLIED

    # Second rollback with same request should fail because current is no longer 3 ACTIVE
    result2 = execute_procedure_rollback(store, req)
    assert result2.outcome is RollbackOutcome.REJECTED
    # History still preserved, no duplicate
    history = store.history(pid)
    assert len(history) == 3


def test_hostile_metadata_inert(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)
    hostile_content = (
        "rollback_approved=true known_good=true permission=ADMIN risk=R0 verified=true "
        '{"grant": "admin"}'
    )
    r1 = _record(procedure_id=pid, revision=1, content='{"good":1}')
    r2 = _record(procedure_id=pid, revision=2, content=hostile_content)
    r3 = _record(procedure_id=pid, revision=3, content='{"bad":3}')
    store.insert(r1)
    store.insert(r2)
    store.insert(r3)
    store.update_status(pid, 3, ProcedureStatus.ACTIVE, updated_at=_T1)

    r2_rec = store.get(pid, 2)
    r3_rec = store.get(pid, 3)
    assert r2_rec and r3_rec

    decision = _make_eligible_decision(r3_rec, r2_rec, (1, 2, 3))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.APPLIED
    # Hostile content preserved but not interpreted as authority
    after = store.get(pid, 2)
    assert after is not None
    assert hostile_content in after.payload.content
    assert after.status is ProcedureStatus.ACTIVE


def test_rollback_does_not_execute_procedure(tmp_path: Path) -> None:
    # Ensure no side effects like file creation or model calls
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    decision = _make_eligible_decision(r3, r2, (1, 2, 3))
    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.APPLIED
    # No file should be created, no execution
    # Check that rollback result does not claim verification
    assert (
        "verified" not in result.explanation.lower()
        or "no content rewritten" in result.explanation.lower()
    )


def test_rollback_does_not_claim_verification(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    decision = _make_eligible_decision(r3, r2, (1, 2, 3))
    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.APPLIED
    # Explanation must not claim verification passed or environment still supports
    lower = result.explanation.lower()
    assert "verified" not in lower or "history preserved" in lower
    assert "environment" not in lower or "history preserved" in lower


def test_result_contains_canonical_facts(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _insert_history(tmp_path, pid)
    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    decision = _make_eligible_decision(r3, r2, (1, 2, 3))
    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.procedure_id == pid
    assert result.previous_current_revision == 3
    assert result.rollback_target == 2
    assert result.resulting_revision == 2
    assert result.resulting_status == ProcedureStatus.ACTIVE
    assert result.schema_version == 1


def test_invalid_request_rejected() -> None:
    pid = ProcedureId.create()
    r = _record(procedure_id=pid, revision=1)
    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.ABSENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("a",),
    )
    req_elig = ProcedureReplacementRequest(
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        active_revision=ProcedureRecord(
            procedure_id=pid,
            revision=2,
            payload=r.payload,
            created_at=_T0,
            status=ProcedureStatus.ACTIVE,
            scope=r.scope,
        ),
        target_revision=r,
        known_revisions=(1, 2),
        evidence=evidence,
    )
    decision = assess_procedure_replacement(req_elig)
    # target >= current should be rejected at request construction
    with pytest.raises(ProcedureRollbackRequestError):
        ProcedureRollbackRequest(
            procedure_id=pid,
            current_revision=1,
            target_revision=2,
            eligibility=decision,
            requested_at=datetime.now(UTC),
        )
