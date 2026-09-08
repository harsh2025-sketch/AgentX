"""Unit tests for the M3.02 procedure-execution trace and evidence contract.

The contract is historical DATA. These tests pin the vocabulary, the
non-collapsible truth model (executed != observed != verified != control
terminated != task verified), the fail-closed validation invariants, the hard
size bounds, and deterministic serialization.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from agentx.core.ids import ArtifactId, EpisodeId, ProcedureId, TaskId
from agentx.core.procedure_execution import (
    MAX_BINDING_SNAPSHOT_BYTES,
    MAX_BINDING_SNAPSHOT_ITEMS,
    MAX_EVIDENCE_REFERENCES,
    MAX_PROCEDURE_RUN_STEPS,
    MAX_RUN_METADATA_ITEMS,
    PROCEDURE_EXECUTION_SCHEMA_VERSION,
    ExecutedEdgeKind,
    ExecutedNodeKind,
    ProcedureEvidenceKind,
    ProcedureExecutionDeserializationError,
    ProcedureExecutionEvidence,
    ProcedureExecutionValidationError,
    ProcedureRunDisposition,
    ProcedureRunRecord,
    ProcedureStepDisposition,
    ProcedureStepRecord,
    ProcedureStepTransition,
    ProcedureTaskVerification,
    UnsupportedProcedureExecutionSchemaVersionError,
)

_T0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(seconds=1)
_T2 = _T0 + timedelta(seconds=2)
_T3 = _T0 + timedelta(seconds=3)
_T4 = _T0 + timedelta(seconds=4)
_T5 = _T0 + timedelta(seconds=5)


def _event_evidence(event_id: UUID | None = None) -> ProcedureExecutionEvidence:
    return ProcedureExecutionEvidence(
        kind=ProcedureEvidenceKind.EVENT,
        event_id=uuid4() if event_id is None else event_id,
    )


def _step(
    *,
    index: int = 0,
    node_id: str = "node-1",
    node_kind: ExecutedNodeKind = ExecutedNodeKind.ACTION,
    disposition: ProcedureStepDisposition = ProcedureStepDisposition.EXECUTED,
    started_at: datetime = _T1,
    ended_at: datetime = _T2,
    **overrides: object,
) -> ProcedureStepRecord:
    payload: dict[str, object] = {
        "step_index": index,
        "node_id": node_id,
        "node_kind": node_kind,
        "disposition": disposition,
        "started_at": started_at,
        "ended_at": ended_at,
    }
    payload.update(overrides)
    return ProcedureStepRecord(**payload)  # type: ignore[arg-type]


def _run(**overrides: object) -> ProcedureRunRecord:
    payload: dict[str, object] = {
        "run_id": uuid4(),
        "procedure_id": ProcedureId.create(),
        "procedure_revision": 1,
        "correlation_id": uuid4(),
        "started_at": _T0,
        "ended_at": _T5,
        "disposition": ProcedureRunDisposition.CANCELLED,
    }
    payload.update(overrides)
    return ProcedureRunRecord(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Valid shapes
# ---------------------------------------------------------------------------


def test_run_with_no_steps_is_valid_history() -> None:
    run = _run()

    assert run.steps == ()
    assert run.schema_version == PROCEDURE_EXECUTION_SCHEMA_VERSION
    assert run.disposition is ProcedureRunDisposition.CANCELLED
    assert run.task_verification is ProcedureTaskVerification.NOT_ASSESSED
    assert run.task_verification_evidence == ()


def test_one_step_run_records_node_identity_kind_and_disposition() -> None:
    step = _step(
        transition=ProcedureStepTransition(
            edge_kind=ExecutedEdgeKind.NEXT, target_node_id="node-2"
        ),
        binding_snapshot={"selector": "#submit", "attempt": 1},
        observation_evidence=(_event_evidence(),),
    )
    run = _run(steps=(step,))

    assert run.step_at(0) is step
    assert step.node_id == "node-1"
    assert step.node_kind is ExecutedNodeKind.ACTION
    assert step.disposition is ProcedureStepDisposition.EXECUTED
    assert step.transition is not None
    assert step.transition.target_node_id == "node-2"
    assert step.binding_snapshot["attempt"] == 1


def test_ordered_multi_step_run_is_accepted_in_index_order() -> None:
    steps = (
        _step(index=0, node_id="a", started_at=_T1, ended_at=_T1),
        _step(
            index=1,
            node_id="b",
            node_kind=ExecutedNodeKind.VERIFY,
            disposition=ProcedureStepDisposition.VERIFIED,
            verification_evidence=(_event_evidence(),),
            started_at=_T2,
            ended_at=_T3,
        ),
        _step(
            index=2,
            node_id="z",
            node_kind=ExecutedNodeKind.END,
            started_at=_T4,
            ended_at=_T4,
        ),
    )
    run = _run(
        steps=steps,
        disposition=ProcedureRunDisposition.REACHED_END,
        control_evidence=(_event_evidence(),),
    )

    assert tuple(step.step_index for step in run.steps) == (0, 1, 2)
    assert run.step_at(2).node_kind is ExecutedNodeKind.END


def test_records_are_immutable_deeply() -> None:
    step = _step(binding_snapshot={"nested": {"value": 1}})
    run = _run(steps=(step,))

    with pytest.raises(FrozenInstanceError):
        step.node_id = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        run.disposition = ProcedureRunDisposition.REACHED_END  # type: ignore[misc]
    with pytest.raises(TypeError):
        step.binding_snapshot["selector"] = "x"  # type: ignore[index]
    nested = step.binding_snapshot["nested"]
    assert isinstance(nested, type(step.binding_snapshot))
    with pytest.raises(TypeError):
        nested["value"] = 2  # type: ignore[index]
    assert isinstance(run.steps, tuple)


def test_equality_is_deterministic_by_value() -> None:
    run_id = uuid4()
    procedure_id = ProcedureId.create()
    correlation_id = uuid4()
    common: dict[str, object] = {
        "run_id": run_id,
        "procedure_id": procedure_id,
        "correlation_id": correlation_id,
        "steps": (_step(),),
    }

    assert _run(**common) == _run(**common)
    assert _run(**common) != _run(**{**common, "procedure_revision": 2})


def test_input_snapshot_is_defensively_copied_from_the_caller_mapping() -> None:
    source: dict[str, object] = {"attempt": 1}
    step = _step(binding_snapshot=source)
    source["attempt"] = 999

    assert step.binding_snapshot["attempt"] == 1


# ---------------------------------------------------------------------------
# Step ordering, indices, timestamps
# ---------------------------------------------------------------------------


def test_negative_step_index_is_rejected() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="must not be negative"):
        _step(index=-1)


def test_duplicate_step_indices_are_rejected() -> None:
    steps = (_step(index=0, node_id="a"), _step(index=0, node_id="b"))
    with pytest.raises(ProcedureExecutionValidationError, match="ordered and contiguous"):
        _run(steps=steps)


def test_out_of_order_steps_are_rejected() -> None:
    steps = (_step(index=1, node_id="a"), _step(index=0, node_id="b"))
    with pytest.raises(ProcedureExecutionValidationError, match="ordered and contiguous"):
        _run(steps=steps)


def test_step_index_gaps_are_rejected() -> None:
    steps = (_step(index=0, node_id="a"), _step(index=2, node_id="b"))
    with pytest.raises(ProcedureExecutionValidationError, match="ordered and contiguous"):
        _run(steps=steps)


def test_steps_recorded_out_of_chronological_order_are_rejected() -> None:
    steps = (
        _step(index=0, node_id="a", started_at=_T3, ended_at=_T3),
        _step(index=1, node_id="b", started_at=_T1, ended_at=_T2),
    )
    with pytest.raises(ProcedureExecutionValidationError, match="before the previous step"):
        _run(steps=steps)


def test_step_outside_the_run_window_is_rejected() -> None:
    late = _step(started_at=_T5 + timedelta(seconds=1), ended_at=_T5 + timedelta(seconds=2))
    with pytest.raises(ProcedureExecutionValidationError, match="outside the run window"):
        _run(steps=(late,))


def test_end_before_start_is_rejected_on_step_and_run() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="must not precede started_at"):
        _step(started_at=_T3, ended_at=_T1)
    with pytest.raises(ProcedureExecutionValidationError, match="must not precede started_at"):
        _run(started_at=_T5, ended_at=_T0)


def test_naive_timestamps_are_rejected() -> None:
    naive = datetime(2026, 9, 8, 10, 0, 0)
    with pytest.raises(ProcedureExecutionValidationError, match="timezone-aware"):
        _step(started_at=naive, ended_at=_T2)
    with pytest.raises(ProcedureExecutionValidationError, match="timezone-aware"):
        _run(started_at=naive)


def test_timestamps_are_normalized_to_utc() -> None:
    offset = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    step = _step(started_at=offset, ended_at=offset)

    assert step.started_at.tzinfo is UTC
    assert step.started_at == datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# The truth model: executed != observed != verified != end != task success
# ---------------------------------------------------------------------------


def test_verified_step_without_verification_evidence_is_rejected() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="require at least one explicit"):
        _step(
            node_kind=ExecutedNodeKind.VERIFY,
            disposition=ProcedureStepDisposition.VERIFIED,
        )


def test_executed_step_may_not_carry_verification_evidence() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="not verification"):
        _step(
            disposition=ProcedureStepDisposition.EXECUTED,
            verification_evidence=(_event_evidence(),),
        )


def test_observation_evidence_does_not_make_a_step_verified() -> None:
    step = _step(observation_evidence=(_event_evidence(), _event_evidence()))

    assert step.disposition is ProcedureStepDisposition.EXECUTED
    assert step.verification_evidence == ()


def test_verification_failure_requires_evidence_or_error_code() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="verification evidence or a"):
        _step(
            node_kind=ExecutedNodeKind.VERIFY,
            disposition=ProcedureStepDisposition.VERIFICATION_FAILED,
        )

    by_evidence = _step(
        node_kind=ExecutedNodeKind.VERIFY,
        disposition=ProcedureStepDisposition.VERIFICATION_FAILED,
        verification_evidence=(_event_evidence(),),
    )
    by_error = _step(
        node_kind=ExecutedNodeKind.VERIFY,
        disposition=ProcedureStepDisposition.VERIFICATION_FAILED,
        error_code="verification.postcondition_not_met",
    )

    assert by_evidence.disposition is ProcedureStepDisposition.VERIFICATION_FAILED
    assert by_error.error_code == "verification.postcondition_not_met"


def test_reaching_end_is_not_task_success() -> None:
    end_step = _step(index=0, node_kind=ExecutedNodeKind.END, started_at=_T4, ended_at=_T4)
    run = _run(
        steps=(end_step,),
        disposition=ProcedureRunDisposition.REACHED_END,
        control_evidence=(_event_evidence(),),
        task_id=TaskId.create(),
    )

    assert run.disposition is ProcedureRunDisposition.REACHED_END
    assert run.task_verification is ProcedureTaskVerification.NOT_ASSESSED
    for forbidden in ("success", "succeeded", "ok", "passed", "verified", "is_success"):
        assert not hasattr(run, forbidden)


def test_all_steps_verified_still_does_not_verify_the_task() -> None:
    steps = (
        _step(
            index=0,
            node_kind=ExecutedNodeKind.VERIFY,
            disposition=ProcedureStepDisposition.VERIFIED,
            verification_evidence=(_event_evidence(),),
            started_at=_T1,
            ended_at=_T2,
        ),
        _step(index=1, node_id="z", node_kind=ExecutedNodeKind.END, started_at=_T4, ended_at=_T4),
    )
    run = _run(
        steps=steps,
        disposition=ProcedureRunDisposition.REACHED_END,
        control_evidence=(_event_evidence(),),
        task_id=TaskId.create(),
    )

    assert run.task_verification is ProcedureTaskVerification.NOT_ASSESSED


def test_run_marked_reached_end_requires_terminal_control_evidence() -> None:
    end_step = _step(node_kind=ExecutedNodeKind.END, started_at=_T4, ended_at=_T4)
    with pytest.raises(ProcedureExecutionValidationError, match="terminal control evidence"):
        _run(steps=(end_step,), disposition=ProcedureRunDisposition.REACHED_END)


def test_run_marked_reached_end_requires_a_final_end_node() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="final recorded step to be an END"):
        _run(
            steps=(_step(),),
            disposition=ProcedureRunDisposition.REACHED_END,
            control_evidence=(_event_evidence(),),
        )
    with pytest.raises(ProcedureExecutionValidationError, match="no recorded steps"):
        _run(
            disposition=ProcedureRunDisposition.REACHED_END,
            control_evidence=(_event_evidence(),),
        )


def test_task_verification_is_represented_separately_and_needs_evidence() -> None:
    task_id = TaskId.create()
    with pytest.raises(ProcedureExecutionValidationError, match="task-verification evidence"):
        _run(task_id=task_id, task_verification=ProcedureTaskVerification.TASK_VERIFIED)

    verified = _run(
        task_id=task_id,
        task_verification=ProcedureTaskVerification.TASK_VERIFIED,
        task_verification_evidence=(_event_evidence(),),
    )

    assert verified.task_verification is ProcedureTaskVerification.TASK_VERIFIED
    assert verified.disposition is ProcedureRunDisposition.CANCELLED


def test_task_verification_requires_a_task_identity() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="requires an explicit task_id"):
        _run(
            task_verification=ProcedureTaskVerification.TASK_VERIFIED,
            task_verification_evidence=(_event_evidence(),),
        )


def test_not_assessed_task_verification_must_not_carry_evidence() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="must not carry task-verification"):
        _run(task_id=TaskId.create(), task_verification_evidence=(_event_evidence(),))


def test_task_verification_failure_is_its_own_value() -> None:
    run = _run(
        task_id=TaskId.create(),
        task_verification=ProcedureTaskVerification.TASK_VERIFICATION_FAILED,
        task_verification_evidence=(_event_evidence(),),
    )

    assert run.task_verification is ProcedureTaskVerification.TASK_VERIFICATION_FAILED


# ---------------------------------------------------------------------------
# Dispositions: cancellation, denial, requirements, branch skipping
# ---------------------------------------------------------------------------


def test_cancelled_step_and_run_are_representable() -> None:
    step = _step(disposition=ProcedureStepDisposition.CANCELLED)
    run = _run(steps=(step,), disposition=ProcedureRunDisposition.CANCELLED)

    assert run.step_at(0).disposition is ProcedureStepDisposition.CANCELLED


def test_denied_step_requires_an_error_code_and_denied_run_ends_on_denial() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="require an explicit canonical"):
        _step(disposition=ProcedureStepDisposition.DENIED)

    denied = _step(disposition=ProcedureStepDisposition.DENIED, error_code="kernel.denied")
    run = _run(steps=(denied,), disposition=ProcedureRunDisposition.DENIED)

    assert run.disposition is ProcedureRunDisposition.DENIED

    with pytest.raises(ProcedureExecutionValidationError, match="end on a DENIED step"):
        _run(steps=(_step(),), disposition=ProcedureRunDisposition.DENIED)


def test_execution_failure_requires_an_error_code_and_halts_the_run() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="require an explicit canonical"):
        _step(disposition=ProcedureStepDisposition.EXECUTION_FAILED)

    failed = _step(
        disposition=ProcedureStepDisposition.EXECUTION_FAILED,
        error_code="capability.execution_failed",
    )
    run = _run(steps=(failed,), disposition=ProcedureRunDisposition.HALTED_ON_STEP_FAILURE)

    assert run.step_at(0).error_code == "capability.execution_failed"

    with pytest.raises(ProcedureExecutionValidationError, match="requires a final step that"):
        _run(steps=(_step(),), disposition=ProcedureRunDisposition.HALTED_ON_STEP_FAILURE)


def test_executed_step_must_not_carry_an_error_code() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="must not carry an error code"):
        _step(error_code="capability.execution_failed")


def test_requirement_dispositions_carry_no_outcome_evidence() -> None:
    for disposition in (
        ProcedureStepDisposition.REASON_REQUIRED,
        ProcedureStepDisposition.RESEARCH_REQUIRED,
        ProcedureStepDisposition.SUBPROCEDURE_REQUIRED,
    ):
        step = _step(disposition=disposition, node_kind=ExecutedNodeKind.REASON)
        assert step.verification_evidence == ()
        with pytest.raises(ProcedureExecutionValidationError, match="not verification"):
            _step(
                disposition=disposition,
                node_kind=ExecutedNodeKind.REASON,
                verification_evidence=(_event_evidence(),),
            )


def test_skipped_by_branch_step_carries_no_observation_or_error() -> None:
    step = _step(
        node_kind=ExecutedNodeKind.BRANCH,
        disposition=ProcedureStepDisposition.SKIPPED_BY_BRANCH,
        started_at=_T1,
        ended_at=_T1,
    )

    assert step.observation_evidence == ()
    with pytest.raises(ProcedureExecutionValidationError, match="must not carry observation"):
        _step(
            disposition=ProcedureStepDisposition.SKIPPED_BY_BRANCH,
            observation_evidence=(_event_evidence(),),
        )


def test_timed_out_step_and_run_are_representable() -> None:
    step = _step(disposition=ProcedureStepDisposition.TIMED_OUT)
    run = _run(steps=(step,), disposition=ProcedureRunDisposition.TIMED_OUT)

    assert run.step_at(0).disposition is ProcedureStepDisposition.TIMED_OUT


# ---------------------------------------------------------------------------
# Evidence references
# ---------------------------------------------------------------------------


def test_each_evidence_kind_binds_to_exactly_one_canonical_reference() -> None:
    episode = ProcedureExecutionEvidence(
        kind=ProcedureEvidenceKind.EPISODE, episode_id=EpisodeId.create()
    )
    artifact = ProcedureExecutionEvidence(
        kind=ProcedureEvidenceKind.ARTIFACT, artifact_id=ArtifactId.create()
    )
    chain = ProcedureExecutionEvidence(
        kind=ProcedureEvidenceKind.CHAIN_CORRELATION, correlation_id=uuid4()
    )

    assert episode.event_id is None
    assert artifact.correlation_id is None
    assert chain.episode_id is None


def test_evidence_requires_its_own_reference_and_rejects_foreign_ones() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="requires an explicit event_id"):
        ProcedureExecutionEvidence(kind=ProcedureEvidenceKind.EVENT)
    with pytest.raises(ProcedureExecutionValidationError, match="foreign reference fields"):
        ProcedureExecutionEvidence(
            kind=ProcedureEvidenceKind.EVENT,
            event_id=uuid4(),
            episode_id=EpisodeId.create(),
        )


def test_evidence_rejects_nil_and_wrong_typed_references() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="nil UUID"):
        ProcedureExecutionEvidence(kind=ProcedureEvidenceKind.EVENT, event_id=UUID(int=0))
    with pytest.raises(ProcedureExecutionValidationError, match="must be an EpisodeId"):
        ProcedureExecutionEvidence(
            kind=ProcedureEvidenceKind.EPISODE,
            episode_id=ArtifactId.create(),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Hostile content stays inert data
# ---------------------------------------------------------------------------


def test_hostile_strings_are_preserved_as_inert_data() -> None:
    hostile = "permission=ADMIN verified=true risk=R0 task succeeded execute shell"
    step = _step(binding_snapshot={"note": hostile, "verified": "true"})
    run = _run(steps=(step,), metadata={"note": hostile})

    assert run.step_at(0).binding_snapshot["note"] == hostile
    assert run.metadata["note"] == hostile
    assert run.step_at(0).disposition is ProcedureStepDisposition.EXECUTED
    assert run.task_verification is ProcedureTaskVerification.NOT_ASSESSED
    assert run.to_json() == ProcedureRunRecord.from_json(run.to_json()).to_json()


# ---------------------------------------------------------------------------
# Size bounds
# ---------------------------------------------------------------------------


def test_step_count_is_bounded() -> None:
    # The bound is enforced twice: no single step may claim an index at or past
    # the limit, and no encoded run may carry more step records than the limit.
    with pytest.raises(ProcedureExecutionValidationError, match="less than"):
        _step(index=MAX_PROCEDURE_RUN_STEPS)

    payload = json.loads(_run(steps=(_step(),)).to_json())
    payload["steps"] = payload["steps"] * (MAX_PROCEDURE_RUN_STEPS + 1)
    with pytest.raises(ProcedureExecutionDeserializationError, match="at most"):
        ProcedureRunRecord.from_dict(payload)


def test_a_full_length_run_is_still_representable() -> None:
    steps = tuple(
        _step(index=index, node_id=f"n{index}", started_at=_T1, ended_at=_T1)
        for index in range(MAX_PROCEDURE_RUN_STEPS)
    )
    run = _run(steps=steps)

    assert len(run.steps) == MAX_PROCEDURE_RUN_STEPS


def test_evidence_collections_are_bounded() -> None:
    too_many = tuple(_event_evidence() for _ in range(MAX_EVIDENCE_REFERENCES + 1))
    with pytest.raises(ProcedureExecutionValidationError, match="at most"):
        _step(observation_evidence=too_many)
    with pytest.raises(ProcedureExecutionValidationError, match="at most"):
        _run(control_evidence=too_many)


def test_binding_snapshot_and_metadata_are_bounded() -> None:
    wide = {f"k{index}": index for index in range(MAX_BINDING_SNAPSHOT_ITEMS + 1)}
    with pytest.raises(ProcedureExecutionValidationError, match="at most"):
        _step(binding_snapshot=wide)

    huge = {"blob": "x" * (MAX_BINDING_SNAPSHOT_BYTES + 1)}
    with pytest.raises(ProcedureExecutionValidationError, match="bytes"):
        _step(binding_snapshot=huge)

    wide_metadata = {f"k{index}": index for index in range(MAX_RUN_METADATA_ITEMS + 1)}
    with pytest.raises(ProcedureExecutionValidationError, match="at most"):
        _run(metadata=wide_metadata)


def test_binding_snapshot_depth_is_bounded() -> None:
    deep: dict[str, object] = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    with pytest.raises(ProcedureExecutionValidationError, match="nesting depth"):
        _step(binding_snapshot=deep)


def test_node_identifier_is_bounded_and_trimmed() -> None:
    with pytest.raises(ProcedureExecutionValidationError, match="at most"):
        _step(node_id="n" * 200)
    with pytest.raises(ProcedureExecutionValidationError, match="non-empty and trimmed"):
        _step(node_id="  padded  ")
    with pytest.raises(ProcedureExecutionValidationError, match="control characters"):
        _step(node_id="node\n1")


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _rich_run() -> ProcedureRunRecord:
    steps = (
        _step(
            index=0,
            node_id="click",
            binding_snapshot={"selector": "#ok", "retries": 0},
            observation_evidence=(_event_evidence(),),
            transition=ProcedureStepTransition(
                edge_kind=ExecutedEdgeKind.NEXT, target_node_id="check"
            ),
            started_at=_T1,
            ended_at=_T2,
        ),
        _step(
            index=1,
            node_id="check",
            node_kind=ExecutedNodeKind.VERIFY,
            disposition=ProcedureStepDisposition.VERIFIED,
            verification_evidence=(
                ProcedureExecutionEvidence(
                    kind=ProcedureEvidenceKind.EPISODE, episode_id=EpisodeId.create()
                ),
            ),
            started_at=_T2,
            ended_at=_T3,
        ),
        _step(
            index=2,
            node_id="done",
            node_kind=ExecutedNodeKind.END,
            started_at=_T4,
            ended_at=_T4,
        ),
    )
    return _run(
        steps=steps,
        task_id=TaskId.create(),
        disposition=ProcedureRunDisposition.REACHED_END,
        control_evidence=(
            ProcedureExecutionEvidence(
                kind=ProcedureEvidenceKind.CHAIN_CORRELATION, correlation_id=uuid4()
            ),
        ),
        task_verification=ProcedureTaskVerification.TASK_VERIFIED,
        task_verification_evidence=(_event_evidence(),),
        metadata={"origin": "unit-test"},
    )


def test_round_trip_is_deterministic_and_lossless() -> None:
    run = _rich_run()
    encoded = run.to_json()

    assert encoded == run.to_json()
    assert ProcedureRunRecord.from_json(encoded) == run
    assert ProcedureRunRecord.from_json(encoded).to_json() == encoded


def test_serialized_keys_are_sorted_and_schema_versioned() -> None:
    run = _rich_run()
    payload = json.loads(run.to_json())

    assert list(payload) == sorted(payload)
    assert payload["schema_version"] == PROCEDURE_EXECUTION_SCHEMA_VERSION
    assert payload["task_verification"] == "task_verified"
    assert payload["disposition"] == "reached_end"


def test_step_round_trip_is_deterministic() -> None:
    step = _step(
        binding_snapshot={"z": 1, "a": {"b": [1, 2, 3]}},
        observation_evidence=(_event_evidence(),),
    )

    assert ProcedureStepRecord.from_json(step.to_json()) == step


def test_unknown_fields_are_rejected() -> None:
    payload = json.loads(_rich_run().to_json())
    payload["authority"] = "granted"

    with pytest.raises(ProcedureExecutionDeserializationError, match="unknown fields"):
        ProcedureRunRecord.from_dict(payload)


def test_missing_fields_are_rejected() -> None:
    payload = json.loads(_rich_run().to_json())
    del payload["control_evidence"]

    with pytest.raises(ProcedureExecutionDeserializationError, match="missing required fields"):
        ProcedureRunRecord.from_dict(payload)


def test_malformed_json_is_rejected() -> None:
    with pytest.raises(ProcedureExecutionDeserializationError, match="malformed"):
        ProcedureRunRecord.from_json("{not json")
    with pytest.raises(ProcedureExecutionDeserializationError, match="root must be an object"):
        ProcedureRunRecord.from_json("[]")
    with pytest.raises(ProcedureExecutionDeserializationError, match="must be a string"):
        ProcedureRunRecord.from_json(b"{}")  # type: ignore[arg-type]


def test_unknown_enum_values_are_rejected() -> None:
    payload = json.loads(_rich_run().to_json())
    payload["disposition"] = "reached_end_and_authorized"

    with pytest.raises(ProcedureExecutionDeserializationError, match="unknown run disposition"):
        ProcedureRunRecord.from_dict(payload)

    payload = json.loads(_rich_run().to_json())
    payload["steps"][0]["disposition"] = "definitely_verified"
    with pytest.raises(ProcedureExecutionDeserializationError, match="unknown step disposition"):
        ProcedureRunRecord.from_dict(payload)


def test_unsupported_schema_version_is_rejected() -> None:
    payload = json.loads(_rich_run().to_json())
    payload["schema_version"] = PROCEDURE_EXECUTION_SCHEMA_VERSION + 1

    with pytest.raises(UnsupportedProcedureExecutionSchemaVersionError):
        ProcedureRunRecord.from_dict(payload)


def test_contradictory_encoded_records_fail_closed_on_decode() -> None:
    payload = json.loads(_rich_run().to_json())
    payload["steps"][1]["verification_evidence"] = []

    with pytest.raises(ProcedureExecutionDeserializationError, match="step is invalid"):
        ProcedureRunRecord.from_dict(payload)


def test_encoded_task_verification_without_evidence_fails_closed() -> None:
    payload = json.loads(_rich_run().to_json())
    payload["task_verification_evidence"] = []

    with pytest.raises(ProcedureExecutionDeserializationError, match="procedure run is invalid"):
        ProcedureRunRecord.from_dict(payload)
