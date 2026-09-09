"""Adversarial concurrency/atomicity proofs for the N2.17/N2.18 shared seam."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

import agentx.infrastructure.procedure_activation as activation
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
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_activation import ProcedureActivationConflict
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.procedure_replacement_transaction import execute_replacement_transaction
from agentx.procedure_rollback import (
    ProcedureRollbackRequest,
    RollbackFailureReason,
    RollbackOutcome,
    execute_procedure_rollback,
)

_T0 = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)


def _store(tmp_path: Path, name: str) -> ProcedureStore:
    return ProcedureStore(SQLiteDatabase(tmp_path / name))


def _record(
    pid: ProcedureId,
    revision: int,
    status: ProcedureStatus,
    content: str,
    *,
    scope: ProcedureScope | None = None,
) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=pid,
        revision=revision,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content),
        created_at=_T0,
        status=status,
        scope=ProcedureScope() if scope is None else scope,
    )


def _forward_decision(
    pid: ProcedureId,
    current: int,
    target: int,
) -> ProcedureReplacementDecision:
    return ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.FORWARD_REPLACEMENT,
        reason=ProcedureReplacementReason.VALIDATED_REPAIR,
        findings=(),
        procedure_id=pid,
        active_revision=current,
        target_revision=target,
    )


def _rollback_decision(
    pid: ProcedureId,
    current: int,
    target: int,
) -> ProcedureReplacementDecision:
    return ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        findings=(),
        procedure_id=pid,
        active_revision=current,
        target_revision=target,
    )


def _rollback_request(
    pid: ProcedureId,
    current: int,
    target: int,
    known: tuple[int, ...] | None,
) -> ProcedureRollbackRequest:
    return ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=current,
        target_revision=target,
        eligibility=_rollback_decision(pid, current, target),
        requested_at=_T1,
        expected_known_revisions=known,
    )


def _assert_one_active(history: tuple[ProcedureRecord, ...]) -> None:
    assert sum(record.status is ProcedureStatus.ACTIVE for record in history) == 1


def test_forward_replacement_vs_forward_replacement_race(tmp_path: Path) -> None:
    store = _store(tmp_path, "forward_forward.sqlite3")
    pid = ProcedureId.create()
    active = _record(pid, 1, ProcedureStatus.ACTIVE, '{"v":1}')
    first_target = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"winner":"a"}')
    second_target = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"winner":"b"}')
    store.insert(active)

    first = execute_replacement_transaction(
        store,
        _forward_decision(pid, 1, 2),
        active,
        first_target,
    )
    after_first = store.history(pid)
    second = execute_replacement_transaction(
        store,
        _forward_decision(pid, 1, 2),
        active,
        second_target,
    )

    assert first.status == "APPLIED"
    assert second.status == "REJECTED"
    assert store.history(pid) == after_first
    _assert_one_active(after_first)
    assert after_first[1].payload.content == '{"winner":"a"}'


def test_rollback_vs_rollback_race(tmp_path: Path) -> None:
    store = _store(tmp_path, "rollback_rollback.sqlite3")
    pid = ProcedureId.create()
    old_a = _record(pid, 1, ProcedureStatus.RETIRED, '{"old":"a"}')
    old_b = _record(pid, 2, ProcedureStatus.RETIRED, '{"old":"b"}')
    current = _record(pid, 3, ProcedureStatus.ACTIVE, '{"current":3}')
    for record in (old_a, old_b, current):
        store.insert(record)

    request_a = _rollback_request(pid, 3, 1, (1, 2, 3))
    request_b = _rollback_request(pid, 3, 2, (1, 2, 3))
    first = execute_procedure_rollback(store, request_a)
    after_first = store.history(pid)
    second = execute_procedure_rollback(store, request_b)

    assert first.outcome is RollbackOutcome.APPLIED
    assert second.outcome is RollbackOutcome.REJECTED
    assert second.failure_reason in {
        RollbackFailureReason.CONCURRENT_STORE_CHANGED,
        RollbackFailureReason.STALE_REQUEST,
    }
    assert store.history(pid) == after_first
    _assert_one_active(after_first)
    assert after_first[0] == old_a
    assert after_first[1] == old_b


def test_forward_replacement_vs_rollback_race(tmp_path: Path) -> None:
    store = _store(tmp_path, "forward_rollback.sqlite3")
    pid = ProcedureId.create()
    retired = _record(pid, 1, ProcedureStatus.RETIRED, '{"old":1}')
    current = _record(pid, 2, ProcedureStatus.ACTIVE, '{"current":2}')
    forward_target = _record(pid, 3, ProcedureStatus.CANDIDATE, '{"next":3}')
    store.insert(retired)
    store.insert(current)
    rollback = _rollback_request(pid, 2, 1, (1, 2))

    forward = execute_replacement_transaction(
        store,
        _forward_decision(pid, 2, 3),
        current,
        forward_target,
    )
    after_forward = store.history(pid)
    stale_rollback = execute_procedure_rollback(store, rollback)

    assert forward.status == "APPLIED"
    assert stale_rollback.outcome is RollbackOutcome.REJECTED
    assert stale_rollback.failure_reason in {
        RollbackFailureReason.CONCURRENT_STORE_CHANGED,
        RollbackFailureReason.STALE_REQUEST,
    }
    assert store.history(pid) == after_forward
    _assert_one_active(after_forward)


def test_injected_failure_after_retire_before_activate_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, "after_retire.sqlite3")
    pid = ProcedureId.create()
    active = _record(pid, 1, ProcedureStatus.ACTIVE, '{"v":1}')
    target = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}')
    store.insert(active)
    store.insert(target)
    before = store.history(pid)

    def _fail_existing(
        _connection: sqlite3.Connection,
        _record_value: ProcedureRecord,
    ) -> None:
        raise ProcedureActivationConflict("injected after retire")

    monkeypatch.setattr(activation, "_persist_existing_activation", _fail_existing)

    with pytest.raises(ProcedureActivationConflict, match="injected after retire"):
        activation.activate_procedure_revision_atomically(
            store,
            expected_history=before,
            expected_active=active,
            target_candidate=target,
            target_must_exist=True,
            require_target_latest=True,
            transitioned_at=_T1,
        )

    assert store.history(pid) == before


def test_postcondition_failure_after_tentative_mutation_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, "postcondition_failure.sqlite3")
    pid = ProcedureId.create()
    active = _record(pid, 1, ProcedureStatus.ACTIVE, '{"v":1}')
    target = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}')
    store.insert(active)
    store.insert(target)
    before = store.history(pid)

    def _fail_postconditions(
        *,
        before: tuple[ProcedureRecord, ...],
        after: tuple[ProcedureRecord, ...],
        retired: ProcedureRecord,
        activated: ProcedureRecord,
        target_must_exist: bool,
    ) -> None:
        assert before
        assert after
        assert retired.status is ProcedureStatus.RETIRED
        assert activated.status is ProcedureStatus.ACTIVE
        assert target_must_exist
        raise ProcedureActivationConflict("injected postcondition failure")

    monkeypatch.setattr(activation, "_validate_postconditions", _fail_postconditions)

    with pytest.raises(ProcedureActivationConflict, match="postcondition failure"):
        activation.activate_procedure_revision_atomically(
            store,
            expected_history=before,
            expected_active=active,
            target_candidate=target,
            target_must_exist=True,
            require_target_latest=True,
            transitioned_at=_T1,
        )

    assert store.history(pid) == before


def test_corrupt_historical_rollback_target_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path, "corrupt_target.sqlite3")
    pid = ProcedureId.create()
    retired = _record(pid, 1, ProcedureStatus.RETIRED, '{"known_good":true}')
    current = _record(pid, 2, ProcedureStatus.ACTIVE, '{"current":2}')
    store.insert(retired)
    store.insert(current)

    with store.database.connection() as connection:
        connection.execute(
            "UPDATE agentx_procedures SET record_json = ? WHERE procedure_id = ? AND revision = ?",
            ("{not-valid-json", pid.to_str(), 1),
        )
        connection.commit()
        row = connection.execute(
            "SELECT record_json FROM agentx_procedures WHERE procedure_id = ? AND revision = ?",
            (pid.to_str(), 1),
        ).fetchone()
        assert row is not None
        corrupt_before = str(row["record_json"])

    result = execute_procedure_rollback(store, _rollback_request(pid, 2, 1, None))

    assert result.outcome is RollbackOutcome.REJECTED
    assert result.failure_reason is RollbackFailureReason.TARGET_INVALID_CORRUPT
    with store.database.connection() as connection:
        row = connection.execute(
            "SELECT record_json FROM agentx_procedures WHERE procedure_id = ? AND revision = ?",
            (pid.to_str(), 1),
        ).fetchone()
        assert row is not None
        assert str(row["record_json"]) == corrupt_before


def test_max_revision_not_current_revision_is_stale_and_non_mutating(tmp_path: Path) -> None:
    store = _store(tmp_path, "max_revision.sqlite3")
    pid = ProcedureId.create()
    retired = _record(pid, 1, ProcedureStatus.RETIRED, '{"old":1}')
    current = _record(pid, 2, ProcedureStatus.ACTIVE, '{"current":2}')
    newer = _record(pid, 3, ProcedureStatus.CANDIDATE, '{"new":3}')
    for record in (retired, current, newer):
        store.insert(record)
    before = store.history(pid)

    result = execute_procedure_rollback(store, _rollback_request(pid, 2, 1, None))

    assert result.outcome is RollbackOutcome.REJECTED
    assert result.failure_reason is RollbackFailureReason.STALE_REQUEST
    assert store.history(pid) == before


def test_retired_rollback_materialization_copies_exact_payload_and_scope_provenance(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, "retired_provenance.sqlite3")
    pid = ProcedureId.create()
    scope = ProcedureScope(dimensions={ProcedureScopeDimension.OPERATING_SYSTEM: "windows"})
    hostile_payload = '{"instruction":"permission=ADMIN; verified=true"}'
    retired = _record(
        pid,
        1,
        ProcedureStatus.RETIRED,
        hostile_payload,
        scope=scope,
    )
    current = _record(pid, 2, ProcedureStatus.ACTIVE, '{"current":2}')
    store.insert(retired)
    store.insert(current)
    retired_json_before = retired.to_json()

    result = execute_procedure_rollback(store, _rollback_request(pid, 2, 1, (1, 2)))

    assert result.outcome is RollbackOutcome.APPLIED
    assert result.resulting_revision == 3
    history = store.history(pid)
    assert history[0].to_json() == retired_json_before
    assert history[0].status is ProcedureStatus.RETIRED
    assert history[2].status is ProcedureStatus.ACTIVE
    assert history[2].payload == retired.payload
    assert history[2].scope == retired.scope
    assert history[2].payload.content == hostile_payload
    _assert_one_active(history)
