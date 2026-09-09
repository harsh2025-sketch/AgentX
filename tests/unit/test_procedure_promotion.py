"""Unit tests for the N2.10 explicit procedure-promotion transaction.

These pin the deterministic fail-closed decision rules in isolation, on a real
store over a real SQLite database:

- eligible candidate promotion commits exactly one CANDIDATE -> ACTIVE write;
- wrong identity, wrong revision, absent revision, stale expected record;
- negative / insufficient / degraded / foreign validation evidence;
- invalid or non-ALLOWED lifecycle decisions; retired-terminal and
  repeated-promotion (no-op) semantics dictated by canonical policy;
- the compare-and-set seam: a concurrent writer between the read and the
  write loses deterministically and nothing is written;
- history, candidate rows, and validation evidence are never deleted;
- malformed raw-typed requests (booleans, free text, strings) raise instead
  of promoting.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agentx.capabilities.abi import (
    CapabilityObservation,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedure_lifecycle import (
    ProcedureLifecycleAssessment,
    ProcedureLifecycleDecision,
    ProcedureLifecycleReason,
    assess_procedure_transition,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
    ProcedureValidationError,
)
from agentx.core.task_state import try_transition_task
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import (
    ProcedureNotFoundError,
    ProcedureStaleRecordError,
    ProcedureStore,
)
from agentx.kernel.resource_budget import ResourceUsage
from agentx.procedure_promotion import (
    ProcedurePromotionError,
    ProcedurePromotionOutcome,
    ProcedurePromotionRequest,
    ProcedurePromotionResult,
    promote_procedure_candidate,
)
from agentx.procedure_validation import (
    ProcedureCandidateIdentity,
    ValidationDecision,
    ValidationPolicy,
    ValidationReport,
    ValidationRunEvidence,
)

_T0 = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 9, 8, 12, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Canonical builders (the same frozen value types production manufactures).
# ---------------------------------------------------------------------------


def _store(tmp_path: Path) -> ProcedureStore:
    store = ProcedureStore(database=SQLiteDatabase(path=tmp_path / "procedures.sqlite3"))
    assert store.list_records() == ()  # migrate/WAL before any use
    return store


def _candidate(
    store: ProcedureStore,
    *,
    procedure_id: ProcedureId | None = None,
    content: str = '{"steps": []}',
) -> ProcedureRecord:
    record = ProcedureRecord.create(
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content),
        procedure_id=procedure_id,
        revision=1,
        created_at=_T0,
    )
    store.insert(record)
    return record


def _succeeded_task(objective: str) -> Task:
    pending = Task.create(objective=objective)
    running = try_transition_task(pending, TaskStatus.RUNNING).unwrap()
    return try_transition_task(running, TaskStatus.SUCCEEDED).unwrap()


def _verified_outcome(run: int) -> ClosedLoopOutcome:
    observation = CapabilityObservation(summary="step completed", data={"run": run})
    return ClosedLoopOutcome(
        task=_succeeded_task(f"validate the candidate (run {run})"),
        kind=LoopOutcome.VERIFIED,
        error=None,
        execution=ExecutionResult(
            succeeded=True, message="invocation produced a result", observation=observation
        ),
        observation=observation,
        verification=VerificationResult(passed=True, detail="postcondition holds"),
        budget_usage=ResourceUsage.zero(),
    )


def _verification_failed_outcome() -> ClosedLoopOutcome:
    return ClosedLoopOutcome(
        task=_succeeded_task("validate the candidate (failed postcondition)"),
        kind=LoopOutcome.VERIFICATION_FAILED,
        error=AgentXError(
            code="verifier.failed",
            message="postcondition did not hold",
            category=ErrorCategory.VERIFICATION,
        ),
        execution=None,
        observation=None,
        verification=VerificationResult(passed=False, detail="postcondition violated"),
        budget_usage=ResourceUsage.zero(),
    )


def _evidence(
    procedure_id: ProcedureId,
    revision: int,
    *,
    successes: int = 2,
    extra_outcomes: tuple[ClosedLoopOutcome, ...] = (),
) -> tuple[ValidationRunEvidence, ...]:
    records = [
        ValidationRunEvidence(
            run_id=TaskId.create(),
            procedure_id=procedure_id,
            revision=revision,
            outcome=_verified_outcome(index),
            parameter_binding={"path": f"C:/data/file-{index}.txt"},
            recorded_at=_T0,
        )
        for index in range(successes)
    ]
    records.extend(
        ValidationRunEvidence(
            run_id=TaskId.create(),
            procedure_id=procedure_id,
            revision=revision,
            outcome=outcome,
            parameter_binding={"path": f"C:/data/extra-{index}.txt"},
            recorded_at=_T0,
        )
        for index, outcome in enumerate(extra_outcomes)
    )
    return tuple(records)


def _report(
    record: ProcedureRecord,
    *,
    successes: int = 2,
    extra_outcomes: tuple[ClosedLoopOutcome, ...] = (),
) -> ValidationReport:
    policy = ValidationPolicy()
    return policy.evaluate(
        ProcedureCandidateIdentity(procedure_id=record.procedure_id, revision=record.revision),
        _evidence(
            record.procedure_id, record.revision, successes=successes, extra_outcomes=extra_outcomes
        ),
    )


def _assessment(record: ProcedureRecord) -> ProcedureLifecycleAssessment:
    return assess_procedure_transition(
        procedure_id=record.procedure_id,
        revision=record.revision,
        current_status=record.status,
        target_status=ProcedureStatus.ACTIVE,
        reason=ProcedureLifecycleReason.VALIDATION_PROMOTION,
        requested_at=_T1,
    )


def _request(
    record: ProcedureRecord,
    report: ValidationReport,
    *,
    expected_current: ProcedureRecord | None = None,
    assessment: ProcedureLifecycleAssessment | None = None,
) -> ProcedurePromotionRequest:
    """Build a canonical promotion request from canonical decisions."""
    return ProcedurePromotionRequest(
        procedure_id=record.procedure_id,
        revision=record.revision,
        expected_current=record if expected_current is None else expected_current,
        validation_report=report,
        lifecycle_assessment=_assessment(record) if assessment is None else assessment,
        requested_at=_T1,
    )


# ---------------------------------------------------------------------------
# Eligible promotion commits exactly one CANDIDATE -> ACTIVE write.
# ---------------------------------------------------------------------------


def test_eligible_candidate_is_promoted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    result = promote_procedure_candidate(store, _request(record, _report(record)))

    assert result.promoted is True
    assert result.outcome is ProcedurePromotionOutcome.PROMOTED
    assert result.procedure_id == record.procedure_id
    assert result.revision == 1
    assert result.prior_status is ProcedureStatus.CANDIDATE
    assert result.resulting_status is ProcedureStatus.ACTIVE
    assert result.promoted_record is not None
    assert result.promoted_record.status is ProcedureStatus.ACTIVE
    assert result.validation_decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert result.lifecycle_decision is ProcedureLifecycleDecision.ALLOWED
    assert result.explanation == (
        f"promotion committed: procedure {record.procedure_id.to_str()} revision 1 "
        "moved candidate -> active for reason validation_promotion; the active record "
        "remains inert data with zero execution authority"
    )

    stored = store.get(record.procedure_id, 1)
    assert stored is not None
    assert stored.status is ProcedureStatus.ACTIVE
    assert stored.updated_at == _T1  # the explicit requested instant, not a clock read
    assert stored.payload == record.payload
    assert stored.created_at == record.created_at


def test_promotion_is_deterministic_across_identical_runs(tmp_path: Path) -> None:
    store_a = _store(tmp_path / "a")
    store_b = _store(tmp_path / "b")
    record_a = _candidate(store_a)
    record_b = _candidate(store_b)

    result_a = promote_procedure_candidate(store_a, _request(record_a, _report(record_a)))
    result_b = promote_procedure_candidate(store_b, _request(record_b, _report(record_b)))

    assert result_a.procedure_id != result_b.procedure_id
    assert result_a.outcome is result_b.outcome
    assert result_a.prior_status is result_b.prior_status
    assert result_a.resulting_status is result_b.resulting_status
    assert result_a.validation_decision is result_b.validation_decision
    assert result_a.lifecycle_decision is result_b.lifecycle_decision
    assert result_a.explanation.replace(
        result_a.procedure_id.to_str(), "<id>"
    ) == result_b.explanation.replace(result_b.procedure_id.to_str(), "<id>")
    assert result_a.promoted_record is not None and result_b.promoted_record is not None
    assert (
        replace(result_a.promoted_record, procedure_id=result_b.promoted_record.procedure_id)
        == result_b.promoted_record
    )


def test_promoted_record_is_a_new_immutable_snapshot_not_an_inplace_mutation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    result = promote_procedure_candidate(store, _request(record, _report(record)))

    assert record.status is ProcedureStatus.CANDIDATE  # historical object untouched
    assert record.updated_at is None
    assert result.promoted_record is not None
    assert result.promoted_record is not record


# ---------------------------------------------------------------------------
# Identity, revision, and absence fail closed.
# ---------------------------------------------------------------------------


def test_wrong_procedure_id_in_request_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    other_id = ProcedureId.create()
    request = ProcedurePromotionRequest(
        procedure_id=other_id,
        revision=1,
        expected_current=record,
        validation_report=replace(
            _report(record),
            candidate=ProcedureCandidateIdentity(procedure_id=other_id, revision=1),
        ),
        lifecycle_assessment=_assessment(record),
        requested_at=_T1,
    )

    result = promote_procedure_candidate(store, request)

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_IDENTITY_MISMATCH
    assert store.get(record.procedure_id, 1) == record  # untouched


def test_wrong_revision_in_request_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    request = ProcedurePromotionRequest(
        procedure_id=record.procedure_id,
        revision=2,
        expected_current=record,
        validation_report=replace(
            _report(record),
            candidate=ProcedureCandidateIdentity(procedure_id=record.procedure_id, revision=2),
        ),
        lifecycle_assessment=_assessment(record),
        requested_at=_T1,
    )

    result = promote_procedure_candidate(store, request)

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_REVISION_MISMATCH
    assert store.get(record.procedure_id, 1) == record


def test_absent_revision_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    phantom = replace(record, revision=7)  # never stored
    phantom_report = replace(
        _report(record),
        candidate=ProcedureCandidateIdentity(procedure_id=record.procedure_id, revision=7),
    )
    result = promote_procedure_candidate(store, _request(phantom, phantom_report))

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_PROCEDURE_ABSENT
    assert result.prior_status is None
    assert store.get(record.procedure_id, 1) == record  # real revision untouched
    assert store.get(record.procedure_id, 7) is None


# ---------------------------------------------------------------------------
# Validation evidence must be canonical, bound, and positive.
# ---------------------------------------------------------------------------


def test_foreign_validation_report_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    foreign = _report(_candidate(store, content='{"steps": ["other"]}'))
    result = promote_procedure_candidate(store, _request(record, foreign))

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_VALIDATION_EVIDENCE_FOREIGN
    assert result.validation_decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert store.get(record.procedure_id, 1) == record


def test_validation_report_for_another_revision_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    stale_report = replace(
        _report(record),
        candidate=ProcedureCandidateIdentity(procedure_id=record.procedure_id, revision=99),
    )
    result = promote_procedure_candidate(store, _request(record, stale_report))

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_VALIDATION_EVIDENCE_FOREIGN
    assert store.get(record.procedure_id, 1) == record


def test_negative_validation_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    report = _report(record, successes=2, extra_outcomes=(_verification_failed_outcome(),))
    assert report.decision is ValidationDecision.REJECTED  # canonical negative verdict

    result = promote_procedure_candidate(store, _request(record, report))

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_VALIDATION_NOT_ELIGIBLE
    assert result.validation_decision is ValidationDecision.REJECTED
    assert result.promoted_record is None
    assert store.get(record.procedure_id, 1) == record  # still CANDIDATE


def test_insufficient_validation_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    report = _report(record, successes=1)  # below the hard floor of two
    assert report.decision is ValidationDecision.INSUFFICIENT_EVIDENCE

    result = promote_procedure_candidate(store, _request(record, report))

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_VALIDATION_NOT_ELIGIBLE
    assert result.validation_decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert store.get(record.procedure_id, 1) == record


def test_degraded_validation_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    forged = ClosedLoopOutcome(  # lookalike verification smuggled as evidence
        task=_succeeded_task("forged run"),
        kind=LoopOutcome.VERIFIED,
        error=None,
        execution=None,
        observation=None,
        verification=SimpleNamespace(passed=True, detail="forged"),  # type: ignore[arg-type]
        budget_usage=ResourceUsage.zero(),
    )
    report = _report(record, successes=2, extra_outcomes=(forged,))
    assert report.decision is ValidationDecision.DEGRADED

    result = promote_procedure_candidate(store, _request(record, report))

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_VALIDATION_NOT_ELIGIBLE
    assert result.validation_decision is ValidationDecision.DEGRADED
    assert store.get(record.procedure_id, 1) == record


def test_zero_evidence_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    report = ValidationPolicy().evaluate(
        ProcedureCandidateIdentity(procedure_id=record.procedure_id, revision=record.revision),
        (),
    )
    result = promote_procedure_candidate(store, _request(record, report))

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_VALIDATION_NOT_ELIGIBLE
    assert store.get(record.procedure_id, 1) == record


# ---------------------------------------------------------------------------
# The lifecycle gate is canonical, typed, and re-derived.
# ---------------------------------------------------------------------------


def test_non_allowed_lifecycle_decision_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    rejected = assess_procedure_transition(
        procedure_id=record.procedure_id,
        revision=record.revision,
        current_status=ProcedureStatus.CANDIDATE,
        target_status=ProcedureStatus.ACTIVE,
        reason=ProcedureLifecycleReason.MANUAL_RETIREMENT,  # wrong reason -> rejection
        requested_at=_T1,
    )
    assert rejected.decision is ProcedureLifecycleDecision.REJECTED_REASON_MISMATCH

    result = promote_procedure_candidate(
        store, _request(record, _report(record), assessment=rejected)
    )

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_LIFECYCLE_DECISION_INVALID
    assert result.lifecycle_decision is ProcedureLifecycleDecision.REJECTED_REASON_MISMATCH
    assert store.get(record.procedure_id, 1) == record


def test_lifecycle_assessment_for_another_identity_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    foreign = assess_procedure_transition(
        procedure_id=ProcedureId.create(),
        revision=record.revision,
        current_status=ProcedureStatus.CANDIDATE,
        target_status=ProcedureStatus.ACTIVE,
        reason=ProcedureLifecycleReason.VALIDATION_PROMOTION,
        requested_at=_T1,
    )

    result = promote_procedure_candidate(
        store, _request(record, _report(record), assessment=foreign)
    )

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_LIFECYCLE_DECISION_INVALID
    assert store.get(record.procedure_id, 1) == record


def test_lifecycle_assessment_for_a_different_transition_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    backwards = assess_procedure_transition(
        procedure_id=record.procedure_id,
        revision=record.revision,
        current_status=ProcedureStatus.ACTIVE,
        target_status=ProcedureStatus.CANDIDATE,
        reason=ProcedureLifecycleReason.VALIDATION_PROMOTION,
        requested_at=_T1,
    )

    result = promote_procedure_candidate(
        store, _request(record, _report(record), assessment=backwards)
    )

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_LIFECYCLE_DECISION_INVALID
    assert store.get(record.procedure_id, 1) == record


def test_lookalike_lifecycle_assessment_raises_without_writing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    report = _report(record)
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(
            procedure_id=record.procedure_id,
            revision=record.revision,
            expected_current=record,
            validation_report=report,
            lifecycle_assessment=cast("Any", SimpleNamespace(decision="allowed")),
            requested_at=_T1,
        )
    assert store.get(record.procedure_id, 1) == record


# ---------------------------------------------------------------------------
# Stored-state rejections: repeated promotion and terminal retirement.
# ---------------------------------------------------------------------------


def test_repeated_promotion_is_a_typed_no_op_rejection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    first = promote_procedure_candidate(store, _request(record, _report(record)))
    assert first.promoted is True

    active = store.get(record.procedure_id, 1)
    assert active is not None
    fresh_request = _request(record, _report(record), expected_current=active)
    result = promote_procedure_candidate(store, fresh_request)

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_REPEATED_PROMOTION
    assert result.prior_status is ProcedureStatus.ACTIVE
    assert result.lifecycle_decision is ProcedureLifecycleDecision.NO_OP
    assert result.promoted_record is None
    assert store.get(record.procedure_id, 1) == active  # byte-identical, no rewrite


def test_stale_repetition_of_the_original_request_rejects_and_writes_nothing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    request = _request(record, _report(record))
    assert promote_procedure_candidate(store, request).promoted is True
    active = store.get(record.procedure_id, 1)

    replay = promote_procedure_candidate(store, request)  # expected_current says CANDIDATE

    assert replay.outcome is ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD
    assert replay.prior_status is ProcedureStatus.ACTIVE
    assert store.get(record.procedure_id, 1) == active


def test_retired_candidate_is_never_resurrected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    store.update_status(record.procedure_id, 1, ProcedureStatus.RETIRED, updated_at=_T1)
    retired = store.get(record.procedure_id, 1)
    assert retired is not None

    result = promote_procedure_candidate(
        store, _request(record, _report(record), expected_current=retired)
    )

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_RETIRED_TERMINAL
    assert result.prior_status is ProcedureStatus.RETIRED
    assert result.promoted_record is None
    assert store.get(record.procedure_id, 1) == retired  # retirement is monotonic


# ---------------------------------------------------------------------------
# Stale current-record rejection and the compare-and-set race.
# ---------------------------------------------------------------------------


def test_stale_expected_record_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    stale_expected = ProcedureRecord(
        procedure_id=record.procedure_id,
        revision=1,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON, content='{"steps": ["different"]}'
        ),  # differs from what is actually stored
        created_at=record.created_at,
        status=ProcedureStatus.CANDIDATE,
    )
    assert stale_expected != store.get(record.procedure_id, 1)

    result = promote_procedure_candidate(
        store,
        _request(record, _report(record), expected_current=stale_expected),
    )

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD
    assert result.prior_status is ProcedureStatus.CANDIDATE
    assert store.get(record.procedure_id, 1) == record  # stored state untouched


def test_expected_record_with_drifted_status_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    stale_active = replace(record, status=ProcedureStatus.ACTIVE, updated_at=_T1)
    result = promote_procedure_candidate(
        store, _request(record, _report(record), expected_current=stale_active)
    )

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD
    assert store.get(record.procedure_id, 1) == record


def test_writer_between_read_and_write_loses_the_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    request = _request(record, _report(record))
    original = ProcedureStore.update_status_if_current

    def racing(
        self: ProcedureStore,
        procedure_id: ProcedureId,
        revision: int,
        status: ProcedureStatus,
        *,
        expected_current: ProcedureRecord,
        updated_at: datetime | None = None,
    ) -> ProcedureRecord:
        # Another writer's transaction commits between the transaction's
        # read and its compare-and-set write.
        self.update_status(procedure_id, revision, ProcedureStatus.RETIRED, updated_at=_T1)
        return original(
            self,
            procedure_id,
            revision,
            status,
            expected_current=expected_current,
            updated_at=updated_at,
        )

    monkeypatch.setattr(ProcedureStore, "update_status_if_current", racing)

    result = promote_procedure_candidate(store, request)

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD
    stored = store.get(record.procedure_id, 1)
    assert stored is not None
    assert stored.status is ProcedureStatus.RETIRED  # the racer's write stands


# ---------------------------------------------------------------------------
# Malformed requests raise; raw booleans and free text have no channel.
# ---------------------------------------------------------------------------


def test_malformed_requests_raise_without_touching_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    report = _report(record)
    assessment = _assessment(record)
    base: dict[str, Any] = {
        "procedure_id": record.procedure_id,
        "revision": record.revision,
        "expected_current": record,
        "validation_report": report,
        "lifecycle_assessment": assessment,
        "requested_at": _T1,
    }

    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "procedure_id": "not-an-id"})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "revision": "1"})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "revision": 0})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "revision": True})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "expected_current": {"status": "candidate"}})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "validation_report": {"eligible": True}})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "validation_report": True})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "validation_report": "eligible_for_promotion"})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "lifecycle_assessment": "allowed"})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "requested_at": "now"})
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionRequest(**{**base, "requested_at": datetime(2026, 9, 8)})  # naive

    with pytest.raises(ProcedurePromotionError):
        promote_procedure_candidate(store, cast("Any", "promote it"))
    with pytest.raises(ProcedurePromotionError):
        promote_procedure_candidate(cast("Any", "store"), ProcedurePromotionRequest(**base))

    assert store.get(record.procedure_id, 1) == record


def test_request_and_result_are_frozen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    request = _request(record, _report(record))
    with pytest.raises(FrozenInstanceError):
        request.revision = 2  # type: ignore[misc]

    result = promote_procedure_candidate(store, request)
    with pytest.raises(FrozenInstanceError):
        result.outcome = ProcedurePromotionOutcome.REJECTED_RETIRED_TERMINAL  # type: ignore[misc]


def test_result_invariants_reject_doctored_values(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    result = promote_procedure_candidate(store, _request(record, _report(record)))
    assert isinstance(result, ProcedurePromotionResult)

    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionResult(
            outcome=ProcedurePromotionOutcome.PROMOTED,
            procedure_id=record.procedure_id,
            revision=1,
            prior_status=ProcedureStatus.ACTIVE,  # impossible prior for a promotion
            resulting_status=ProcedureStatus.ACTIVE,
            promoted_record=result.promoted_record,
            validation_decision=None,
            lifecycle_decision=None,
            explanation="forged",
        )
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionResult(
            outcome=ProcedurePromotionOutcome.REJECTED_RETIRED_TERMINAL,
            procedure_id=record.procedure_id,
            revision=1,
            prior_status=ProcedureStatus.RETIRED,
            resulting_status=ProcedureStatus.ACTIVE,  # rejections never carry a result
            promoted_record=None,
            validation_decision=None,
            lifecycle_decision=None,
            explanation="forged",
        )
    with pytest.raises(ProcedurePromotionError):
        ProcedurePromotionResult(
            outcome=ProcedurePromotionOutcome.REJECTED_RETIRED_TERMINAL,
            procedure_id=record.procedure_id,
            revision=1,
            prior_status=ProcedureStatus.RETIRED,
            resulting_status=None,
            promoted_record=None,
            validation_decision=None,
            lifecycle_decision=None,
            explanation="   ",  # explanations are always meaningful
        )


# ---------------------------------------------------------------------------
# History, candidates, and evidence are never deleted or rewritten.
# ---------------------------------------------------------------------------


def test_promotion_preserves_history_and_never_deletes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    before = store.list_records()
    assert len(before) == 1

    result = promote_procedure_candidate(store, _request(record, _report(record)))
    assert result.promoted is True

    after = store.list_records()
    assert len(after) == len(before)  # no row deleted, no row added
    assert after[0].procedure_id == before[0].procedure_id
    assert after[0].payload == before[0].payload  # payload byte-identical
    assert after[0].status is ProcedureStatus.ACTIVE
    assert store.history(record.procedure_id) == (after[0],)


def test_rejections_never_delete_candidates_or_rewrite_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    snapshot = store.get(record.procedure_id, 1)
    reports = [
        _report(record, successes=0),  # insufficient (zero evidence)
        _report(record, successes=1),  # insufficient (one success)
    ]
    for report in reports:
        result = promote_procedure_candidate(store, _request(record, report))
        assert result.promoted is False
        assert store.get(record.procedure_id, 1) == snapshot

    assert len(store.list_records()) == 1


def test_validation_evidence_is_never_persisted_into_the_procedure_store(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    evidence = _evidence(record.procedure_id, record.revision, successes=2)
    report = ValidationPolicy().evaluate(
        ProcedureCandidateIdentity(procedure_id=record.procedure_id, revision=record.revision),
        evidence,
    )
    result = promote_procedure_candidate(store, _request(record, report))

    assert result.promoted is True
    assert len(store.list_records()) == 1  # evidence records are not procedure rows
    stored = store.get(record.procedure_id, 1)
    assert stored is not None
    assert stored.payload == record.payload


def test_promotion_imports_no_model_research_or_capability_execution_module(
    tmp_path: Path,
) -> None:
    import sys

    store = _store(tmp_path)
    record = _candidate(store)
    request = _request(record, _report(record))
    watched_prefixes = (
        "agentx.cognition",
        "agentx.capabilities.executor",
        "agentx.capabilities.registry",
        "agentx.learning",
    )
    before = {name for name in sys.modules if name.startswith(watched_prefixes)}

    result = promote_procedure_candidate(store, request)

    after = {name for name in sys.modules if name.startswith(watched_prefixes)}
    assert result.promoted is True
    assert after == before  # promotion never pulls model/research/execution machinery


# ---------------------------------------------------------------------------
# The tiny canonical store seam (compare-and-set) itself.
# ---------------------------------------------------------------------------


def test_store_cas_updates_when_the_stored_record_matches(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)

    updated = store.update_status_if_current(
        record.procedure_id,
        1,
        ProcedureStatus.ACTIVE,
        expected_current=record,
        updated_at=_T1,
    )

    assert updated.status is ProcedureStatus.ACTIVE
    assert updated.updated_at == _T1
    assert store.get(record.procedure_id, 1) == updated


def test_store_cas_refuses_and_writes_nothing_on_mismatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    stale = replace(record, updated_at=_T1)  # the caller's snapshot has drifted

    with pytest.raises(ProcedureStaleRecordError) as excinfo:
        store.update_status_if_current(
            record.procedure_id,
            1,
            ProcedureStatus.ACTIVE,
            expected_current=stale,
            updated_at=_T1,
        )

    assert excinfo.value.procedure_id == record.procedure_id.to_str()
    assert excinfo.value.revision == 1
    assert store.get(record.procedure_id, 1) == record  # nothing written


def test_store_cas_refuses_unknown_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)
    phantom = replace(record, revision=5)

    with pytest.raises(ProcedureNotFoundError):
        store.update_status_if_current(
            record.procedure_id,
            5,
            ProcedureStatus.ACTIVE,
            expected_current=phantom,
            updated_at=_T1,
        )

    assert store.get(record.procedure_id, 1) == record


def test_store_cas_requires_canonical_types(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _candidate(store)

    with pytest.raises(ProcedureValidationError):
        store.update_status_if_current(
            record.procedure_id,
            1,
            "active",  # type: ignore[arg-type]
            expected_current=record,
        )
    with pytest.raises(TypeError):
        store.update_status_if_current(
            record.procedure_id,
            1,
            ProcedureStatus.ACTIVE,
            expected_current={"status": "candidate"},  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        store.update_status_if_current(  # type: ignore[call-arg]
            record.procedure_id,
            1,
            ProcedureStatus.ACTIVE,
        )
