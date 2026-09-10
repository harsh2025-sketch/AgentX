from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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
from agentx.procedure_replacement_transaction import execute_replacement_transaction


def test_forward_replacement_is_atomic_and_restart_safe(tmp_path: Path) -> None:
    path = tmp_path / "replacement_restart.sqlite3"
    pid = ProcedureId.create()
    store = ProcedureStore(SQLiteDatabase(path))
    active = ProcedureRecord(
        procedure_id=pid,
        revision=1,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"v":1}',
        ),
        created_at=datetime(2026, 9, 9, tzinfo=UTC),
        status=ProcedureStatus.ACTIVE,
    )
    target = ProcedureRecord(
        procedure_id=pid,
        revision=2,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"v":2}',
        ),
        created_at=datetime(2026, 9, 9, tzinfo=UTC),
        status=ProcedureStatus.CANDIDATE,
    )
    store.insert(active)
    decision = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
        kind=ProcedureReplacementKind.FORWARD_REPLACEMENT,
        reason=ProcedureReplacementReason.VALIDATED_REPAIR,
        findings=(),
        procedure_id=pid,
        active_revision=1,
        target_revision=2,
    )

    result = execute_replacement_transaction(store, decision, active, target)
    assert result.status == "APPLIED"

    restarted = ProcedureStore(SQLiteDatabase(path))
    history = restarted.history(pid)
    assert tuple(record.revision for record in history) == (1, 2)
    assert sum(record.status is ProcedureStatus.ACTIVE for record in history) == 1
    assert history[0].status is ProcedureStatus.RETIRED
    assert history[1].status is ProcedureStatus.ACTIVE
