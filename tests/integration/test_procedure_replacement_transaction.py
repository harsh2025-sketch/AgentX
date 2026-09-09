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
    db_path = tmp_path / "integration_store.sqlite"
    db = SQLiteDatabase(path=db_path)
    return ProcedureStore(database=db)


def test_integration_full_replacement_lifecycle(store):
    procedure_id = ProcedureId(uuid4())
    active_record = ProcedureRecord(
        procedure_id=procedure_id,
        revision=1,
        status=ProcedureStatus.ACTIVE,
        created_at=datetime.now(UTC),
    )
    store.insert(active_record)

    target_record = ProcedureRecord(
        procedure_id=procedure_id,
        revision=2,
        status=ProcedureStatus.CANDIDATE,
        created_at=datetime.now(UTC),
    )

    decision = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.FORWARD_REPLACEMENT,
        reason=ProcedureReplacementReason.REPAIR_VALIDATED,
        findings=(),
        procedure_id=procedure_id,
        active_revision=1,
        target_revision=2,
    )

    stored_active = store.get(procedure_id, 1)

    result = execute_replacement_transaction(store, decision, stored_active, target_record)
    assert result.status == "APPLIED"
    assert result.previous_revision == 1
    assert result.replacement_revision == 2

    # Verify the new state
    history = store.history(procedure_id)
    assert len(history) == 2
    assert history[0].revision == 1
    assert history[0].status == ProcedureStatus.RETIRED
    assert history[1].revision == 2
    assert history[1].status == ProcedureStatus.ACTIVE
