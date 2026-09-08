"""Unit tests for the M4.02 procedure-candidate validation evidence policy.

These pin the deterministic decision rules in isolation, without executing any
capability and without touching any store:

- zero/one/many verified successes;
- duplicate replay counted once; conflicting duplicate identity fails closed;
- every canonical failure class blocks clean eligibility;
- stale revision and foreign identity evidence are excluded and reported;
- variation (parameter bindings) versus exact repeats;
- deterministic output and bounded configuration;
- hostile strings stay inert; success requires canonical verification truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from agentx.capabilities.abi import (
    CapabilityObservation,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedures import ProcedurePayload, ProcedurePayloadKind, ProcedureRecord
from agentx.core.task_state import try_transition_task
from agentx.core.tasks import JsonValue, Task, TaskStatus
from agentx.kernel.resource_budget import ResourceUsage
from agentx.procedure_validation import (
    MIN_VERIFIED_SUCCESSES_FLOOR,
    ProcedureCandidateIdentity,
    ValidationDecision,
    ValidationEvidenceError,
    ValidationPolicy,
    ValidationPolicyConfigError,
    ValidationReasonCode,
    ValidationRunEvidence,
    ValidationRunKind,
)

_T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Canonical evidence builders. The policy consumes the same frozen value types
# the A1.10 runtime manufactures in production; nothing here is a fake.
# ---------------------------------------------------------------------------


def _succeeded_task() -> Task:
    pending = Task.create(objective="validate the procedure candidate")
    running = try_transition_task(pending, TaskStatus.RUNNING).unwrap()
    return try_transition_task(running, TaskStatus.SUCCEEDED).unwrap()


def _failed_task() -> Task:
    pending = Task.create(objective="validate the procedure candidate")
    running = try_transition_task(pending, TaskStatus.RUNNING).unwrap()
    return try_transition_task(running, TaskStatus.FAILED).unwrap()


def _cancelled_task() -> Task:
    return try_transition_task(
        Task.create(objective="validate the procedure candidate"), TaskStatus.CANCELLED
    ).unwrap()


def _observation(data: Mapping[str, JsonValue] | None = None) -> CapabilityObservation:
    return CapabilityObservation(summary="step completed", data={} if data is None else dict(data))


def make_outcome(
    *,
    kind: LoopOutcome = LoopOutcome.VERIFIED,
    task: Task | None = None,
    verification: VerificationResult | None = None,
    execution: ExecutionResult | None = None,
    observation: CapabilityObservation | None = None,
    error: AgentXError | None = None,
) -> ClosedLoopOutcome:
    if task is None:
        task = _succeeded_task() if kind is LoopOutcome.VERIFIED else _failed_task()
    if error is None and kind is not LoopOutcome.VERIFIED:
        error = AgentXError(
            code="runtime.test",
            message=f"run closed as {kind.value}",
            category=ErrorCategory.EXECUTION,
        )
    return ClosedLoopOutcome(
        task=task,
        kind=kind,
        error=error,
        execution=execution,
        observation=observation,
        verification=verification,
        budget_usage=ResourceUsage.zero(),
    )


def verified_outcome() -> ClosedLoopOutcome:
    observation = _observation({"stored": True})
    return make_outcome(
        kind=LoopOutcome.VERIFIED,
        verification=VerificationResult(passed=True, detail="postcondition holds"),
        execution=ExecutionResult(
            succeeded=True, message="invocation produced a result", observation=observation
        ),
        observation=observation,
    )


def make_evidence(
    procedure_id: ProcedureId,
    revision: int,
    *,
    run_id: TaskId | None = None,
    outcome: ClosedLoopOutcome | None = None,
    binding: Mapping[str, JsonValue] | None = None,
    environment: str | None = None,
    recorded_at: datetime | None = _T0,
) -> ValidationRunEvidence:
    return ValidationRunEvidence(
        run_id=TaskId.create() if run_id is None else run_id,
        procedure_id=procedure_id,
        revision=revision,
        outcome=verified_outcome() if outcome is None else outcome,
        parameter_binding={} if binding is None else dict(binding),
        environment=environment,
        recorded_at=recorded_at,
    )


def default_policy() -> ValidationPolicy:
    return ValidationPolicy()


# ---------------------------------------------------------------------------
# Evidence quantity.
# ---------------------------------------------------------------------------


def test_zero_evidence_is_insufficient() -> None:
    report = default_policy().evaluate(ProcedureCandidateIdentity(ProcedureId.create(), 3), [])
    assert report.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert report.verified_successes == 0
    assert report.counted_success_run_ids == ()
    assert report.reasons == (ValidationReasonCode.NO_EVIDENCE,)


def test_one_verified_success_is_insufficient() -> None:
    identity = ProcedureId.create()
    evidence = [make_evidence(identity, 3)]
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 3), evidence)
    assert report.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert report.verified_successes == 1
    assert ValidationReasonCode.INSUFFICIENT_VERIFIED_SUCCESSES in report.reasons


def test_two_distinct_verified_successes_are_eligible() -> None:
    identity = ProcedureId.create()
    evidence = [
        make_evidence(identity, 4, binding={"target": "alpha"}),
        make_evidence(identity, 4, binding={"target": "beta"}),
    ]
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 4), evidence)
    assert report.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert report.eligible is True
    assert report.verified_successes == 2
    assert report.distinct_parameter_bindings == 2
    assert len(report.counted_success_run_ids) == 2
    assert report.reasons == ()


def test_two_successes_with_same_binding_still_meet_default_variation() -> None:
    identity = ProcedureId.create()
    evidence = [
        make_evidence(identity, 5, binding={"target": "alpha"}),
        make_evidence(identity, 5, binding={"target": "alpha"}),
    ]
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 5), evidence)
    assert report.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert report.distinct_parameter_bindings == 1
    assert report.exact_repeat_only is True


# ---------------------------------------------------------------------------
# Evidence identity / replay.
# ---------------------------------------------------------------------------


def test_duplicate_same_run_counts_once() -> None:
    identity = ProcedureId.create()
    run_id = TaskId.create()
    shared_outcome = verified_outcome()
    first = make_evidence(
        identity, 6, run_id=run_id, binding={"target": "alpha"}, outcome=shared_outcome
    )
    replay = make_evidence(
        identity, 6, run_id=run_id, binding={"target": "alpha"}, outcome=shared_outcome
    )
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 6), [first, replay])
    assert report.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert report.verified_successes == 1
    assert report.replayed_duplicates == (run_id,)


def test_duplicate_replay_does_not_inflate_distinct_bindings() -> None:
    identity = ProcedureId.create()
    run_a = TaskId.create()
    run_b = TaskId.create()
    run_b_outcome = verified_outcome()
    evidence = [
        make_evidence(identity, 7, run_id=run_a, binding={"target": "alpha"}),
        make_evidence(identity, 7, run_id=run_b, binding={"target": "beta"}, outcome=run_b_outcome),
        make_evidence(identity, 7, run_id=run_b, binding={"target": "beta"}, outcome=run_b_outcome),
    ]
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 7), evidence)
    assert report.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert report.verified_successes == 2
    assert report.distinct_parameter_bindings == 2
    assert report.replayed_duplicates == (run_b,)


def test_conflicting_duplicate_identity_fails_closed() -> None:
    identity = ProcedureId.create()
    run_id = TaskId.create()
    first = make_evidence(identity, 8, run_id=run_id, binding={"target": "alpha"})
    conflicting = make_evidence(identity, 8, run_id=run_id, binding={"target": "beta"})
    report = default_policy().evaluate(
        ProcedureCandidateIdentity(identity, 8), [first, conflicting]
    )
    assert report.decision is ValidationDecision.DEGRADED
    assert not report.eligible
    assert any(
        record.reason is ValidationReasonCode.CONFLICTING_DUPLICATE_IDENTITY
        for record in report.invalid_evidence
    )


def test_replayed_timestamp_change_is_still_a_replay() -> None:
    identity = ProcedureId.create()
    run_id = TaskId.create()
    shared_outcome = verified_outcome()
    first = make_evidence(identity, 9, run_id=run_id, recorded_at=_T0, outcome=shared_outcome)
    replay = make_evidence(
        identity,
        9,
        run_id=run_id,
        recorded_at=datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
        outcome=shared_outcome,
    )
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 9), [first, replay])
    assert report.replayed_duplicates == (run_id,)
    assert report.verified_successes == 1


# ---------------------------------------------------------------------------
# Failure evidence.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "verification", "task", "error_category", "expected_kind"),
    [
        (
            LoopOutcome.VERIFICATION_FAILED,
            VerificationResult(passed=False, detail="postcondition mismatch"),
            None,
            ErrorCategory.VERIFICATION,
            ValidationRunKind.VERIFICATION_FAILURE,
        ),
        (
            LoopOutcome.EXECUTION_FAILED,
            None,
            None,
            ErrorCategory.EXECUTION,
            ValidationRunKind.EXECUTION_FAILURE,
        ),
        (
            LoopOutcome.DENIED,
            None,
            None,
            ErrorCategory.PERMISSION,
            ValidationRunKind.DENIED,
        ),
        (
            LoopOutcome.DENIED,
            None,
            _cancelled_task(),
            ErrorCategory.TIMEOUT,
            ValidationRunKind.TIMEOUT_OR_CANCEL,
        ),
    ],
)
def test_canonical_failures_block_clean_eligibility(
    kind: LoopOutcome,
    verification: VerificationResult | None,
    task: Task | None,
    error_category: ErrorCategory,
    expected_kind: ValidationRunKind,
) -> None:
    identity = ProcedureId.create()
    failure_outcome = make_outcome(
        kind=kind,
        verification=verification,
        task=task,
        error=AgentXError(code="runtime.test", message="run failed", category=error_category),
    )
    evidence = [
        make_evidence(identity, 10, binding={"target": "alpha"}),
        make_evidence(identity, 10, binding={"target": "beta"}),
        make_evidence(identity, 10, outcome=failure_outcome),
    ]
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 10), evidence)
    assert report.decision is ValidationDecision.REJECTED
    assert report.verified_successes == 2
    assert any(failure.kind is expected_kind for failure in report.failures)
    assert _reason_for_kind(expected_kind) in report.reasons


def _reason_for_kind(kind: ValidationRunKind) -> ValidationReasonCode:
    return {
        ValidationRunKind.VERIFICATION_FAILURE: ValidationReasonCode.VERIFICATION_FAILURE,
        ValidationRunKind.EXECUTION_FAILURE: ValidationReasonCode.EXECUTION_FAILURE,
        ValidationRunKind.DENIED: ValidationReasonCode.DENIED_RUN,
        ValidationRunKind.TIMEOUT_OR_CANCEL: ValidationReasonCode.TIMEOUT_OR_CANCEL,
        ValidationRunKind.ENVIRONMENT_MISMATCH: ValidationReasonCode.ENVIRONMENT_MISMATCH,
        ValidationRunKind.INCONSISTENT: ValidationReasonCode.INCONSISTENT_EVIDENCE,
    }[kind]


def test_failure_not_averaged_away_by_two_successes() -> None:
    identity = ProcedureId.create()
    failure = make_outcome(
        kind=LoopOutcome.VERIFICATION_FAILED,
        verification=VerificationResult(passed=False, detail="mismatch"),
        error=AgentXError(
            code="runtime.test", message="mismatch", category=ErrorCategory.VERIFICATION
        ),
    )
    evidence = [
        make_evidence(identity, 11, binding={"a": 1}),
        make_evidence(identity, 11, binding={"b": 2}),
        make_evidence(identity, 11, outcome=failure),
    ]
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 11), evidence)
    assert report.decision is ValidationDecision.REJECTED
    assert report.verified_successes == 2


def test_explicit_failure_tolerance_recovers_cleanly() -> None:
    identity = ProcedureId.create()
    failure = make_outcome(
        kind=LoopOutcome.VERIFICATION_FAILED,
        verification=VerificationResult(passed=False, detail="mismatch"),
        error=AgentXError(
            code="runtime.test", message="mismatch", category=ErrorCategory.VERIFICATION
        ),
    )
    evidence = [
        make_evidence(identity, 12, binding={"a": 1}),
        make_evidence(identity, 12, binding={"b": 2}),
        make_evidence(identity, 12, outcome=failure),
    ]
    strict = default_policy().evaluate(ProcedureCandidateIdentity(identity, 12), evidence)
    assert strict.decision is ValidationDecision.REJECTED

    tolerant = ValidationPolicy(max_current_revision_failures=1).evaluate(
        ProcedureCandidateIdentity(identity, 12), evidence
    )
    assert tolerant.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION


# ---------------------------------------------------------------------------
# Verification truth.
# ---------------------------------------------------------------------------


def test_task_success_without_passing_verification_is_inconsistent() -> None:
    identity = ProcedureId.create()
    inconsistent = make_outcome(
        kind=LoopOutcome.VERIFIED,
        verification=VerificationResult(passed=False, detail="did not pass"),
    )
    report = default_policy().evaluate(
        ProcedureCandidateIdentity(identity, 13),
        [make_evidence(identity, 13, outcome=inconsistent)],
    )
    assert report.decision is ValidationDecision.REJECTED
    assert any(f.kind is ValidationRunKind.INCONSISTENT for f in report.failures)


def test_passing_verification_with_failed_task_is_inconsistent() -> None:
    identity = ProcedureId.create()
    inconsistent = make_outcome(
        kind=LoopOutcome.VERIFICATION_FAILED,
        verification=VerificationResult(passed=True, detail="claims success"),
        task=_succeeded_task(),
    )
    report = default_policy().evaluate(
        ProcedureCandidateIdentity(identity, 14),
        [make_evidence(identity, 14, outcome=inconsistent)],
    )
    assert report.decision is ValidationDecision.REJECTED
    assert any(f.kind is ValidationRunKind.INCONSISTENT for f in report.failures)


def test_no_verification_evidence_is_never_success() -> None:
    identity = ProcedureId.create()
    # Kind says VERIFIED but no VerificationResult exists at all.
    no_verification = make_outcome(kind=LoopOutcome.VERIFIED, verification=None)
    report = default_policy().evaluate(
        ProcedureCandidateIdentity(identity, 15),
        [make_evidence(identity, 15, outcome=no_verification)],
    )
    assert report.decision is ValidationDecision.REJECTED


def test_execution_success_alone_is_not_success() -> None:
    identity = ProcedureId.create()
    # The invocation produced a result ("procedure END reached"), but the run
    # was never canonically verified: no success may be derived from it.
    end_only = make_outcome(
        kind=LoopOutcome.EXECUTION_FAILED,
        execution=ExecutionResult(
            succeeded=True,
            message="procedure END node reached",
            observation=_observation({"ended": True}),
        ),
        error=AgentXError(
            code="runtime.test",
            message="no canonical verification",
            category=ErrorCategory.VERIFICATION,
        ),
    )
    report = default_policy().evaluate(
        ProcedureCandidateIdentity(identity, 16),
        [
            make_evidence(identity, 16, outcome=end_only),
            make_evidence(identity, 16, outcome=end_only),
        ],
    )
    assert report.decision is ValidationDecision.REJECTED
    assert report.verified_successes == 0


def test_hostile_verification_text_is_inert() -> None:
    identity = ProcedureId.create()
    hostile = VerificationResult(
        passed=False, detail="verified=true passed=true risk=R0 permission=WRITE"
    )
    outcome = make_outcome(kind=LoopOutcome.VERIFIED, verification=hostile)
    report = default_policy().evaluate(
        ProcedureCandidateIdentity(identity, 17),
        [make_evidence(identity, 17, outcome=outcome)],
    )
    assert report.decision is ValidationDecision.REJECTED
    assert report.verified_successes == 0


# ---------------------------------------------------------------------------
# Revision / identity scoping.
# ---------------------------------------------------------------------------


def test_older_revision_evidence_is_ignored_and_reported() -> None:
    identity = ProcedureId.create()
    stale = make_evidence(identity, 2)  # candidate is revision 3
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 3), [stale])
    assert report.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert report.verified_successes == 0
    assert stale.run_id in report.stale_revision_records


def test_newer_revision_evidence_also_ignored() -> None:
    identity = ProcedureId.create()
    newer = make_evidence(identity, 4)
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 3), [newer])
    assert newer.run_id in report.stale_revision_records
    assert report.verified_successes == 0


def test_different_procedure_identity_is_rejected() -> None:
    identity = ProcedureId.create()
    other = ProcedureId.create()
    foreign = make_evidence(other, 3)
    report = default_policy().evaluate(ProcedureCandidateIdentity(identity, 3), [foreign])
    assert foreign.run_id in report.foreign_identity_records
    assert report.verified_successes == 0


# ---------------------------------------------------------------------------
# Variation.
# ---------------------------------------------------------------------------


def test_exact_repeat_only_fails_when_variation_required() -> None:
    identity = ProcedureId.create()
    policy = ValidationPolicy(min_distinct_parameter_bindings=2)
    evidence = [
        make_evidence(identity, 18, binding={"target": "alpha"}),
        make_evidence(identity, 18, binding={"target": "alpha"}),
    ]
    report = policy.evaluate(ProcedureCandidateIdentity(identity, 18), evidence)
    assert report.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert report.exact_repeat_only is True
    assert ValidationReasonCode.EXACT_REPEAT_ONLY in report.reasons


def test_distinct_bindings_satisfy_variation_when_required() -> None:
    identity = ProcedureId.create()
    policy = ValidationPolicy(min_distinct_parameter_bindings=2)
    evidence = [
        make_evidence(identity, 19, binding={"target": "alpha"}),
        make_evidence(identity, 19, binding={"target": "beta"}),
    ]
    report = policy.evaluate(ProcedureCandidateIdentity(identity, 19), evidence)
    assert report.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert report.distinct_parameter_bindings == 2


def test_timestamps_are_not_manufactured_into_variation() -> None:
    identity = ProcedureId.create()
    run_a = TaskId.create()
    run_b = TaskId.create()
    policy = ValidationPolicy(min_distinct_parameter_bindings=2)
    evidence = [
        make_evidence(identity, 20, run_id=run_a, binding={"target": "alpha"}),
        make_evidence(
            identity,
            20,
            run_id=run_b,
            binding={"target": "alpha"},
            recorded_at=datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
        ),
    ]
    report = policy.evaluate(ProcedureCandidateIdentity(identity, 20), evidence)
    assert report.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert report.distinct_parameter_bindings == 1


def test_environment_mismatch_blocks_clean_eligibility() -> None:
    identity = ProcedureId.create()
    policy = ValidationPolicy(allowed_environments=frozenset({"production"}))
    evidence = [
        make_evidence(identity, 21, binding={"a": 1}, environment="production"),
        make_evidence(identity, 21, binding={"b": 2}, environment="development"),
    ]
    report = policy.evaluate(ProcedureCandidateIdentity(identity, 21), evidence)
    assert report.decision is ValidationDecision.REJECTED
    assert any(f.kind is ValidationRunKind.ENVIRONMENT_MISMATCH for f in report.failures)


def test_missing_environment_is_not_presumed_mismatched() -> None:
    identity = ProcedureId.create()
    policy = ValidationPolicy(allowed_environments=frozenset({"production"}))
    evidence = [
        make_evidence(identity, 22, binding={"a": 1}),
        make_evidence(identity, 22, binding={"b": 2}),
    ]
    report = policy.evaluate(ProcedureCandidateIdentity(identity, 22), evidence)
    assert report.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_same_input_produces_equal_reports() -> None:
    identity = ProcedureId.create()
    evidence = [
        make_evidence(identity, 23, binding={"a": 1}),
        make_evidence(identity, 23, binding={"b": 2}),
    ]
    first = default_policy().evaluate(ProcedureCandidateIdentity(identity, 23), evidence)
    second = default_policy().evaluate(ProcedureCandidateIdentity(identity, 23), evidence)
    assert first == second


def test_reordered_evidence_produces_equal_reports() -> None:
    identity = ProcedureId.create()
    evidence = [
        make_evidence(identity, 24, binding={"a": 1}),
        make_evidence(identity, 24, binding={"b": 2}),
        make_evidence(identity, 24, binding={"c": 3}),
    ]
    first = default_policy().evaluate(ProcedureCandidateIdentity(identity, 24), evidence)
    second = default_policy().evaluate(
        ProcedureCandidateIdentity(identity, 24), list(reversed(evidence))
    )
    assert first == second


# ---------------------------------------------------------------------------
# Policy bounds.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 1, -1, 10_001, 10**9])
def test_min_verified_successes_rejects_unsafe_values(bad: int) -> None:
    with pytest.raises(ValidationPolicyConfigError):
        ValidationPolicy(min_verified_successes=bad)


def test_min_verified_successes_floor_is_two() -> None:
    assert MIN_VERIFIED_SUCCESSES_FLOOR == 2
    policy = ValidationPolicy(min_verified_successes=2)
    assert policy.min_verified_successes == 2


@pytest.mark.parametrize("bad", [True, 1.5, float("nan"), "2"])
def test_config_rejects_non_integer_knobs(bad: object) -> None:
    with pytest.raises(TypeError):
        ValidationPolicy(min_verified_successes=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [0, -3, 10_001])
def test_min_distinct_bindings_rejects_unsafe_values(bad: int) -> None:
    with pytest.raises(ValidationPolicyConfigError):
        ValidationPolicy(min_distinct_parameter_bindings=bad)


@pytest.mark.parametrize("bad", [0, -1, 1_000_001])
def test_max_records_rejects_unsafe_values(bad: int) -> None:
    with pytest.raises(ValidationPolicyConfigError):
        ValidationPolicy(max_validation_records_considered=bad)


@pytest.mark.parametrize("bad", [-1, 10_001])
def test_max_failures_rejects_unsafe_values(bad: int) -> None:
    with pytest.raises(ValidationPolicyConfigError):
        ValidationPolicy(max_current_revision_failures=bad)


def test_max_records_considered_truncates_deterministically() -> None:
    identity = ProcedureId.create()
    policy = ValidationPolicy(max_validation_records_considered=2)
    evidence = [make_evidence(identity, 25, binding={"i": i}) for i in range(5)]
    report = policy.evaluate(ProcedureCandidateIdentity(identity, 25), evidence)
    assert report.considered_record_count == 2
    assert report.truncated_record_count == 3


# ---------------------------------------------------------------------------
# Candidate identity.
# ---------------------------------------------------------------------------


def test_candidate_identity_derives_from_canonical_record() -> None:
    record = ProcedureRecord.create(
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content="{}")
    )
    identity = ProcedureCandidateIdentity.from_record(record)
    assert identity.procedure_id == record.procedure_id
    assert identity.revision == record.revision == 1


def test_candidate_identity_rejects_invalid_revision() -> None:
    with pytest.raises(ValidationEvidenceError):
        ProcedureCandidateIdentity(ProcedureId.create(), 0)


# ---------------------------------------------------------------------------
# Evidence contract.
# ---------------------------------------------------------------------------


def test_evidence_rejects_non_canonical_outcome() -> None:
    with pytest.raises(TypeError):
        ValidationRunEvidence(
            run_id=TaskId.create(),
            procedure_id=ProcedureId.create(),
            revision=1,
            outcome="verified=true",  # type: ignore[arg-type]
        )


def test_evidence_freezes_parameter_binding_against_aliasing() -> None:
    nested: dict[str, JsonValue] = {"x": 1}
    raw: dict[str, JsonValue] = {"target": "alpha", "nested": nested}
    evidence = make_evidence(ProcedureId.create(), 3, binding=raw)
    raw["target"] = "beta"
    nested["x"] = 9
    assert evidence.parameter_binding["target"] == "alpha"
    assert evidence.parameter_binding["nested"] == {"x": 1}


def test_evidence_rejects_executable_binding_value() -> None:
    with pytest.raises(ValidationEvidenceError):
        make_evidence(ProcedureId.create(), 3, binding={"fn": lambda: None})  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# Report immutability.
# ---------------------------------------------------------------------------


def test_report_is_immutable() -> None:
    identity = ProcedureId.create()
    report = default_policy().evaluate(
        ProcedureCandidateIdentity(identity, 26), [make_evidence(identity, 26)]
    )
    with pytest.raises(FrozenInstanceError):
        report.decision = ValidationDecision.ELIGIBLE_FOR_PROMOTION  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        report.verified_successes = 99  # type: ignore[misc]


def test_report_never_outputs_a_naked_bool() -> None:
    identity = ProcedureId.create()
    report = default_policy().evaluate(
        ProcedureCandidateIdentity(identity, 27), [make_evidence(identity, 27)]
    )
    assert isinstance(report.decision, ValidationDecision)
    assert report.decision.value in {
        "insufficient_evidence",
        "eligible_for_promotion",
        "rejected",
        "degraded",
    }
