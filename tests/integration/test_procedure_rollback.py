"""Integration tests for procedure rollback transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    EvidencePresence,
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
    RollbackOutcome,
    execute_procedure_rollback,
)

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> ProcedureStore:
    return ProcedureStore(SQLiteDatabase(tmp_path / "integration_rollback.sqlite3"))


def _payload(content: str) -> ProcedurePayload:
    return ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content)


def _record(pid: ProcedureId, rev: int, status: ProcedureStatus, content: str) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=pid,
        revision=rev,
        payload=_payload(content),
        created_at=_T0,
        status=status,
        scope=ProcedureScope(),
    )


def test_integration_rollback_preserves_history_and_atomic(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)

    # Create 3 revisions, 3 ACTIVE bad
    for i in range(1, 4):
        store.insert(_record(pid, i, ProcedureStatus.CANDIDATE, f'{{"v":{i}}}'))

    store.update_status(pid, 3, ProcedureStatus.ACTIVE, updated_at=_T1)

    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3

    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.ABSENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("artifact:1",),
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
    assert decision.outcome is ProcedureReplacementOutcome.ELIGIBLE

    rollback_req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
        expected_known_revisions=(1, 2, 3),
    )

    result = execute_procedure_rollback(store, rollback_req)
    assert result.outcome is RollbackOutcome.APPLIED

    # Verify persistence across restart
    del store
    store2 = _store(tmp_path)
    history = store2.history(pid)
    assert len(history) == 3
    assert store2.get(pid, 3).status is ProcedureStatus.RETIRED
    assert store2.get(pid, 2).status is ProcedureStatus.ACTIVE
    # Payload preserved
    assert store2.get(pid, 2).payload.content == '{"v":2}'


def test_integration_rollback_retired_target_creates_new_revision(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)

    r1 = _record(pid, 1, ProcedureStatus.CANDIDATE, '{"good":1}')
    r2 = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"good":2}')
    r3 = _record(pid, 3, ProcedureStatus.CANDIDATE, '{"bad":3}')
    store.insert(r1)
    store.insert(r2)
    store.insert(r3)
    store.update_status(pid, 1, ProcedureStatus.RETIRED, updated_at=_T0)
    store.update_status(pid, 3, ProcedureStatus.ACTIVE, updated_at=_T1)

    r1_after = store.get(pid, 1)
    r3_after = store.get(pid, 3)
    assert r1_after and r3_after
    assert r1_after.status is ProcedureStatus.RETIRED

    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.ABSENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("artifact:1",),
    )
    req_elig = ProcedureReplacementRequest(
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        active_revision=r3_after,
        target_revision=r1_after,
        known_revisions=(1, 2, 3),
        evidence=evidence,
        retired_target_reactivation=RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT,
    )
    decision = assess_procedure_replacement(req_elig)
    assert decision.outcome is ProcedureReplacementOutcome.ELIGIBLE

    rollback_req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=1,
        eligibility=decision,
        requested_at=datetime.now(UTC),
        expected_known_revisions=(1, 2, 3),
    )

    result = execute_procedure_rollback(store, rollback_req)
    assert result.outcome is RollbackOutcome.APPLIED
    assert result.resulting_revision == 4

    # Verify persistence across restart
    del store
    store2 = _store(tmp_path)
    assert len(store2.history(pid)) == 4
    assert store2.get(pid, 3).status is ProcedureStatus.RETIRED
    assert store2.get(pid, 1).status is ProcedureStatus.RETIRED
    new_rec = store2.get(pid, 4)
    assert new_rec is not None
    assert new_rec.payload.content == '{"good":1}'
    assert new_rec.status is ProcedureStatus.ACTIVE
