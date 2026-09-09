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
from agentx.core.procedures import ProcedurePayload, ProcedurePayloadKind, ProcedureRecord, ProcedureStatus
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.procedure_rollback import ProcedureRollbackRequest, RollbackOutcome, execute_procedure_rollback

_T0 = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)


def test_retired_target_rollback_is_atomic_restart_safe_and_history_preserving(tmp_path: Path) -> None:
    path = tmp_path / "rollback_restart.sqlite3"
    pid = ProcedureId.create()
    store = ProcedureStore(SQLiteDatabase(path))
    retired = ProcedureRecord(
        procedure_id=pid,
        revision=1,
        payload=ProcedurePayload(ProcedurePayloadKind.CANONICAL_JSON, '{"known_good":true}'),
        created_at=_T0,
        status=ProcedureStatus.RETIRED,
    )
    current = ProcedureRecord(
        procedure_id=pid,
        revision=2,
        payload=ProcedurePayload(ProcedurePayloadKind.CANONICAL_JSON, '{"regressed":true}'),
        created_at=_T0,
        status=ProcedureStatus.ACTIVE,
    )
    store.insert(retired)
    store.insert(current)
    decision = ProcedureReplacementDecision(
        outcome=ProcedureReplacementOutcome.ELIGIBLE,
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
        eligibility=decision,
        requested_at=_T1,
        expected_known_revisions=(1, 2),
    )

    result = execute_procedure_rollback(store, request)
    assert result.outcome is RollbackOutcome.APPLIED

    restarted = ProcedureStore(SQLiteDatabase(path))
    history = restarted.history(pid)
    assert tuple(record.revision for record in history) == (1, 2, 3)
    assert history[0].status is ProcedureStatus.RETIRED
    assert history[1].status is ProcedureStatus.RETIRED
    assert history[2].status is ProcedureStatus.ACTIVE
    assert history[0].payload.content == '{"known_good":true}'
    assert history[1].payload.content == '{"regressed":true}'
    assert history[2].payload.content == '{"known_good":true}'
    assert sum(record.status is ProcedureStatus.ACTIVE for record in history) == 1
