"""Unit tests for the C4.07 canonical shadow-repair execution-evidence contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from agentx.core.events import VerificationPayload
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedures import ProcedureScope, ProcedureScopeDimension
from agentx.core.shadow_repair import (
    ShadowRepairDeserializationError,
    ShadowRepairDisposition,
    ShadowRepairMode,
    ShadowRepairResult,
    ShadowRepairRunId,
    ShadowRepairStepEvidence,
    ShadowRepairValidationError,
    ShadowRevisionOutcome,
    ShadowStepOutcome,
    UnsupportedShadowRepairSchemaVersionError,
)

_T0 = datetime(2026, 9, 8, 10, 0, 0, 123456, tzinfo=UTC)
_T1 = datetime(2026, 9, 8, 10, 5, 30, 654321, tzinfo=UTC)

_EXPECTED_DISPOSITION_VALUES = (
    "passed",
    "failed",
    "aborted",
    "unsafe_to_evaluate",
    "insufficient_evidence",
)
_EXPECTED_STEP_OUTCOME_VALUES = ("observed", "failed")
_EXPECTED_REVISION_OUTCOME_VALUES = ("passed", "failed")
_EXPECTED_MODE_VALUES = ("non_committing",)

_EXPECTED_RESULT_FIELDS = {
    "schema_version",
    "run_id",
    "procedure_id",
    "source_revision",
    "candidate_revision",
    "candidate_fingerprint",
    "target_node_id",
    "task_id",
    "correlation_id",
    "scope",
    "started_at",
    "ended_at",
    "mode",
    "disposition",
    "verification",
    "steps",
    "original_outcome",
    "regression_node_ids",
    "detail",
}
_EXPECTED_STEP_FIELDS = {
    "order",
    "node_id",
    "outcome",
    "external_effect_observed",
    "effect_contained",
}


def _step(**overrides: object) -> ShadowRepairStepEvidence:
    payload: dict[str, object] = {"order": 1, "node_id": "node-2"}
    payload.update(overrides)
    return ShadowRepairStepEvidence(**payload)  # type: ignore[arg-type]


def _result(**overrides: object) -> ShadowRepairResult:
    payload: dict[str, object] = {
        "run_id": uuid4(),
        "procedure_id": ProcedureId.create(),
        "source_revision": 3,
        "candidate_revision": 4,
        "correlation_id": uuid4(),
        "started_at": _T0,
        "ended_at": _T1,
        "mode": ShadowRepairMode.NON_COMMITTING,
        "disposition": ShadowRepairDisposition.INSUFFICIENT_EVIDENCE,
        "steps": (_step(),),
    }
    payload.update(overrides)
    return ShadowRepairResult(**payload)  # type: ignore[arg-type]


def _passed_result(**overrides: object) -> ShadowRepairResult:
    payload: dict[str, object] = {
        "disposition": ShadowRepairDisposition.PASSED,
        "verification": VerificationPayload(passed=True, detail="objective met"),
    }
    payload.update(overrides)
    return _result(**payload)


class TestVocabulary:
    def test_disposition_vocabulary_is_the_closed_five(self) -> None:
        values = [member.value for member in ShadowRepairDisposition]
        assert values == list(_EXPECTED_DISPOSITION_VALUES)

    def test_step_outcome_vocabulary_is_closed(self) -> None:
        assert [member.value for member in ShadowStepOutcome] == list(_EXPECTED_STEP_OUTCOME_VALUES)

    def test_revision_outcome_vocabulary_is_closed(self) -> None:
        assert [member.value for member in ShadowRevisionOutcome] == list(
            _EXPECTED_REVISION_OUTCOME_VALUES
        )

    def test_mode_vocabulary_is_only_non_committing(self) -> None:
        assert [member.value for member in ShadowRepairMode] == list(_EXPECTED_MODE_VALUES)

    def test_run_identity_is_a_plain_non_nil_uuid(self) -> None:
        # Deliberately no new DomainId type: the run identity is a plain
        # non-nil UUID (the alias is the stdlib type itself).
        assert ShadowRepairRunId is UUID
        result = _result()
        assert isinstance(result.run_id, UUID)
        assert result.run_id.int != 0


class TestValidRecords:
    def test_valid_passed_shadow_record(self) -> None:
        result = _passed_result(
            target_node_id="node-2",
            task_id=TaskId.create(),
            scope=ProcedureScope(dimensions={ProcedureScopeDimension.OPERATING_SYSTEM: "windows"}),
            original_outcome=ShadowRevisionOutcome.FAILED,
            regression_node_ids=("node-2",),
            detail="candidate verified against the original failure objective",
        )

        assert result.is_passed
        assert result.disposition is ShadowRepairDisposition.PASSED
        assert result.verification is not None
        assert result.verification.passed is True
        assert result.candidate_identity == "revision:4"
        assert result.target_node_id == "node-2"
        assert result.task_id is not None
        assert result.scope.value_for(ProcedureScopeDimension.OPERATING_SYSTEM) == "windows"
        assert result.original_outcome is ShadowRevisionOutcome.FAILED
        assert result.regression_node_ids == ("node-2",)

    def test_valid_failed_disposition_with_failing_verification(self) -> None:
        result = _result(
            disposition=ShadowRepairDisposition.FAILED,
            verification=VerificationPayload(passed=False, detail="postcondition not met"),
        )
        assert result.is_passed is False
        assert result.disposition is ShadowRepairDisposition.FAILED

    def test_valid_failed_disposition_with_failed_step(self) -> None:
        result = _result(
            disposition=ShadowRepairDisposition.FAILED,
            steps=(_step(order=1), _step(order=2, outcome=ShadowStepOutcome.FAILED)),
        )
        assert result.disposition is ShadowRepairDisposition.FAILED
        assert result.steps[1].outcome is ShadowStepOutcome.FAILED

    def test_valid_aborted_trial(self) -> None:
        result = _result(disposition=ShadowRepairDisposition.ABORTED)
        assert result.disposition is ShadowRepairDisposition.ABORTED
        assert result.verification is None

    def test_valid_unsafe_trial(self) -> None:
        result = _result(
            disposition=ShadowRepairDisposition.UNSAFE_TO_EVALUATE,
            detail="trial refused: uncontrolled write surface in scope",
        )
        assert result.disposition is ShadowRepairDisposition.UNSAFE_TO_EVALUATE
        assert result.verification is None

    def test_valid_insufficient_evidence_trial(self) -> None:
        result = _result(disposition=ShadowRepairDisposition.INSUFFICIENT_EVIDENCE)
        assert result.disposition is ShadowRepairDisposition.INSUFFICIENT_EVIDENCE
        assert result.verification is None

    def test_valid_trial_with_fingerprint_candidate(self) -> None:
        result = _result(candidate_revision=None, candidate_fingerprint="sha256:abc123")
        assert result.candidate_identity == "fingerprint:sha256:abc123"

    def test_valid_trial_with_empty_steps_and_non_passed_disposition(self) -> None:
        result = _result(disposition=ShadowRepairDisposition.ABORTED, steps=())
        assert result.steps == ()

    def test_passed_trial_with_contained_external_effect_is_allowed(self) -> None:
        result = _passed_result(
            steps=(
                _step(order=1, external_effect_observed=True, effect_contained=True),
                _step(order=2),
            ),
        )
        assert result.is_passed

    def test_passed_trial_may_record_regression_indicators(self) -> None:
        result = _passed_result(
            regression_node_ids=("node-2", "node-7"),
            original_outcome=ShadowRevisionOutcome.FAILED,
        )
        assert result.regression_node_ids == ("node-2", "node-7")
        # The contract preserves the comparison facts; it declares no superiority.
        assert not hasattr(result, "candidate_superior")


class TestVerificationRequirements:
    def test_passed_with_no_verification_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="explicit canonical verification"):
            _passed_result(verification=None)

    def test_passed_with_failing_verification_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="passing verification"):
            _passed_result(verification=VerificationPayload(passed=False))

    def test_passed_requires_at_least_one_step(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="at least one recorded step"):
            _passed_result(steps=())

    def test_passed_with_failed_step_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="failed outcome"):
            _passed_result(steps=(_step(order=1, outcome=ShadowStepOutcome.FAILED),))

    def test_failed_requires_an_explicit_failure_signal(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="explicit failure signal"):
            _result(disposition=ShadowRepairDisposition.FAILED)

    def test_failed_with_passing_verification_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="contradicted by a passing"):
            _result(
                disposition=ShadowRepairDisposition.FAILED,
                verification=VerificationPayload(passed=True),
            )

    def test_aborted_with_verification_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="must not carry verification"):
            _result(
                disposition=ShadowRepairDisposition.ABORTED,
                verification=VerificationPayload(passed=True),
            )

    def test_unsafe_with_verification_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="must not carry verification"):
            _result(
                disposition=ShadowRepairDisposition.UNSAFE_TO_EVALUATE,
                verification=VerificationPayload(passed=False),
            )

    def test_insufficient_with_any_verification_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="must not carry verification"):
            _result(
                disposition=ShadowRepairDisposition.INSUFFICIENT_EVIDENCE,
                verification=VerificationPayload(passed=True),
            )
        with pytest.raises(ShadowRepairValidationError, match="must not carry verification"):
            _result(
                disposition=ShadowRepairDisposition.INSUFFICIENT_EVIDENCE,
                verification=VerificationPayload(passed=False),
            )

    def test_verification_payload_type_is_enforced(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="VerificationPayload"):
            _passed_result(verification={"passed": True})


class TestSideEffectContradictions:
    def test_passed_with_uncontained_external_effect_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="contained/reverted"):
            _passed_result(
                steps=(_step(order=1, external_effect_observed=True),),
            )

    def test_containment_without_an_observed_effect_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="observed external effect"):
            _step(effect_contained=True)

    def test_containment_claim_is_valid_for_an_observed_effect(self) -> None:
        step = _step(external_effect_observed=True, effect_contained=True)
        assert step.effect_contained is True

    def test_non_passed_disposition_may_record_uncontained_effects(self) -> None:
        result = _result(
            disposition=ShadowRepairDisposition.ABORTED,
            steps=(_step(order=1, external_effect_observed=True),),
        )
        assert result.steps[0].external_effect_observed is True
        assert result.steps[0].effect_contained is False


class TestRunBinding:
    def test_nil_run_id_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="run_id"):
            _result(run_id=UUID(int=0))

    def test_nil_correlation_id_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="correlation_id"):
            _result(correlation_id=UUID(int=0))

    def test_exact_procedure_id_binding(self) -> None:
        procedure = ProcedureId.create()
        result = _result(procedure_id=procedure)
        assert result.procedure_id == procedure

        other = ProcedureId.create()
        other_result = ShadowRepairResult.from_dict(
            {**result.to_dict(), "procedure_id": other.to_str()}
        )
        assert other_result.procedure_id == other
        assert other_result.procedure_id != result.procedure_id

    def test_procedure_id_must_be_typed(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="ProcedureId"):
            _result(procedure_id=uuid4())

    def test_source_revision_must_be_positive(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="source_revision"):
            _result(source_revision=0)
        with pytest.raises(ShadowRepairValidationError, match="source_revision"):
            _result(source_revision=-1)
        with pytest.raises(ShadowRepairValidationError, match="source_revision"):
            _result(source_revision=True)

    def test_candidate_identity_exactly_one(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="exactly one candidate identity"):
            _result(candidate_revision=4, candidate_fingerprint="sha256:abc")
        with pytest.raises(ShadowRepairValidationError, match="exactly one candidate identity"):
            _result(candidate_revision=None, candidate_fingerprint=None)

    def test_candidate_revision_must_be_positive(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="candidate_revision"):
            _result(candidate_revision=0)

    def test_candidate_revision_equal_to_source_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="differ from source_revision"):
            _result(source_revision=3, candidate_revision=3)

    def test_candidate_fingerprint_must_be_nonempty_and_bounded(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="candidate_fingerprint"):
            _result(candidate_revision=None, candidate_fingerprint="")
        with pytest.raises(ShadowRepairValidationError, match="candidate_fingerprint"):
            _result(candidate_revision=None, candidate_fingerprint="  ")
        with pytest.raises(ShadowRepairValidationError, match="candidate_fingerprint"):
            _result(candidate_revision=None, candidate_fingerprint="x" * 257)
        ok = _result(candidate_revision=None, candidate_fingerprint="x" * 256)
        assert ok.candidate_fingerprint == "x" * 256

    def test_target_node_id_must_be_nonempty_and_bounded(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="target_node_id"):
            _result(target_node_id="")
        with pytest.raises(ShadowRepairValidationError, match="target_node_id"):
            _result(target_node_id="  node  ")
        with pytest.raises(ShadowRepairValidationError, match="target_node_id"):
            _result(target_node_id="n" * 129)

    def test_scope_must_be_a_procedure_scope(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="ProcedureScope"):
            _result(scope={"os": "windows"})


class TestTimestamps:
    def test_naive_timestamps_are_rejected(self) -> None:
        naive = datetime(2026, 9, 8, 10, 0, 0)
        with pytest.raises(ShadowRepairValidationError, match="timezone-aware"):
            _result(started_at=naive)
        with pytest.raises(ShadowRepairValidationError, match="timezone-aware"):
            _result(ended_at=naive)

    def test_non_datetime_timestamps_are_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="timezone-aware datetime"):
            _result(started_at="2026-09-08T10:00:00Z")

    def test_ended_before_started_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="must not precede"):
            _result(started_at=_T1, ended_at=_T0)

    def test_ended_equal_to_started_is_allowed(self) -> None:
        result = _result(started_at=_T0, ended_at=_T0)
        assert result.ended_at == result.started_at

    def test_non_utc_offsets_are_normalized_to_utc(self) -> None:
        from datetime import timezone

        offset = _T0.astimezone(timezone(timedelta(hours=2)))
        result = _result(started_at=offset, ended_at=_T1)
        assert result.started_at == _T0
        assert result.started_at.utcoffset() == timedelta(0)


class TestSteps:
    def test_ordered_steps_must_be_contiguous_from_one(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="contiguous sequence"):
            _result(steps=(_step(order=2), _step(order=3)))
        with pytest.raises(ShadowRepairValidationError, match="contiguous sequence"):
            _result(steps=(_step(order=1), _step(order=3)))

    def test_duplicate_step_order_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="duplicate order"):
            _result(steps=(_step(order=1), _step(order=1)))

    def test_step_order_must_be_positive(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="order"):
            _step(order=0)
        with pytest.raises(ShadowRepairValidationError, match="order"):
            _step(order=-2)

    def test_bounded_steps(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="at most 64"):
            _result(steps=tuple(_step(order=i) for i in range(1, 66)))
        result = _result(steps=tuple(_step(order=i) for i in range(1, 65)))
        assert len(result.steps) == 64

    def test_steps_must_be_step_evidence(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="ShadowRepairStepEvidence"):
            _result(steps=("not-a-step",))

    def test_step_node_id_must_be_nonempty_and_bounded(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="node_id"):
            _step(node_id="")
        with pytest.raises(ShadowRepairValidationError, match="node_id"):
            _step(node_id="n" * 129)

    def test_step_outcome_must_be_typed(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="ShadowStepOutcome"):
            _step(outcome="observed")

    def test_step_flags_must_be_boolean(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="external_effect_observed"):
            _step(external_effect_observed="true")
        with pytest.raises(ShadowRepairValidationError, match="effect_contained"):
            _step(effect_contained="true")


class TestComparisonFields:
    def test_original_outcome_must_be_typed_or_none(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="ShadowRevisionOutcome"):
            _result(original_outcome="passed")

    def test_regression_node_ids_are_bounded_unique_and_trimmed(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="at most 32"):
            _result(regression_node_ids=tuple(f"node-{i}" for i in range(33)))
        with pytest.raises(ShadowRepairValidationError, match="duplicates"):
            _result(regression_node_ids=("node-1", "node-1"))
        with pytest.raises(ShadowRepairValidationError, match="regression_node_ids entry"):
            _result(regression_node_ids=("node-1", "  "))
        with pytest.raises(ShadowRepairValidationError, match="regression_node_ids entry"):
            _result(regression_node_ids=("n" * 129,))
        result = _result(regression_node_ids=tuple(f"node-{i}" for i in range(32)))
        assert len(result.regression_node_ids) == 32

    def test_regression_ids_must_be_a_tuple(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="tuple"):
            _result(regression_node_ids=["node-1"])

    def test_detail_is_bounded_and_trimmed(self) -> None:
        with pytest.raises(ShadowRepairValidationError, match="detail"):
            _result(detail="x" * 4_097)
        with pytest.raises(ShadowRepairValidationError, match="detail"):
            _result(detail=" padded ")
        result = _result(detail="x" * 4_096)
        assert result.detail == "x" * 4_096


class TestImmutabilityAndEquality:
    def test_result_is_immutable(self) -> None:
        result = _result()
        with pytest.raises(FrozenInstanceError):
            result.disposition = ShadowRepairDisposition.PASSED  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            result.steps = ()  # type: ignore[misc]

    def test_step_is_immutable(self) -> None:
        step = _step()
        with pytest.raises(FrozenInstanceError):
            step.order = 2  # type: ignore[misc]

    def test_nested_step_tuple_is_immutable(self) -> None:
        result = _result()
        with pytest.raises(TypeError):
            result.steps[0] = _step(order=99)  # type: ignore[index]

    def test_deterministic_equality(self) -> None:
        a = _result()
        b = ShadowRepairResult(**_same_kwargs(a))
        assert a == b
        # Identical typed inputs serialize byte-identically. (Records are
        # frozen but intentionally unhashable, like the other scope-bearing
        # canonical records, so equality — not hash — is the determinism proof.)
        assert a.to_json() == b.to_json()


def _same_kwargs(a: ShadowRepairResult) -> dict[str, Any]:
    return {
        "run_id": a.run_id,
        "procedure_id": a.procedure_id,
        "source_revision": a.source_revision,
        "candidate_revision": a.candidate_revision,
        "candidate_fingerprint": a.candidate_fingerprint,
        "target_node_id": a.target_node_id,
        "task_id": a.task_id,
        "correlation_id": a.correlation_id,
        "scope": a.scope,
        "started_at": a.started_at,
        "ended_at": a.ended_at,
        "mode": a.mode,
        "disposition": a.disposition,
        "verification": a.verification,
        "steps": a.steps,
        "original_outcome": a.original_outcome,
        "regression_node_ids": a.regression_node_ids,
        "detail": a.detail,
    }


class TestSerialization:
    def test_to_dict_has_the_canonical_field_set(self) -> None:
        result = _passed_result()
        assert set(result.to_dict()) == _EXPECTED_RESULT_FIELDS

    def test_to_dict_step_fields(self) -> None:
        result = _passed_result()
        step_dicts = cast("list[dict[str, object]]", result.to_dict()["steps"])
        assert set(step_dicts[0]) == _EXPECTED_STEP_FIELDS

    def test_json_round_trip(self) -> None:
        result = _passed_result(
            task_id=TaskId.create(),
            scope=ProcedureScope(dimensions={ProcedureScopeDimension.ENVIRONMENT: "prod"}),
            original_outcome=ShadowRevisionOutcome.PASSED,
            regression_node_ids=("node-2",),
            detail="deterministic",
        )
        restored = ShadowRepairResult.from_json(result.to_json())
        assert restored == result
        assert restored.to_json() == result.to_json()

    def test_json_is_byte_deterministic(self) -> None:
        result = _passed_result()
        assert result.to_json() == ShadowRepairResult.from_json(result.to_json()).to_json()
        parsed = json.loads(result.to_json())
        assert list(parsed) == sorted(parsed)

    def test_dict_round_trip(self) -> None:
        result = _passed_result()
        restored = ShadowRepairResult.from_dict(result.to_dict())
        assert restored == result

    def test_timestamps_round_trip_through_iso8601(self) -> None:
        result = _result()
        raw = result.to_dict()
        assert raw["started_at"] == "2026-09-08T10:00:00.123456Z"
        assert raw["ended_at"] == "2026-09-08T10:05:30.654321Z"

    def test_unknown_fields_are_rejected(self) -> None:
        result = _result()
        raw = dict(result.to_dict())
        raw["safe"] = True
        with pytest.raises(ShadowRepairDeserializationError, match="unknown fields"):
            ShadowRepairResult.from_dict(raw)

    def test_missing_fields_are_rejected(self) -> None:
        result = _result()
        raw = dict(result.to_dict())
        del raw["correlation_id"]
        with pytest.raises(ShadowRepairDeserializationError, match="missing required fields"):
            ShadowRepairResult.from_dict(raw)

    def test_unknown_step_fields_are_rejected(self) -> None:
        result = _result()
        raw = result.to_dict()
        raw["steps"] = [dict(result.steps[0].to_dict(), trusted=True)]
        with pytest.raises(ShadowRepairDeserializationError, match="unknown fields"):
            ShadowRepairResult.from_dict(raw)

    def test_unknown_disposition_value_is_rejected(self) -> None:
        result = _result()
        raw = dict(result.to_dict(), disposition="probably_fine")
        with pytest.raises(ShadowRepairDeserializationError, match="disposition"):
            ShadowRepairResult.from_dict(raw)

    def test_unknown_mode_value_is_rejected(self) -> None:
        result = _result()
        raw = dict(result.to_dict(), mode="os_sandbox")
        with pytest.raises(ShadowRepairDeserializationError, match="mode"):
            ShadowRepairResult.from_dict(raw)

    def test_malformed_verification_record_is_rejected(self) -> None:
        result = _result()
        base = result.to_dict()

        raw = dict(base, verification={"passed": "true"})
        with pytest.raises(ShadowRepairDeserializationError, match="verification"):
            ShadowRepairResult.from_dict(raw)

        raw = dict(base, verification={"trusted": True})
        with pytest.raises(ShadowRepairDeserializationError, match="verification"):
            ShadowRepairResult.from_dict(raw)

        raw = dict(base, verification={"passed": True, "extra": 1})
        with pytest.raises(ShadowRepairDeserializationError, match="verification"):
            ShadowRepairResult.from_dict(raw)

        raw = dict(base, verification="verified")
        with pytest.raises(ShadowRepairDeserializationError, match="verification"):
            ShadowRepairResult.from_dict(raw)

    def test_nil_uuids_are_rejected_in_json(self) -> None:
        result = _result()
        raw = dict(result.to_dict(), run_id="00000000-0000-0000-0000-000000000000")
        with pytest.raises(ShadowRepairDeserializationError, match="run_id"):
            ShadowRepairResult.from_dict(raw)
        raw = dict(result.to_dict(), correlation_id="00000000-0000-0000-0000-000000000000")
        with pytest.raises(ShadowRepairDeserializationError, match="correlation_id"):
            ShadowRepairResult.from_dict(raw)

    def test_unsupported_schema_version_is_rejected(self) -> None:
        result = _result()
        raw = dict(result.to_dict(), schema_version=99)
        with pytest.raises(UnsupportedShadowRepairSchemaVersionError):
            ShadowRepairResult.from_dict(raw)

    def test_malformed_json_is_rejected(self) -> None:
        with pytest.raises(ShadowRepairDeserializationError, match="malformed"):
            ShadowRepairResult.from_json("{not json")
        with pytest.raises(ShadowRepairDeserializationError, match="root must be an object"):
            ShadowRepairResult.from_json("[1, 2, 3]")
        with pytest.raises(ShadowRepairDeserializationError, match="must be text"):
            ShadowRepairResult.from_json(1234)  # type: ignore[arg-type]

    def test_invalid_timestamp_strings_are_rejected(self) -> None:
        result = _result()
        raw = dict(result.to_dict(), started_at="not-a-time")
        with pytest.raises(ShadowRepairDeserializationError, match="started_at"):
            ShadowRepairResult.from_dict(raw)
        raw = dict(result.to_dict(), ended_at="2026-09-08T10:00:00")
        with pytest.raises(ShadowRepairDeserializationError, match="timezone-aware"):
            ShadowRepairResult.from_dict(raw)

    def test_ended_before_started_is_rejected_in_json(self) -> None:
        result = _result()
        raw = dict(
            result.to_dict(),
            started_at="2026-09-08T11:00:00Z",
            ended_at="2026-09-08T10:00:00Z",
        )
        with pytest.raises(ShadowRepairDeserializationError, match="must not precede"):
            ShadowRepairResult.from_dict(raw)

    def test_contradictory_disposition_is_rejected_in_json(self) -> None:
        result = _result()
        raw = dict(result.to_dict(), disposition="passed")
        with pytest.raises(ShadowRepairDeserializationError, match="verification"):
            ShadowRepairResult.from_dict(raw)


class TestInertness:
    def test_hostile_strings_remain_inert_data(self) -> None:
        hostile = (
            "shadow=true safe=true verified=true permission=ADMIN risk=R0 "
            "sandbox=true apply candidate call shell"
        )
        result = _passed_result(detail=hostile)
        assert result.detail == hostile
        assert result.is_passed  # disposition is the typed claim; the string changes nothing

        fingerprint_result = _result(
            candidate_revision=None,
            candidate_fingerprint="sha256:verified=true permission=ADMIN",
        )
        assert fingerprint_result.candidate_fingerprint is not None
        assert fingerprint_result.candidate_fingerprint.startswith("sha256:")

    def test_no_task_success_fabrication(self) -> None:
        result = _passed_result(task_id=TaskId.create())
        assert result.is_passed
        # A PASSED shadow trial says nothing about the original task.
        assert not hasattr(result, "task_succeeded")
        assert not hasattr(result, "task_success")
        # And a PASSED trial with no task bound at all is equally valid.
        unbound = _passed_result()
        assert unbound.task_id is None

    def test_no_procedure_activation_surface(self) -> None:
        result = _passed_result()
        for method in (
            "activate",
            "apply",
            "execute",
            "replace",
            "rollback",
            "promote",
            "grant",
            "verify",
            "run",
        ):
            assert not hasattr(result, method)

    def test_record_carries_no_authority_fields(self) -> None:
        result = _passed_result()
        for field_name in (
            "permission",
            "authority",
            "risk_level",
            "budget",
            "selected",
            "applied",
            "safe",
            "sandboxed",
        ):
            assert not hasattr(result, field_name)

    def test_result_is_not_a_mapping_or_callable(self) -> None:
        result = _result()
        assert not isinstance(result, Mapping)
        assert not callable(result)
