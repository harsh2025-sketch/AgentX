"""Integration tests for the M4.02 procedure validation evidence policy.

These feed REAL canonical contracts — procedure records, Tasks transitioned
through the A1.06 state machine, VerificationResult/ExecutionResult/
CapabilityObservation, and ClosedLoopOutcome — into the policy. No capability
is executed, no store is opened, and no lifecycle is mutated: the point is to
prove the policy composes over the canonical evidence path with no simplified
or fake evidence shim.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentx.capabilities.abi import (
    CapabilityObservation,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.task_state import try_transition_task
from agentx.core.tasks import JsonValue, Task, TaskStatus
from agentx.kernel.resource_budget import ResourceUsage
from agentx.procedure_validation import (
    ProcedureCandidateIdentity,
    ValidationDecision,
    ValidationPolicy,
    ValidationRunEvidence,
)

_T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _record(revision: int, procedure_id: ProcedureId | None = None) -> ProcedureRecord:
    """A real canonical procedure record born CANDIDATE (schema v1)."""
    return ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content=('{"schema_version":1,"entry":"start","nodes":[],"edges":[]}'),
        ),
        procedure_id=ProcedureId.create() if procedure_id is None else procedure_id,
        revision=revision,
        created_at=_T0,
    )


def _terminal_task(status: TaskStatus) -> Task:
    pending = Task.create(objective="validate procedure candidate", created_at=_T0)
    running = try_transition_task(pending, TaskStatus.RUNNING).unwrap()
    return try_transition_task(running, status).unwrap()


def _observation() -> CapabilityObservation:
    return CapabilityObservation(
        summary="target cell equals the expected value", data={"match": True}
    )


def _verified_outcome() -> ClosedLoopOutcome:
    observation = _observation()
    return ClosedLoopOutcome(
        task=_terminal_task(TaskStatus.SUCCEEDED),
        kind=LoopOutcome.VERIFIED,
        error=None,
        execution=ExecutionResult(
            succeeded=True,
            message="invocation produced a result",
            observation=observation,
        ),
        observation=observation,
        verification=VerificationResult(passed=True, detail="postcondition confirmed"),
        budget_usage=ResourceUsage.zero(),
    )


def _verification_failure_outcome() -> ClosedLoopOutcome:
    return ClosedLoopOutcome(
        task=_terminal_task(TaskStatus.FAILED),
        kind=LoopOutcome.VERIFICATION_FAILED,
        error=AgentXError(
            code="runtime.verification_failed",
            message="postcondition did not hold",
            category=ErrorCategory.VERIFICATION,
        ),
        execution=None,
        observation=_observation(),
        verification=VerificationResult(passed=False, detail="postcondition did not hold"),
        budget_usage=ResourceUsage.zero(),
    )


def _evidence(
    record: ProcedureRecord,
    *,
    run_id: TaskId,
    outcome: ClosedLoopOutcome,
    binding: dict[str, JsonValue],
    environment: str | None = None,
) -> ValidationRunEvidence:
    return ValidationRunEvidence(
        run_id=run_id,
        procedure_id=record.procedure_id,
        revision=record.revision,
        outcome=outcome,
        parameter_binding=binding,
        environment=environment,
        recorded_at=_T0,
    )


def test_real_canonical_evidence_reaches_eligibility() -> None:
    record = _record(revision=4)
    candidate = ProcedureCandidateIdentity.from_record(record)

    evidence = [
        _evidence(
            record, run_id=TaskId.create(), outcome=_verified_outcome(), binding={"cell": "A1"}
        ),
        _evidence(
            record, run_id=TaskId.create(), outcome=_verified_outcome(), binding={"cell": "B2"}
        ),
    ]

    report = ValidationPolicy().evaluate(candidate, evidence)

    assert report.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert report.verified_successes == 2
    assert report.distinct_parameter_bindings == 2
    assert report.candidate == candidate
    assert report.candidate.procedure_id == record.procedure_id
    assert report.candidate.revision == record.revision


def test_real_canonical_evidence_never_mutates_the_record() -> None:
    record = _record(revision=5)
    candidate = ProcedureCandidateIdentity.from_record(record)

    before = record.to_dict()
    ValidationPolicy().evaluate(
        candidate,
        [_evidence(record, run_id=TaskId.create(), outcome=_verified_outcome(), binding={})],
    )

    # The record is an immutable snapshot; nothing about it may change — and
    # certainly not its status — as a side effect of producing a decision.
    assert record.to_dict() == before
    assert record.status is ProcedureStatus.CANDIDATE


def test_real_failure_evidence_blocks_eligibility() -> None:
    record = _record(revision=6)
    candidate = ProcedureCandidateIdentity.from_record(record)

    evidence = [
        _evidence(
            record, run_id=TaskId.create(), outcome=_verified_outcome(), binding={"cell": "A1"}
        ),
        _evidence(
            record, run_id=TaskId.create(), outcome=_verified_outcome(), binding={"cell": "B2"}
        ),
        _evidence(
            record,
            run_id=TaskId.create(),
            outcome=_verification_failure_outcome(),
            binding={"cell": "C3"},
        ),
    ]

    report = ValidationPolicy().evaluate(candidate, evidence)

    assert report.decision is ValidationDecision.REJECTED
    assert report.verified_successes == 2
    assert len(report.failures) == 1


def test_real_stale_revision_evidence_is_scoped_out() -> None:
    identity = ProcedureId.create()
    current = _record(revision=7, procedure_id=identity)
    older = _record(revision=6, procedure_id=identity)
    candidate = ProcedureCandidateIdentity.from_record(current)

    stale = _evidence(
        older, run_id=TaskId.create(), outcome=_verified_outcome(), binding={"cell": "A1"}
    )
    fresh = _evidence(
        current, run_id=TaskId.create(), outcome=_verified_outcome(), binding={"cell": "A1"}
    )

    report = ValidationPolicy().evaluate(candidate, [stale, fresh])

    assert report.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert report.verified_successes == 1
    assert stale.run_id in report.stale_revision_records


def test_distinct_revisions_of_the_same_identity_never_count_together() -> None:
    current = _record(revision=8)
    candidate = ProcedureCandidateIdentity.from_record(current)

    old_revision = _evidence(
        current, run_id=TaskId.create(), outcome=_verified_outcome(), binding={"cell": "A1"}
    )
    # Re-bind the same canonical evidence to a different revision: it must not
    # validate the candidate revision.
    old_revision = ValidationRunEvidence(
        run_id=old_revision.run_id,
        procedure_id=old_revision.procedure_id,
        revision=1,
        outcome=old_revision.outcome,
        parameter_binding=old_revision.parameter_binding,
        environment=old_revision.environment,
        recorded_at=old_revision.recorded_at,
    )
    fresh = _evidence(
        current, run_id=TaskId.create(), outcome=_verified_outcome(), binding={"cell": "A1"}
    )

    report = ValidationPolicy().evaluate(candidate, [old_revision, fresh])
    assert report.verified_successes == 1
    assert old_revision.run_id in report.stale_revision_records
    assert report.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
