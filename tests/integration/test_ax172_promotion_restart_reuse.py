"""AX-172 acceptance chain: validate -> promote -> restart -> ACTIVE reuse."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentx.active_procedure_reuse import ActiveProcedureReuse
from agentx.capabilities.abi import CapabilityObservation, ExecutionResult, VerificationResult
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.core.ids import TaskId
from agentx.core.procedure_lifecycle import (
    ProcedureLifecycleReason,
    assess_procedure_transition,
)
from agentx.core.procedure_matching import ProcedureRequirement
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.task_state import try_transition_task
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.active_procedure_reader import ActiveProcedureReader
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.kernel.resource_budget import ResourceUsage
from agentx.procedure_promotion import (
    ProcedurePromotionOutcome,
    ProcedurePromotionRequest,
    promote_procedure_candidate,
)
from agentx.procedure_reuse_selector import ProcedureReuseSelectionOutcome
from agentx.procedure_validation import (
    ProcedureCandidateIdentity,
    ValidationDecision,
    ValidationPolicy,
    ValidationRunEvidence,
)

_T0 = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
_T1 = datetime(2026, 9, 15, 9, 30, tzinfo=UTC)
_SCOPE = ProcedureScope(
    {
        ProcedureScopeDimension.OPERATING_SYSTEM: "windows",
        ProcedureScopeDimension.ENVIRONMENT: "desktop-primary",
    }
)


def _succeeded_task(label: str) -> Task:
    pending = Task.create(objective=label)
    running = try_transition_task(pending, TaskStatus.RUNNING).unwrap()
    return try_transition_task(running, TaskStatus.SUCCEEDED).unwrap()


def _verified_outcome(index: int) -> ClosedLoopOutcome:
    observation = CapabilityObservation(summary="validated", data={"run": index})
    return ClosedLoopOutcome(
        task=_succeeded_task(f"validation run {index}"),
        kind=LoopOutcome.VERIFIED,
        error=None,
        execution=ExecutionResult(
            succeeded=True,
            message="governed execution completed",
            observation=observation,
        ),
        observation=observation,
        verification=VerificationResult(passed=True, detail="postcondition observed"),
        budget_usage=ResourceUsage.zero(),
    )


def test_validated_candidate_survives_restart_and_is_reused_deterministically(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agentx.sqlite3"
    store = ProcedureStore(SQLiteDatabase(path))
    candidate = ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"kind":"procedure","params":{"key":"value"}}',
        ),
        scope=_SCOPE,
        created_at=_T0,
    )
    store.insert(candidate)

    evidence = tuple(
        ValidationRunEvidence(
            run_id=TaskId.create(),
            procedure_id=candidate.procedure_id,
            revision=candidate.revision,
            outcome=_verified_outcome(index),
            parameter_binding={"key": f"value-{index}"},
            environment="desktop-primary",
            recorded_at=_T0,
        )
        for index in (1, 2)
    )
    report = ValidationPolicy(
        allowed_environments=frozenset({"desktop-primary"})
    ).evaluate(
        ProcedureCandidateIdentity(candidate.procedure_id, candidate.revision),
        evidence,
    )
    assert report.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION

    lifecycle = assess_procedure_transition(
        procedure_id=candidate.procedure_id,
        revision=candidate.revision,
        current_status=ProcedureStatus.CANDIDATE,
        target_status=ProcedureStatus.ACTIVE,
        reason=ProcedureLifecycleReason.VALIDATION_PROMOTION,
        requested_at=_T1,
    )
    promoted = promote_procedure_candidate(
        store,
        ProcedurePromotionRequest(
            procedure_id=candidate.procedure_id,
            revision=candidate.revision,
            expected_current=candidate,
            validation_report=report,
            lifecycle_assessment=lifecycle,
            requested_at=_T1,
        ),
    )
    assert promoted.outcome is ProcedurePromotionOutcome.PROMOTED

    # Process boundary: reconstruct both persistence and reuse composition.
    restarted_store = ProcedureStore(SQLiteDatabase(path))
    reuse = ActiveProcedureReuse(ActiveProcedureReader(restarted_store), {})
    selected = reuse.select(ProcedureRequirement(scope=_SCOPE))

    assert selected.outcome is ProcedureReuseSelectionOutcome.SELECTED
    assert selected.selected is not None
    assert selected.selected.record.procedure_id == candidate.procedure_id
    assert selected.selected.record.revision == candidate.revision
    assert selected.selected.record.status is ProcedureStatus.ACTIVE
    assert selected.selected.record.scope == _SCOPE
    assert selected.selected.record.payload == candidate.payload
    assert selected.selected.record.updated_at == _T1
