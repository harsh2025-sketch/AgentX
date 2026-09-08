from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    ProcedureReplacementDecision,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
    ProcedureReplacementReason,
)
from agentx.core.procedures import ProcedureRecord, ProcedureStatus
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.procedure_replacement_transaction import (
    execute_replacement_transaction,
)


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_store.sqlite"
    db = SQLiteDatabase(path=db_path)
    return ProcedureStore(database=db)


def create_record(procedure_id, revision, status=ProcedureStatus.CANDIDATE):
    return ProcedureRecord(
        procedure_id=procedure_id,
        revision=revision,
        status=status,
        created_at=datetime.now(UTC),
    )


def test_successful_forward_replacement(store):
    procedure_id = ProcedureId(uuid4())
    active_record = create_record(procedure_id, 1, ProcedureStatus.ACTIVE)
    target_record = create_record(procedure_id, 2, ProcedureStatus.CANDIDATE)

    store.insert(active_record)

    decision = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.FORWARD_REPLACEMENT,
        reason=ProcedureReplacementReason.REPAIR_VALIDATED,
        findings=(),
        procedure_id=procedure_id,
        active_revision=1,
        target_revision=2,
    )

    # Needs to match exact stored state
    stored_active = store.get(procedure_id, 1)

    result = execute_replacement_transaction(store, decision, stored_active, target_record)

    assert result.status == "APPLIED"

    final_active = store.get(procedure_id, 1)
    assert final_active.status == ProcedureStatus.RETIRED

    final_target = store.get(procedure_id, 2)
    assert final_target.status == ProcedureStatus.ACTIVE


def test_successful_rollback(store):
    procedure_id = ProcedureId(uuid4())
    old_record = create_record(procedure_id, 1, ProcedureStatus.RETIRED)
    active_record = create_record(procedure_id, 2, ProcedureStatus.ACTIVE)

    store.insert(old_record)
    store.insert(active_record)

    decision = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.REGRESSION_OBSERVED,
        findings=(),
        procedure_id=procedure_id,
        active_revision=2,
        target_revision=1,
    )

    stored_active = store.get(procedure_id, 2)
    stored_target = store.get(procedure_id, 1)

    result = execute_replacement_transaction(store, decision, stored_active, stored_target)
    assert result.status == "APPLIED"

    final_active = store.get(procedure_id, 2)
    assert final_active.status == ProcedureStatus.RETIRED

    final_target = store.get(procedure_id, 1)
    assert final_target.status == ProcedureStatus.ACTIVE


def test_reject_ineligible_decision(store):
    procedure_id = ProcedureId(uuid4())
    active_record = create_record(procedure_id, 1, ProcedureStatus.ACTIVE)
    target_record = create_record(procedure_id, 2, ProcedureStatus.CANDIDATE)

    decision = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.INSUFFICIENT_EVIDENCE,
        kind=ProcedureReplacementKind.FORWARD_REPLACEMENT,
        reason=ProcedureReplacementReason.REPAIR_VALIDATED,
        findings=(),
        procedure_id=procedure_id,
        active_revision=1,
        target_revision=2,
    )

    result = execute_replacement_transaction(store, decision, active_record, target_record)
    assert result.status == "REJECTED"
    assert "Decision outcome is" in result.reason


def test_concurrent_state_change(store):
    procedure_id = ProcedureId(uuid4())
    active_record = create_record(procedure_id, 1, ProcedureStatus.ACTIVE)
    target_record = create_record(procedure_id, 2, ProcedureStatus.CANDIDATE)

    store.insert(active_record)
    stored_active = store.get(procedure_id, 1)

    # Concurrently modify state
    store.update_status(procedure_id, 1, ProcedureStatus.RETIRED)

    decision = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.FORWARD_REPLACEMENT,
        reason=ProcedureReplacementReason.REPAIR_VALIDATED,
        findings=(),
        procedure_id=procedure_id,
        active_revision=1,
        target_revision=2,
    )

    result = execute_replacement_transaction(store, decision, stored_active, target_record)
    assert result.status == "REJECTED"
    assert "Active record state has changed concurrently" in result.reason


def test_wrong_procedure_id(store):
    p1 = ProcedureId(uuid4())
    p2 = ProcedureId(uuid4())

    active_record = create_record(p1, 1, ProcedureStatus.ACTIVE)
    target_record = create_record(p1, 2, ProcedureStatus.CANDIDATE)

    decision = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.FORWARD_REPLACEMENT,
        reason=ProcedureReplacementReason.REPAIR_VALIDATED,
        findings=(),
        procedure_id=p2,  # mismatch
        active_revision=1,
        target_revision=2,
    )

    result = execute_replacement_transaction(store, decision, active_record, target_record)
    assert result.status == "REJECTED"


def test_invalid_revision_jump(store):
    procedure_id = ProcedureId(uuid4())
    active_record = create_record(procedure_id, 1, ProcedureStatus.ACTIVE)
    target_record = create_record(procedure_id, 3, ProcedureStatus.CANDIDATE)

    store.insert(active_record)
    stored_active = store.get(procedure_id, 1)

    decision = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.FORWARD_REPLACEMENT,
        reason=ProcedureReplacementReason.REPAIR_VALIDATED,
        findings=(),
        procedure_id=procedure_id,
        active_revision=1,
        target_revision=3,
    )

    result = execute_replacement_transaction(store, decision, stored_active, target_record)
    assert result.status == "REJECTED"
    assert "not contiguous" in result.reason
