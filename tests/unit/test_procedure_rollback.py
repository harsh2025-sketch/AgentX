from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    ProcedureReplacementDecision,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
    ProcedureReplacementReason,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
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

_T0 = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> ProcedureStore:
    return ProcedureStore(SQLiteDatabase(tmp_path / "rollback.sqlite3"))


def _record(pid: ProcedureId, revision: int, status: ProcedureStatus, text: str) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=pid,
        revision=revision,
        payload=ProcedurePayload(ProcedurePayloadKind.CANONICAL_JSON, text),
        created_at=_T0,
        status=status,
    )


def _decision(pid: ProcedureId, current: int, target: int) -> ProcedureReplacementDecision:
    return ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        findings=(),
        procedure_id=pid,
        active_revision=current,
        target_revision=target,
    )


def _request(pid: ProcedureId, current: int, target: int, known: tuple[int, ...]) -> ProcedureRollbackRequest:
    return ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=current,
        target_revision=target,
        eligibility=_decision(pid, current, target),
        requested_at=_T1,
        expected_known_revisions=known,
    )


def test_candidate_rollback_activates_exact_caller_target_and_preserves_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    target = _record(pid, 1, ProcedureStatus.CANDIDATE, '{"old":1}')
    current = _record(pid, 2, ProcedureStatus.ACTIVE, '{"new":2}')
    store.insert(target)
    store.insert(current)

    result = execute_procedure_rollback(store, _request(pid, 2, 1, (1, 2)))

    assert result.outcome is RollbackOutcome.APPLIED
    assert result.resulting_revision == 1
    history = store.history(pid)
    assert [record.status for record in history] == [
        ProcedureStatus.ACTIVE,
        ProcedureStatus.RETIRED,
    ]
    assert history[0].payload.content == '{"old":1}'
    assert history[1].payload.content == '{"new":2}'
    assert sum(record.status is ProcedureStatus.ACTIVE for record in history) == 1


def test_retired_target_is_never_resurrected_and_new_revision_is_created(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    retired = _record(pid, 1, ProcedureStatus.RETIRED, 'permission=ADMIN verified=true')
    current = _record(pid, 2, ProcedureStatus.ACTIVE, '{"new":2}')
    store.insert(retired)
    store.insert(current)

    result = execute_procedure_rollback(store, _request(pid, 2, 1, (1, 2)))

    assert result.outcome is RollbackOutcome.APPLIED
    assert result.resulting_revision == 3
    history = store.history(pid)
    assert tuple(record.revision for record in history) == (1, 2, 3)
    assert history[0].status is ProcedureStatus.RETIRED
    assert history[0].payload.content == 'permission=ADMIN verified=true'
    assert history[1].status is ProcedureStatus.RETIRED
    assert history[2].status is ProcedureStatus.ACTIVE
    assert history[2].payload == history[0].payload
    assert sum(record.status is ProcedureStatus.ACTIVE for record in history) == 1


def test_stale_request_with_newer_revision_is_rejected_without_mutation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    target = _record(pid, 1, ProcedureStatus.RETIRED, '{"old":1}')
    current = _record(pid, 2, ProcedureStatus.ACTIVE, '{"live":2}')
    newer = _record(pid, 3, ProcedureStatus.CANDIDATE, '{"newer":3}')
    store.insert(target)
    store.insert(current)
    store.insert(newer)
    before = store.history(pid)

    result = execute_procedure_rollback(
        store,
        ProcedureRollbackRequest(
            procedure_id=pid,
            current_revision=2,
            target_revision=1,
            eligibility=_decision(pid, 2, 1),
            requested_at=_T1,
        ),
    )

    assert result.failure_reason is RollbackFailureReason.STALE_REQUEST
    assert store.history(pid) == before


def test_expected_revision_set_is_exact_concurrency_guard(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    target = _record(pid, 1, ProcedureStatus.CANDIDATE, '{"old":1}')
    current = _record(pid, 2, ProcedureStatus.ACTIVE, '{"live":2}')
    store.insert(target)
    store.insert(current)
    request = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=2,
        target_revision=1,
        eligibility=_decision(pid, 2, 1),
        requested_at=_T1,
        expected_known_revisions=(2,),
    )
    result = execute_procedure_rollback(store, request)
    assert result.failure_reason is RollbackFailureReason.CONCURRENT_STORE_CHANGED
    assert store.history(pid) == (target, current)


def test_multiple_active_revisions_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    target = _record(pid, 1, ProcedureStatus.RETIRED, '{"old":1}')
    extra = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"extra":2}')
    current = _record(pid, 3, ProcedureStatus.ACTIVE, '{"live":3}')
    store.insert(target)
    store.insert(extra)
    store.insert(current)
    extra_active = store.update_status(pid, 2, ProcedureStatus.ACTIVE, updated_at=_T0)
    assert extra_active.status is ProcedureStatus.ACTIVE
    before = store.history(pid)

    result = execute_procedure_rollback(store, _request(pid, 3, 1, (1, 2, 3)))

    assert result.failure_reason is RollbackFailureReason.CONCURRENT_STORE_CHANGED
    assert store.history(pid) == before


def test_ineligible_or_mismatched_decision_cannot_mutate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    target = _record(pid, 1, ProcedureStatus.CANDIDATE, '{"old":1}')
    current = _record(pid, 2, ProcedureStatus.ACTIVE, '{"live":2}')
    store.insert(target)
    store.insert(current)
    bad = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.INSUFFICIENT_EVIDENCE,
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        findings=(),
        procedure_id=pid,
        active_revision=2,
        target_revision=1,
    )
    request = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=2,
        target_revision=1,
        eligibility=bad,
        requested_at=_T1,
        expected_known_revisions=(1, 2),
    )
    result = execute_procedure_rollback(store, request)
    assert result.failure_reason is RollbackFailureReason.INELIGIBLE_TARGET
    assert store.history(pid) == (target, current)


def test_request_rejects_automatic_or_non_rollback_target_relation() -> None:
    pid = ProcedureId.create()
    with pytest.raises(ProcedureRollbackRequestError):
        ProcedureRollbackRequest(
            procedure_id=pid,
            current_revision=2,
            target_revision=2,
            eligibility=_decision(pid, 2, 1),
            requested_at=_T1,
        )
