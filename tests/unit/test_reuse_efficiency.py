from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from agentx.core.events import VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.reuse_efficiency import (
    EfficiencyMetric,
    ExecutionEfficiencyEvidence,
    ExecutionEvidenceOutcome,
    MetricChange,
    MetricComparison,
    ProcedureRevisionRef,
    ReuseComparison,
    ReuseEfficiencyDeserializationError,
    ReuseEfficiencyDisposition,
    ReuseEfficiencyValidationError,
    ReuseMode,
    TaskRelationship,
    TaskRelationshipEvidence,
    compare_execution_efficiency,
)

_START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _run(
    *,
    task_id: TaskId,
    mode: ReuseMode,
    outcome: ExecutionEvidenceOutcome = ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
    model_calls: int | None = 4,
    model_tokens: int | None = 1_000,
    elapsed: timedelta | None = timedelta(seconds=10),
    cost: Decimal | None = Decimal("1.25"),
    research_queries: int | None = 3,
    machine_actions: int | None = 5,
    repair_attempts: int | None = 1,
    procedure: ProcedureRevisionRef | None = None,
    evidence_reference: UUID | None = None,
    episode_id: EpisodeId | None = None,
    correlation_id: UUID | None = None,
    verification_detail: str = "canonical verifier passed",
) -> ExecutionEfficiencyEvidence:
    if mode in {ReuseMode.PROCEDURE_REUSE, ReuseMode.GUIDED_PROCEDURE} and procedure is None:
        procedure = ProcedureRevisionRef(ProcedureId.create(), 3)
    verification = (
        VerificationPayload(passed=True, detail=verification_detail)
        if outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS
        else None
    )
    return ExecutionEfficiencyEvidence(
        task_id=task_id,
        episode_id=EpisodeId.create() if episode_id is None else episode_id,
        correlation_id=uuid4() if correlation_id is None else correlation_id,
        mode=mode,
        outcome=outcome,
        evidence_source="tests.instrumentation",
        evidence_reference=uuid4() if evidence_reference is None else evidence_reference,
        elapsed=elapsed,
        model_calls=model_calls,
        model_tokens=model_tokens,
        research_queries=research_queries,
        machine_actions=machine_actions,
        repair_attempts=repair_attempts,
        external_cost=cost,
        cost_unit=None if cost is None else "USD",
        procedure=procedure,
        verification=verification,
        verification_source=None if verification is None else "tests.verifier",
        verification_reference=None if verification is None else uuid4(),
    )


def _metric(comparison: ReuseComparison, metric: EfficiencyMetric) -> MetricComparison:
    found = comparison.metric(metric)
    assert found is not None
    return found


