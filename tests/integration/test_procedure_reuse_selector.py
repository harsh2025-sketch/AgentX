from __future__ import annotations

from datetime import UTC, datetime

from agentx.core.ids import ProcedureId
from agentx.core.procedure_matching import ProcedureCandidate, ProcedureRequirement
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.procedure_reuse_selector import (
    ProcedureReuseSelectionOutcome,
    select_reusable_procedure,
)


def test_selector_is_repeatable_and_does_not_need_a_store() -> None:
    record = ProcedureRecord(
        procedure_id=ProcedureId.create(),
        revision=7,
        payload=ProcedurePayload(ProcedurePayloadKind.CANONICAL_JSON, "task_success=true"),
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        status=ProcedureStatus.ACTIVE,
    )
    candidates = [ProcedureCandidate(record)]
    first = select_reusable_procedure(candidates, ProcedureRequirement())
    second = select_reusable_procedure(candidates, ProcedureRequirement())
    assert first == second
    assert first.outcome is ProcedureReuseSelectionOutcome.SELECTED