def test_verified_novel_to_procedure_reuse_improves_all_primary_metrics() -> None:
    task_id = TaskId.create()
    baseline = _run(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    reuse = _run(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        model_calls=1,
        model_tokens=200,
        elapsed=timedelta(seconds=2),
        cost=Decimal("0.20"),
        research_queries=0,
        machine_actions=3,
        repair_attempts=0,
    )

    comparison = compare_execution_efficiency(baseline, reuse)

    assert comparison.disposition is ReuseEfficiencyDisposition.IMPROVED
    assert _metric(comparison, EfficiencyMetric.MODEL_CALLS).delta == Decimal("-3")
    assert _metric(comparison, EfficiencyMetric.MODEL_TOKENS).delta == Decimal("-800")
    assert _metric(comparison, EfficiencyMetric.DURATION_MICROSECONDS).delta == Decimal(
        "-8000000"
    )
    assert _metric(comparison, EfficiencyMetric.EXTERNAL_COST).delta == Decimal("-1.05")
    assert _metric(comparison, EfficiencyMetric.RESEARCH_QUERIES).change is MetricChange.IMPROVED
    assert _metric(comparison, EfficiencyMetric.MACHINE_ACTIONS).change is MetricChange.IMPROVED
    assert _metric(comparison, EfficiencyMetric.REPAIR_ATTEMPTS).change is MetricChange.IMPROVED


def test_cache_reuse_and_deterministic_capability_are_closed_modes() -> None:
    assert ReuseMode.CACHE_REUSE.value == "cache_reuse"
    assert ReuseMode.DETERMINISTIC_CAPABILITY.value == "deterministic_capability"
    assert ReuseMode.EXPLORATORY.value == "exploratory"


def test_same_cost_is_no_material_improvement_when_it_is_the_only_common_metric() -> None:
    task_id = TaskId.create()
    baseline = _run(
        task_id=task_id,
        mode=ReuseMode.NOVEL_PLAN,
        model_calls=None,
        model_tokens=None,
        elapsed=None,
        research_queries=None,
        machine_actions=None,
        repair_attempts=None,
        cost=Decimal("1.00"),
    )
    reuse = _run(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        model_calls=None,
        model_tokens=None,
        elapsed=None,
        research_queries=None,
        machine_actions=None,
        repair_attempts=None,
        cost=Decimal("1.00"),
    )

    comparison = compare_execution_efficiency(baseline, reuse)
    assert comparison.disposition is ReuseEfficiencyDisposition.NO_MATERIAL_IMPROVEMENT
    assert _metric(comparison, EfficiencyMetric.EXTERNAL_COST).change is MetricChange.SAME


def test_higher_cost_regresses_even_when_other_measured_metrics_are_lower() -> None:
    task_id = TaskId.create()
    baseline = _run(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, cost=Decimal("1"))
    reuse = _run(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        model_calls=1,
        model_tokens=100,
        cost=Decimal("2"),
    )
    assert (
        compare_execution_efficiency(baseline, reuse).disposition
        is ReuseEfficiencyDisposition.REGRESSED
    )


@pytest.mark.parametrize(
    "reuse_outcome",
    [ExecutionEvidenceOutcome.FAILED, ExecutionEvidenceOutcome.UNVERIFIED],
)
def test_verified_baseline_to_failed_or_unverified_reuse_is_regressed(
    reuse_outcome: ExecutionEvidenceOutcome,
) -> None:
    task_id = TaskId.create()
    baseline = _run(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    reuse = _run(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        outcome=reuse_outcome,
        model_calls=0,
        model_tokens=0,
        elapsed=timedelta(0),
        cost=Decimal("0"),
        research_queries=0,
        machine_actions=0,
        repair_attempts=0,
    )
    comparison = compare_execution_efficiency(baseline, reuse)
    assert comparison.disposition is ReuseEfficiencyDisposition.REGRESSED
    assert any("lost verified success" in reason for reason in comparison.reasons)


def test_zero_baseline_denominator_makes_ratio_unavailable_without_nan_or_infinity() -> None:
    task_id = TaskId.create()
    baseline = _run(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, model_calls=0)
    reuse = _run(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=0)
    metric = _metric(compare_execution_efficiency(baseline, reuse), EfficiencyMetric.MODEL_CALLS)
    assert metric.ratio is None
    encoded = metric.to_dict()
    assert encoded["ratio"] is None
    assert "NaN" not in json.dumps(encoded)
    assert "Infinity" not in json.dumps(encoded)


def test_different_task_ids_without_relationship_are_not_comparable() -> None:
    baseline = _run(task_id=TaskId.create(), mode=ReuseMode.NOVEL_PLAN)
    reuse = _run(task_id=TaskId.create(), mode=ReuseMode.PROCEDURE_REUSE)
    comparison = compare_execution_efficiency(baseline, reuse)
    assert comparison.disposition is ReuseEfficiencyDisposition.NOT_COMPARABLE


def test_explicit_related_task_evidence_allows_comparison() -> None:
    baseline = _run(task_id=TaskId.create(), mode=ReuseMode.NOVEL_PLAN)
    reuse = _run(
        task_id=TaskId.create(),
        mode=ReuseMode.PROCEDURE_REUSE,
        model_calls=1,
        model_tokens=100,
        elapsed=timedelta(seconds=1),
        cost=Decimal("0.10"),
        research_queries=0,
        machine_actions=2,
        repair_attempts=0,
    )
    relation = TaskRelationshipEvidence(
        relationship=TaskRelationship.RELATED_TASK_REUSE,
        source="tests.task_relation",
        reference=uuid4(),
    )
    comparison = compare_execution_efficiency(baseline, reuse, relationship=relation)
    assert comparison.disposition is ReuseEfficiencyDisposition.IMPROVED


def test_same_task_replay_is_established_by_typed_task_identity() -> None:
    task_id = TaskId.create()
    comparison = compare_execution_efficiency(
        _run(task_id=task_id, mode=ReuseMode.NOVEL_PLAN),
        _run(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1),
    )
    assert comparison.disposition is ReuseEfficiencyDisposition.IMPROVED
    assert comparison.reasons[0] == "same TaskId establishes same-task replay"


def test_procedure_reuse_requires_exact_positive_revision_binding() -> None:
    procedure_id = ProcedureId.create()
    ref = ProcedureRevisionRef(procedure_id=procedure_id, revision=3)
    run = _run(task_id=TaskId.create(), mode=ReuseMode.PROCEDURE_REUSE, procedure=ref)
    assert run.procedure == ref
    assert run.procedure.revision == 3
    with pytest.raises(ReuseEfficiencyValidationError):
        ProcedureRevisionRef(procedure_id=procedure_id, revision=0)
    with pytest.raises(TypeError):
        ProcedureRevisionRef(procedure_id=procedure_id, revision=True)  # type: ignore[arg-type]


def test_negative_count_is_rejected() -> None:
    with pytest.raises(ReuseEfficiencyValidationError):
        _run(task_id=TaskId.create(), mode=ReuseMode.NOVEL_PLAN, model_calls=-1)


def test_bool_count_and_overflow_count_are_rejected() -> None:
    with pytest.raises(TypeError):
        _run(
            task_id=TaskId.create(),
            mode=ReuseMode.NOVEL_PLAN,
            model_calls=True,  # type: ignore[arg-type]
        )
    with pytest.raises(OverflowError):
        _run(task_id=TaskId.create(), mode=ReuseMode.NOVEL_PLAN, model_calls=1 << 63)


@pytest.mark.parametrize("cost", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_cost_is_rejected(cost: Decimal) -> None:
    with pytest.raises(ReuseEfficiencyValidationError):
        _run(task_id=TaskId.create(), mode=ReuseMode.NOVEL_PLAN, cost=cost)


def test_invalid_timestamp_order_and_elapsed_mismatch_are_rejected() -> None:
    task_id = TaskId.create()
    episode_id = EpisodeId.create()
    correlation_id = uuid4()
    evidence_reference = uuid4()
    verification = VerificationPayload(passed=True, detail="ok")
    verification_reference = uuid4()
    with pytest.raises(ReuseEfficiencyValidationError):
        ExecutionEfficiencyEvidence(
            task_id=task_id,
            episode_id=episode_id,
            correlation_id=correlation_id,
            mode=ReuseMode.NOVEL_PLAN,
            outcome=ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
            evidence_source="tests.instrumentation",
            evidence_reference=evidence_reference,
            started_at=_START,
            ended_at=_START - timedelta(seconds=1),
            verification=verification,
            verification_source="tests.verifier",
            verification_reference=verification_reference,
        )
    with pytest.raises(ReuseEfficiencyValidationError):
        ExecutionEfficiencyEvidence(
            task_id=task_id,
            episode_id=EpisodeId.create(),
            correlation_id=uuid4(),
            mode=ReuseMode.NOVEL_PLAN,
            outcome=ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
            evidence_source="tests.instrumentation",
            evidence_reference=uuid4(),
            started_at=_START,
            ended_at=_START + timedelta(seconds=1),
            elapsed=timedelta(seconds=2),
            verification=verification,
            verification_source="tests.verifier",
            verification_reference=uuid4(),
        )


def test_token_total_is_derived_exactly_and_contradictory_total_is_rejected() -> None:
    task_id = TaskId.create()
    run = ExecutionEfficiencyEvidence(
        task_id=task_id,
        episode_id=EpisodeId.create(),
        correlation_id=uuid4(),
        mode=ReuseMode.NOVEL_PLAN,
        outcome=ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        evidence_source="tests.instrumentation",
        evidence_reference=uuid4(),
        model_input_tokens=10,
        model_output_tokens=7,
        verification=VerificationPayload(passed=True, detail="ok"),
        verification_source="tests.verifier",
        verification_reference=uuid4(),
    )
    assert run.model_tokens == 17
    with pytest.raises(ReuseEfficiencyValidationError):
        ExecutionEfficiencyEvidence(
            task_id=task_id,
            episode_id=EpisodeId.create(),
            correlation_id=uuid4(),
            mode=ReuseMode.NOVEL_PLAN,
            outcome=ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
            evidence_source="tests.instrumentation",
            evidence_reference=uuid4(),
            model_input_tokens=10,
            model_output_tokens=7,
            model_tokens=99,
            verification=VerificationPayload(passed=True, detail="ok"),
            verification_source="tests.verifier",
            verification_reference=uuid4(),
        )


def test_records_are_immutable_and_serialization_round_trips_deterministically() -> None:
    task_id = TaskId.create()
    run = _run(task_id=task_id, mode=ReuseMode.PROCEDURE_REUSE)
    with pytest.raises(FrozenInstanceError):
        run.model_calls = 0  # type: ignore[misc]
    assert ExecutionEfficiencyEvidence.from_json(run.to_json()) == run
    comparison = compare_execution_efficiency(
        _run(task_id=task_id, mode=ReuseMode.NOVEL_PLAN), run
    )
    assert ReuseComparison.from_json(comparison.to_json()) == comparison
    assert comparison.to_json() == ReuseComparison.from_json(comparison.to_json()).to_json()


def test_unknown_fields_are_rejected() -> None:
    run = _run(task_id=TaskId.create(), mode=ReuseMode.NOVEL_PLAN)
    raw = run.to_dict()
    raw["permission"] = "ADMIN"
    with pytest.raises(ReuseEfficiencyDeserializationError):
        ExecutionEfficiencyEvidence.from_dict(raw)


def test_no_opaque_single_score_exists() -> None:
    task_id = TaskId.create()
    comparison = compare_execution_efficiency(
        _run(task_id=task_id, mode=ReuseMode.NOVEL_PLAN),
        _run(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1),
    )
    assert not hasattr(comparison, "score")


def test_distinct_runs_cannot_reuse_same_evidence_reference_or_run_identity() -> None:
    task_id = TaskId.create()
    same_ref = uuid4()
    first = _run(
        task_id=task_id,
        mode=ReuseMode.NOVEL_PLAN,
        evidence_reference=same_ref,
    )
    second = _run(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        evidence_reference=same_ref,
    )
    with pytest.raises(ReuseEfficiencyValidationError):
        compare_execution_efficiency(first, second)

    episode_id = EpisodeId.create()
    correlation_id = uuid4()
    first = _run(
        task_id=task_id,
        mode=ReuseMode.NOVEL_PLAN,
        episode_id=episode_id,
        correlation_id=correlation_id,
    )
    second = _run(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        episode_id=episode_id,
        correlation_id=correlation_id,
    )
    with pytest.raises(ReuseEfficiencyValidationError):
        compare_execution_efficiency(first, second)
