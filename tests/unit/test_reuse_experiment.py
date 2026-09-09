"""Unit tests for the N2.12 cold-vs-warm verified-reuse experiment harness.

Every experiment below uses deterministic scripted runners: no clock is read,
nothing sleeps, no model or network call happens, and every measurement is
explicit canonical M8.01 evidence supplied by the injected port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any
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
    ReuseEfficiencyDisposition,
    ReuseMode,
    TaskRelationship,
    TaskRelationshipEvidence,
)
from agentx.reuse_experiment import (
    COLD_NO_REUSE_MODES,
    REUSE_EXPERIMENT_SCHEMA_VERSION,
    WARM_REUSE_MODES,
    ExperimentPhase,
    PhaseEvidenceError,
    PhaseExecutionRequest,
    PhaseRunner,
    ReuseExperimentDeserializationError,
    ReuseExperimentError,
    ReuseExperimentHarness,
    ReuseExperimentResult,
    ReuseExperimentSpec,
    ReuseExperimentVerdict,
    UnsupportedReuseExperimentSchemaVersionError,
    classify_reuse_mode,
)


def _evidence(
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
    verification_detail: str = "canonical verifier passed",
    evidence_source: str = "tests.instrumentation",
) -> ExecutionEfficiencyEvidence:
    if mode in {ReuseMode.PROCEDURE_REUSE, ReuseMode.GUIDED_PROCEDURE} and procedure is None:
        procedure = ProcedureRevisionRef(ProcedureId.create(), 3)
    if mode not in {ReuseMode.PROCEDURE_REUSE, ReuseMode.GUIDED_PROCEDURE}:
        procedure = None
    verification = (
        VerificationPayload(passed=True, detail=verification_detail)
        if outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS
        else (
            VerificationPayload(passed=False, detail=verification_detail)
            if outcome is ExecutionEvidenceOutcome.FAILED
            else None
        )
    )
    return ExecutionEfficiencyEvidence(
        task_id=task_id,
        episode_id=EpisodeId.create(),
        correlation_id=uuid4(),
        mode=mode,
        outcome=outcome,
        evidence_source=evidence_source,
        evidence_reference=uuid4(),
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


@dataclass
class ScriptedRunner:
    """Deterministic injected execution port used by every test."""

    evidence: ExecutionEfficiencyEvidence
    requests: list[PhaseExecutionRequest] = field(default_factory=list)

    def run_phase(self, request: PhaseExecutionRequest) -> ExecutionEfficiencyEvidence:
        self.requests.append(request)
        return self.evidence


@dataclass
class RaisingRunner:
    """Port whose execution fails; the harness must not fabricate evidence."""

    error: Exception
    requests: list[PhaseExecutionRequest] = field(default_factory=list)

    def run_phase(self, request: PhaseExecutionRequest) -> ExecutionEfficiencyEvidence:
        self.requests.append(request)
        raise self.error


@dataclass
class GarbageRunner:
    """Port returning untyped data that must never be parsed as measurement."""

    payload: object
    requests: list[PhaseExecutionRequest] = field(default_factory=list)

    def run_phase(self, request: PhaseExecutionRequest) -> Any:
        self.requests.append(request)
        return self.payload


def _harness(
    cold: ExecutionEfficiencyEvidence, warm: ExecutionEfficiencyEvidence
) -> tuple[ReuseExperimentHarness, ScriptedRunner, ScriptedRunner]:
    cold_runner = ScriptedRunner(cold)
    warm_runner = ScriptedRunner(warm)
    return (
        ReuseExperimentHarness(cold_runner=cold_runner, warm_runner=warm_runner),
        cold_runner,
        warm_runner,
    )


def _spec(
    *,
    cold_task_id: TaskId,
    warm_task_id: TaskId,
    relationship: TaskRelationshipEvidence | None = None,
    expected_warm_procedure: ProcedureRevisionRef | None = None,
) -> ReuseExperimentSpec:
    return ReuseExperimentSpec(
        experiment_id=uuid4(),
        cold_task_id=cold_task_id,
        warm_task_id=warm_task_id,
        relationship=relationship,
        expected_warm_procedure=expected_warm_procedure,
    )


def _relationship(
    relationship: TaskRelationship = TaskRelationship.RELATED_TASK_REUSE,
) -> TaskRelationshipEvidence:
    return TaskRelationshipEvidence(
        relationship=relationship,
        source="tests.relationship",
        reference=uuid4(),
    )


def _run(
    cold: ExecutionEfficiencyEvidence,
    warm: ExecutionEfficiencyEvidence,
    spec: ReuseExperimentSpec,
) -> ReuseExperimentResult:
    harness, _cold_runner, _warm_runner = _harness(cold, warm)
    return harness.run(spec)


def _comparison(result: ReuseExperimentResult) -> ReuseComparison:
    assert result.comparison is not None
    return result.comparison


def _metric(result: ReuseExperimentResult, name: EfficiencyMetric) -> MetricComparison:
    found = result.metric_comparison(name)
    assert found is not None
    return found


# ---------------------------------------------------------------------------
# Cold/warm mode classification (closed, total, typed-only).
# ---------------------------------------------------------------------------


def test_mode_classification_partitions_the_canonical_vocabulary() -> None:
    assert set(ReuseMode) == WARM_REUSE_MODES | COLD_NO_REUSE_MODES
    assert not (WARM_REUSE_MODES & COLD_NO_REUSE_MODES)
    assert (
        frozenset({ReuseMode.CACHE_REUSE, ReuseMode.PROCEDURE_REUSE, ReuseMode.GUIDED_PROCEDURE})
        == WARM_REUSE_MODES
    )
    assert (
        frozenset(
            {
                ReuseMode.DETERMINISTIC_CAPABILITY,
                ReuseMode.NOVEL_PLAN,
                ReuseMode.EXPLORATORY,
            }
        )
        == COLD_NO_REUSE_MODES
    )


@pytest.mark.parametrize("mode", list(ReuseMode))
def test_classify_reuse_mode_is_total_and_typed(mode: ReuseMode) -> None:
    expected = ExperimentPhase.WARM if mode in WARM_REUSE_MODES else ExperimentPhase.COLD
    assert classify_reuse_mode(mode) is expected


def test_classify_reuse_mode_rejects_untyped_input() -> None:
    with pytest.raises(TypeError):
        classify_reuse_mode("procedure_reuse")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cold verified vs warm verified: improvement across M8.01 metrics.
# ---------------------------------------------------------------------------


def test_warm_verified_improvement_across_all_primary_metrics() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
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

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    assert result.cold_classification is ExperimentPhase.COLD
    assert result.warm_classification is ExperimentPhase.WARM
    comparison = _comparison(result)
    assert comparison.disposition is ReuseEfficiencyDisposition.IMPROVED
    assert comparison.baseline is cold
    assert comparison.reuse is warm
    assert _metric(result, EfficiencyMetric.MODEL_CALLS).delta == Decimal("-3")
    assert _metric(result, EfficiencyMetric.MODEL_TOKENS).delta == Decimal("-800")
    assert _metric(result, EfficiencyMetric.DURATION_MICROSECONDS).delta == Decimal("-8000000")
    assert _metric(result, EfficiencyMetric.EXTERNAL_COST).delta == Decimal("-1.05")
    assert _metric(result, EfficiencyMetric.RESEARCH_QUERIES).change is MetricChange.IMPROVED
    assert _metric(result, EfficiencyMetric.MACHINE_ACTIONS).change is MetricChange.IMPROVED
    assert _metric(result, EfficiencyMetric.REPAIR_ATTEMPTS).change is MetricChange.IMPROVED
    # Input/output token components are evidence, not independent dimensions.
    assert result.metric_comparison(EfficiencyMetric.MODEL_TOKENS) is not None
    assert result.reasons[0] == (
        "cold phase executed novel_plan without claiming reusable prior execution"
    )
    assert result.reasons[1] == "warm phase executed procedure_reuse with explicit reuse evidence"
    assert result.reasons[2:] == comparison.reasons


def test_warm_improvement_on_model_calls_alone() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.EXPLORATORY)
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.GUIDED_PROCEDURE,
        model_calls=2,
        model_tokens=1_000,
        elapsed=timedelta(seconds=10),
        cost=Decimal("1.25"),
        research_queries=3,
        machine_actions=5,
        repair_attempts=1,
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    assert _metric(result, EfficiencyMetric.MODEL_CALLS).change is MetricChange.IMPROVED
    assert _metric(result, EfficiencyMetric.MODEL_CALLS).ratio == Fraction(1, 2)
    assert _metric(result, EfficiencyMetric.DURATION_MICROSECONDS).change is MetricChange.SAME


def test_warm_improvement_on_duration_alone() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, elapsed=timedelta(seconds=30))
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        elapsed=timedelta(seconds=5),
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    assert _metric(result, EfficiencyMetric.DURATION_MICROSECONDS).delta == Decimal("-25000000")


def test_warm_improvement_on_external_cost_alone() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, cost=Decimal("3.00"))
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, cost=Decimal("0.50"))

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    assert _metric(result, EfficiencyMetric.EXTERNAL_COST).delta == Decimal("-2.50")


def test_equal_metrics_are_no_material_improvement() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_NO_MATERIAL_IMPROVEMENT
    assert _comparison(result).disposition is ReuseEfficiencyDisposition.NO_MATERIAL_IMPROVEMENT


def test_warm_regression_on_any_metric_is_regression() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.PROCEDURE_REUSE, cost=Decimal("2.00"))

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED
    assert _comparison(result).disposition is ReuseEfficiencyDisposition.REGRESSED
    assert _metric(result, EfficiencyMetric.EXTERNAL_COST).change is MetricChange.REGRESSED


def test_improvement_plus_regression_is_regression() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        model_calls=1,
        elapsed=timedelta(seconds=60),
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED


# ---------------------------------------------------------------------------
# Verified-success truth boundary (canonical M8.01 semantics, no text).
# ---------------------------------------------------------------------------


def test_cheaper_warm_verification_failure_is_regression_not_improvement() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        outcome=ExecutionEvidenceOutcome.FAILED,
        model_calls=0,
        model_tokens=0,
        elapsed=timedelta(seconds=0),
        cost=Decimal("0.00"),
        verification_detail="verified=true task_success=true warm_is_better=true",
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED
    assert _comparison(result).reuse.verified_success is False
    assert any("lost verified success" in reason for reason in result.reasons)


def test_cheaper_warm_unverified_run_is_regression_not_improvement() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        outcome=ExecutionEvidenceOutcome.UNVERIFIED,
        model_calls=0,
        model_tokens=0,
        elapsed=timedelta(seconds=0),
        cost=Decimal("0.00"),
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED


def test_cold_failure_with_warm_success_is_insufficient_evidence() -> None:
    task_id = TaskId.create()
    cold = _evidence(
        task_id=task_id,
        mode=ReuseMode.NOVEL_PLAN,
        outcome=ExecutionEvidenceOutcome.FAILED,
    )
    warm = _evidence(task_id=task_id, mode=ReuseMode.PROCEDURE_REUSE, model_calls=1)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.INSUFFICIENT_EVIDENCE
    assert _comparison(result).disposition is ReuseEfficiencyDisposition.INSUFFICIENT_EVIDENCE
    assert any("baseline lacks" in reason for reason in result.reasons)


def test_both_phases_failed_is_insufficient_evidence() -> None:
    task_id = TaskId.create()
    cold = _evidence(
        task_id=task_id, mode=ReuseMode.EXPLORATORY, outcome=ExecutionEvidenceOutcome.FAILED
    )
    warm = _evidence(
        task_id=task_id, mode=ReuseMode.CACHE_REUSE, outcome=ExecutionEvidenceOutcome.FAILED
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# Missing metrics and zero denominators (handled by M8.01, not re-created).
# ---------------------------------------------------------------------------


def test_metric_missing_on_one_side_stays_unavailable() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, research_queries=None)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, research_queries=0, model_calls=1)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.metric_comparison(EfficiencyMetric.RESEARCH_QUERIES) is None
    comparison = _comparison(result)
    assert comparison.metric(EfficiencyMetric.RESEARCH_QUERIES) is None
    # Other dimensions measured on both sides still compare.
    assert _metric(result, EfficiencyMetric.MODEL_CALLS).change is MetricChange.IMPROVED


def test_no_common_metric_is_insufficient_evidence() -> None:
    task_id = TaskId.create()
    cold = _evidence(
        task_id=task_id,
        mode=ReuseMode.NOVEL_PLAN,
        model_calls=None,
        model_tokens=None,
        elapsed=None,
        cost=None,
        research_queries=None,
        machine_actions=None,
        repair_attempts=None,
    )
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        model_calls=None,
        model_tokens=None,
        elapsed=None,
        cost=None,
        research_queries=None,
        machine_actions=None,
        repair_attempts=None,
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.INSUFFICIENT_EVIDENCE
    assert _comparison(result).metrics == ()
    assert any("no common measured efficiency dimension" in reason for reason in result.reasons)


def test_zero_baseline_metric_omits_ratio_through_m801() -> None:
    task_id = TaskId.create()
    cold = _evidence(
        task_id=task_id,
        mode=ReuseMode.DETERMINISTIC_CAPABILITY,
        model_calls=0,
        model_tokens=0,
    )
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=0, model_tokens=0)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    model_calls = _metric(result, EfficiencyMetric.MODEL_CALLS)
    assert model_calls.ratio is None
    assert model_calls.change is MetricChange.SAME
    assert result.verdict is ReuseExperimentVerdict.WARM_NO_MATERIAL_IMPROVEMENT


def test_zero_baseline_with_nonzero_warm_is_regression_without_ratio() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, model_calls=0, model_tokens=0)
    warm = _evidence(
        task_id=task_id, mode=ReuseMode.PROCEDURE_REUSE, model_calls=1, model_tokens=100
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    model_calls = _metric(result, EfficiencyMetric.MODEL_CALLS)
    assert model_calls.ratio is None
    assert model_calls.change is MetricChange.REGRESSED
    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED


def test_ratio_is_exact_rational_arithmetic_from_m801() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, model_calls=3, model_tokens=900)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1, model_tokens=300)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert _metric(result, EfficiencyMetric.MODEL_CALLS).ratio == Fraction(1, 3)


def test_mismatched_cost_units_omit_external_cost_metric() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, cost=Decimal("1.00"))
    warm = _evidence(
        task_id=task_id, mode=ReuseMode.CACHE_REUSE, cost=Decimal("0.10"), model_calls=1
    )
    assert warm.cost_unit == "USD"

    # Rebuild the warm record with a different accounting unit.
    warm_eur = ExecutionEfficiencyEvidence(
        task_id=warm.task_id,
        episode_id=warm.episode_id,
        correlation_id=warm.correlation_id,
        mode=warm.mode,
        outcome=warm.outcome,
        evidence_source=warm.evidence_source,
        evidence_reference=warm.evidence_reference,
        elapsed=warm.elapsed,
        model_calls=warm.model_calls,
        model_tokens=warm.model_tokens,
        external_cost=Decimal("0.10"),
        cost_unit="EUR",
        verification=warm.verification,
        verification_source=warm.verification_source,
        verification_reference=warm.verification_reference,
    )

    result = _run(cold, warm_eur, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.metric_comparison(EfficiencyMetric.EXTERNAL_COST) is None
    assert any("accounting units differ" in reason for reason in result.reasons)
    # Model calls still improved, so the verdict stays an improvement.
    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED


# ---------------------------------------------------------------------------
# Task comparability and explicit relationship binding.
# ---------------------------------------------------------------------------


def test_same_task_replay_without_relationship_is_comparable() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    assert _comparison(result).relationship is None
    assert any("same-task replay" in reason for reason in result.reasons)


def test_related_task_reuse_with_explicit_relationship_is_comparable() -> None:
    cold_task = TaskId.create()
    warm_task = TaskId.create()
    cold = _evidence(task_id=cold_task, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=warm_task, mode=ReuseMode.PROCEDURE_REUSE, model_calls=1)
    relationship = _relationship()

    result = _run(
        cold,
        warm,
        _spec(
            cold_task_id=cold_task,
            warm_task_id=warm_task,
            relationship=relationship,
        ),
    )

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    assert _comparison(result).relationship is relationship
    assert any("related-task reuse" in reason for reason in result.reasons)


def test_different_tasks_without_relationship_are_not_comparable() -> None:
    cold_task = TaskId.create()
    warm_task = TaskId.create()
    cold = _evidence(task_id=cold_task, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=warm_task, mode=ReuseMode.CACHE_REUSE, model_calls=1)

    result = _run(cold, warm, _spec(cold_task_id=cold_task, warm_task_id=warm_task))

    assert result.verdict is ReuseExperimentVerdict.NOT_COMPARABLE
    assert any("require explicit relationship" in reason for reason in result.reasons)


def test_unrelated_relationship_is_not_comparable() -> None:
    cold_task = TaskId.create()
    warm_task = TaskId.create()
    cold = _evidence(task_id=cold_task, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=warm_task, mode=ReuseMode.CACHE_REUSE, model_calls=1)

    result = _run(
        cold,
        warm,
        _spec(
            cold_task_id=cold_task,
            warm_task_id=warm_task,
            relationship=_relationship(TaskRelationship.UNRELATED),
        ),
    )

    assert result.verdict is ReuseExperimentVerdict.NOT_COMPARABLE
    assert any("unrelated" in reason for reason in result.reasons)


def test_contradictory_relationship_is_not_comparable() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)

    result = _run(
        cold,
        warm,
        _spec(
            cold_task_id=task_id,
            warm_task_id=task_id,
            relationship=_relationship(TaskRelationship.RELATED_TASK_REUSE),
        ),
    )

    assert result.verdict is ReuseExperimentVerdict.NOT_COMPARABLE
    assert any("contradicts identical TaskId" in reason for reason in result.reasons)


def test_same_task_replay_relationship_is_accepted_for_identical_task_ids() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)

    result = _run(
        cold,
        warm,
        _spec(
            cold_task_id=task_id,
            warm_task_id=task_id,
            relationship=_relationship(TaskRelationship.SAME_TASK_REPLAY),
        ),
    )

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED


# ---------------------------------------------------------------------------
# Exact ProcedureId/revision binding for the warm phase.
# ---------------------------------------------------------------------------


def test_matching_procedure_binding_is_valid_and_preserved() -> None:
    task_id = TaskId.create()
    procedure = ProcedureRevisionRef(ProcedureId.create(), 7)
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id, mode=ReuseMode.PROCEDURE_REUSE, procedure=procedure, model_calls=1
    )

    result = _run(
        cold,
        warm,
        _spec(
            cold_task_id=task_id,
            warm_task_id=task_id,
            expected_warm_procedure=procedure,
        ),
    )

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    assert result.warm.procedure == procedure


def test_procedure_revision_mismatch_invalidates_the_experiment() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        procedure=ProcedureRevisionRef(ProcedureId.create(), 3),
    )
    expected = ProcedureRevisionRef(warm.procedure.procedure_id, 9) if warm.procedure else None
    assert expected is not None

    result = _run(
        cold,
        warm,
        _spec(
            cold_task_id=task_id,
            warm_task_id=task_id,
            expected_warm_procedure=expected,
        ),
    )

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT
    assert result.comparison is None
    assert result.metric_comparison(EfficiencyMetric.MODEL_CALLS) is None
    actual = warm.procedure
    assert actual is not None
    mismatch_reasons = [reason for reason in result.reasons if "revision 9" in reason]
    assert mismatch_reasons == [
        f"warm phase used procedure {actual.procedure_id.to_str()} revision 3 "
        f"but the spec declared procedure {actual.procedure_id.to_str()} revision 9"
    ]
    # Both phase records stay preserved even though nothing is comparable.
    assert result.cold is cold
    assert result.warm is warm


def test_procedure_identity_mismatch_invalidates_the_experiment() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        procedure=ProcedureRevisionRef(ProcedureId.create(), 3),
    )
    expected = ProcedureRevisionRef(ProcedureId.create(), 3)

    result = _run(
        cold,
        warm,
        _spec(
            cold_task_id=task_id,
            warm_task_id=task_id,
            expected_warm_procedure=expected,
        ),
    )

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT
    assert result.comparison is None
    assert any(
        reason.startswith("warm phase used procedure ") and "revision 3" in reason
        for reason in result.reasons
    )


def test_declared_procedure_without_warm_procedure_is_invalid() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    expected = ProcedureRevisionRef(ProcedureId.create(), 1)

    result = _run(
        cold,
        warm,
        _spec(
            cold_task_id=task_id,
            warm_task_id=task_id,
            expected_warm_procedure=expected,
        ),
    )

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT
    assert (
        f"warm phase did not use the declared procedure {expected.procedure_id.to_str()} "
        f"revision {expected.revision}" in result.reasons
    )


def test_no_declared_binding_records_the_actual_warm_procedure() -> None:
    task_id = TaskId.create()
    procedure = ProcedureRevisionRef(ProcedureId.create(), 4)
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id, mode=ReuseMode.GUIDED_PROCEDURE, procedure=procedure, model_calls=1
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    assert result.warm.procedure == procedure
    assert result.spec.expected_warm_procedure is None


# ---------------------------------------------------------------------------
# Phase classification and declared-task integrity.
# ---------------------------------------------------------------------------


def test_cold_phase_claiming_reuse_mode_is_invalid_experiment() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.PROCEDURE_REUSE)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT
    assert result.comparison is None
    assert (
        "cold phase evidence mode procedure_reuse claims reusable prior execution" in result.reasons
    )
    assert result.cold_classification is ExperimentPhase.WARM


def test_warm_phase_without_reuse_claim_is_invalid_experiment() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, model_calls=1)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT
    assert result.comparison is None
    assert (
        "warm phase evidence mode novel_plan does not claim reusable prior execution"
        in result.reasons
    )
    assert result.warm_classification is ExperimentPhase.COLD


def test_warm_deterministic_capability_is_not_warm_reuse() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.EXPLORATORY)
    warm = _evidence(task_id=task_id, mode=ReuseMode.DETERMINISTIC_CAPABILITY, model_calls=1)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT


def test_cold_phase_measuring_undeclared_task_is_invalid() -> None:
    declared = TaskId.create()
    measured = TaskId.create()
    cold = _evidence(task_id=measured, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=declared, mode=ReuseMode.CACHE_REUSE, model_calls=1)

    result = _run(cold, warm, _spec(cold_task_id=declared, warm_task_id=declared))

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT
    assert (
        f"cold phase measured task {measured.to_str()} "
        f"but the spec declared task {declared.to_str()}" in result.reasons
    )


def test_warm_phase_measuring_undeclared_task_is_invalid() -> None:
    task_id = TaskId.create()
    other = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=other, mode=ReuseMode.CACHE_REUSE, model_calls=1)

    result = _run(
        cold,
        warm,
        _spec(cold_task_id=task_id, warm_task_id=task_id, relationship=_relationship()),
    )

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT
    assert (
        f"warm phase measured task {other.to_str()} "
        f"but the spec declared task {task_id.to_str()}" in result.reasons
    )


def test_identical_phase_evidence_is_invalid_experiment() -> None:
    task_id = TaskId.create()
    evidence_reference = uuid4()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    # Distinct records that dishonestly share one evidence reference: the
    # canonical M8.01 comparison rejects them and the harness records that.
    shared_cold = ExecutionEfficiencyEvidence(
        task_id=cold.task_id,
        episode_id=cold.episode_id,
        correlation_id=cold.correlation_id,
        mode=cold.mode,
        outcome=cold.outcome,
        evidence_source=cold.evidence_source,
        evidence_reference=evidence_reference,
        elapsed=cold.elapsed,
        model_calls=cold.model_calls,
        model_tokens=cold.model_tokens,
        external_cost=cold.external_cost,
        cost_unit=cold.cost_unit,
        verification=cold.verification,
        verification_source=cold.verification_source,
        verification_reference=cold.verification_reference,
    )
    shared_warm = ExecutionEfficiencyEvidence(
        task_id=warm.task_id,
        episode_id=warm.episode_id,
        correlation_id=warm.correlation_id,
        mode=warm.mode,
        outcome=warm.outcome,
        evidence_source=warm.evidence_source,
        evidence_reference=evidence_reference,
        elapsed=warm.elapsed,
        model_calls=warm.model_calls,
        model_tokens=warm.model_tokens,
        external_cost=warm.external_cost,
        cost_unit=warm.cost_unit,
        verification=warm.verification,
        verification_source=warm.verification_source,
        verification_reference=warm.verification_reference,
    )

    result = _run(shared_cold, shared_warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT
    assert result.comparison is None
    assert result.reasons == (
        "canonical comparison rejected the phase evidence: "
        "baseline and reuse must be distinct measured execution evidence",
    )


# ---------------------------------------------------------------------------
# Injected ports: exactly one bounded call per phase, fail-closed behaviour.
# ---------------------------------------------------------------------------


def test_each_runner_is_called_exactly_once_with_typed_requests() -> None:
    task_id = TaskId.create()
    experiment_id = uuid4()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    harness, cold_runner, warm_runner = _harness(cold, warm)
    spec = ReuseExperimentSpec(
        experiment_id=experiment_id, cold_task_id=task_id, warm_task_id=task_id
    )

    result = harness.run(spec)

    assert len(cold_runner.requests) == 1
    assert len(warm_runner.requests) == 1
    assert cold_runner.requests[0] == PhaseExecutionRequest(
        experiment_id=experiment_id, phase=ExperimentPhase.COLD, task_id=task_id
    )
    assert warm_runner.requests[0] == PhaseExecutionRequest(
        experiment_id=experiment_id, phase=ExperimentPhase.WARM, task_id=task_id
    )
    assert cold_runner.requests[0].phase is ExperimentPhase.COLD
    assert warm_runner.requests[0].phase is ExperimentPhase.WARM
    assert result.cold is cold
    assert result.warm is warm


def test_one_port_object_may_serve_both_phases() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    shared: list[ExecutionEfficiencyEvidence] = [cold, warm]

    @dataclass
    class TwoPhaseRunner:
        requests: list[PhaseExecutionRequest] = field(default_factory=list)

        def run_phase(self, request: PhaseExecutionRequest) -> ExecutionEfficiencyEvidence:
            self.requests.append(request)
            return shared[len(self.requests) - 1]

    runner = TwoPhaseRunner()
    result = ReuseExperimentHarness(cold_runner=runner, warm_runner=runner).run(
        _spec(cold_task_id=task_id, warm_task_id=task_id)
    )

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    assert len(runner.requests) == 2


def test_runner_returning_untyped_data_fails_closed() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    hostile = {
        "verified": "true",
        "model_calls": 0,
        "warm_is_better": "true",
        "permission": "ADMIN",
    }
    harness = ReuseExperimentHarness(
        cold_runner=ScriptedRunner(cold), warm_runner=GarbageRunner(hostile)
    )

    with pytest.raises(PhaseEvidenceError) as excinfo:
        harness.run(_spec(cold_task_id=task_id, warm_task_id=task_id))

    assert "warm phase runner returned dict" in str(excinfo.value)
    assert "does not parse untyped data" in str(excinfo.value)


@pytest.mark.parametrize("payload", ["verified=true", None, 42, ["verified"]])
def test_runner_returning_non_evidence_types_fails_closed(payload: object) -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    harness = ReuseExperimentHarness(
        cold_runner=ScriptedRunner(cold), warm_runner=GarbageRunner(payload)
    )

    with pytest.raises(PhaseEvidenceError):
        harness.run(_spec(cold_task_id=task_id, warm_task_id=task_id))


def test_cold_runner_returning_untyped_data_fails_closed() -> None:
    task_id = TaskId.create()
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    harness = ReuseExperimentHarness(
        cold_runner=GarbageRunner("cold verified=true"), warm_runner=ScriptedRunner(warm)
    )

    with pytest.raises(PhaseEvidenceError) as excinfo:
        harness.run(_spec(cold_task_id=task_id, warm_task_id=task_id))

    assert "cold phase runner returned str" in str(excinfo.value)


def test_runner_exception_propagates_without_fabricated_evidence() -> None:
    task_id = TaskId.create()
    failure = RuntimeError("model provider unavailable")
    harness = ReuseExperimentHarness(
        cold_runner=ScriptedRunner(_evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)),
        warm_runner=RaisingRunner(failure),
    )

    with pytest.raises(RuntimeError, match="model provider unavailable"):
        harness.run(_spec(cold_task_id=task_id, warm_task_id=task_id))


def test_harness_requires_run_phase_ports() -> None:
    with pytest.raises(TypeError):
        ReuseExperimentHarness(cold_runner=object(), warm_runner=object())  # type: ignore[arg-type]


def test_run_requires_a_spec() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    harness, _cold_runner, _warm_runner = _harness(cold, warm)

    with pytest.raises(TypeError):
        harness.run({"cold": cold, "warm": warm})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Determinism with injected measurements (no clock, no sleep, no model).
# ---------------------------------------------------------------------------


def test_repeated_runs_with_the_same_injected_evidence_are_identical() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.PROCEDURE_REUSE, model_calls=1)
    spec = _spec(cold_task_id=task_id, warm_task_id=task_id)
    harness, _cold_runner, _warm_runner = _harness(cold, warm)

    first = harness.run(spec)
    second = harness.run(spec)

    assert first == second
    assert first.to_json() == second.to_json()


def test_result_is_frozen_and_inert() -> None:
    from dataclasses import FrozenInstanceError

    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    with pytest.raises(FrozenInstanceError):
        result.verdict = ReuseExperimentVerdict.WARM_REGRESSED  # type: ignore[misc]


def test_verdicts_map_one_to_one_from_canonical_dispositions() -> None:
    mapping = {
        ReuseEfficiencyDisposition.IMPROVED: ReuseExperimentVerdict.WARM_IMPROVED,
        ReuseEfficiencyDisposition.NO_MATERIAL_IMPROVEMENT: (
            ReuseExperimentVerdict.WARM_NO_MATERIAL_IMPROVEMENT
        ),
        ReuseEfficiencyDisposition.REGRESSED: ReuseExperimentVerdict.WARM_REGRESSED,
        ReuseEfficiencyDisposition.NOT_COMPARABLE: ReuseExperimentVerdict.NOT_COMPARABLE,
        ReuseEfficiencyDisposition.INSUFFICIENT_EVIDENCE: (
            ReuseExperimentVerdict.INSUFFICIENT_EVIDENCE
        ),
    }
    assert set(mapping) == set(ReuseEfficiencyDisposition)
    assert len(set(mapping.values())) == len(mapping)


# ---------------------------------------------------------------------------
# Spec and result contract validation.
# ---------------------------------------------------------------------------


def test_spec_rejects_nil_experiment_id_and_wrong_types() -> None:
    task_id = TaskId.create()
    with pytest.raises(ReuseExperimentError):
        ReuseExperimentSpec(experiment_id=UUID(int=0), cold_task_id=task_id, warm_task_id=task_id)
    with pytest.raises(TypeError):
        ReuseExperimentSpec(experiment_id=uuid4(), cold_task_id="t", warm_task_id=task_id)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ReuseExperimentSpec(
            experiment_id=uuid4(),
            cold_task_id=task_id,
            warm_task_id=task_id,
            relationship="related",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        ReuseExperimentSpec(
            experiment_id=uuid4(),
            cold_task_id=task_id,
            warm_task_id=task_id,
            expected_warm_procedure=object(),  # type: ignore[arg-type]
        )


def test_spec_round_trips_through_json_with_optional_fields() -> None:
    task_id = TaskId.create()
    procedure = ProcedureRevisionRef(ProcedureId.create(), 2)
    spec = ReuseExperimentSpec(
        experiment_id=uuid4(),
        cold_task_id=task_id,
        warm_task_id=TaskId.create(),
        relationship=_relationship(),
        expected_warm_procedure=procedure,
    )

    parsed = ReuseExperimentSpec.from_dict(spec.to_dict())

    assert parsed == spec


def test_spec_deserialization_rejects_unknown_fields_and_bad_ids() -> None:
    task_id = TaskId.create()
    spec = ReuseExperimentSpec(experiment_id=uuid4(), cold_task_id=task_id, warm_task_id=task_id)
    raw = spec.to_dict()
    raw["extra"] = "field"
    with pytest.raises(ReuseExperimentDeserializationError):
        ReuseExperimentSpec.from_dict(raw)

    bad = spec.to_dict()
    bad["experiment_id"] = "not-a-uuid"
    with pytest.raises(ReuseExperimentDeserializationError):
        ReuseExperimentSpec.from_dict(bad)

    nil = spec.to_dict()
    nil["experiment_id"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ReuseExperimentDeserializationError):
        ReuseExperimentSpec.from_dict(nil)

    bad_task = spec.to_dict()
    bad_task["cold_task_id"] = "nope"
    with pytest.raises(ReuseExperimentDeserializationError):
        ReuseExperimentSpec.from_dict(bad_task)


def test_result_rejects_inconsistent_derived_fields() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))
    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED

    with pytest.raises(ReuseExperimentError):
        ReuseExperimentResult(
            spec=result.spec,
            cold=result.cold,
            warm=result.warm,
            cold_classification=result.cold_classification,
            warm_classification=result.warm_classification,
            comparison=result.comparison,
            verdict=ReuseExperimentVerdict.WARM_REGRESSED,
            reasons=result.reasons,
        )
    with pytest.raises(ReuseExperimentError):
        ReuseExperimentResult(
            spec=result.spec,
            cold=result.cold,
            warm=result.warm,
            cold_classification=result.cold_classification,
            warm_classification=result.warm_classification,
            comparison=result.comparison,
            verdict=result.verdict,
            reasons=("fabricated reason",),
        )
    with pytest.raises(ReuseExperimentError):
        ReuseExperimentResult(
            spec=result.spec,
            cold=result.cold,
            warm=result.warm,
            cold_classification=ExperimentPhase.WARM,
            warm_classification=result.warm_classification,
            comparison=result.comparison,
            verdict=result.verdict,
            reasons=result.reasons,
        )


def test_result_requires_non_empty_reasons_and_valid_text() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    with pytest.raises(ReuseExperimentError):
        ReuseExperimentResult(
            spec=result.spec,
            cold=result.cold,
            warm=result.warm,
            cold_classification=result.cold_classification,
            warm_classification=result.warm_classification,
            comparison=result.comparison,
            verdict=result.verdict,
            reasons=(),
        )
    with pytest.raises(ReuseExperimentError):
        ReuseExperimentResult(
            spec=result.spec,
            cold=result.cold,
            warm=result.warm,
            cold_classification=result.cold_classification,
            warm_classification=result.warm_classification,
            comparison=result.comparison,
            verdict=result.verdict,
            reasons=("padded  ",),
        )


def test_result_rejects_wrong_schema_version() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    with pytest.raises(UnsupportedReuseExperimentSchemaVersionError):
        ReuseExperimentResult(
            spec=result.spec,
            cold=result.cold,
            warm=result.warm,
            cold_classification=result.cold_classification,
            warm_classification=result.warm_classification,
            comparison=result.comparison,
            verdict=result.verdict,
            reasons=result.reasons,
            schema_version=2,
        )


# ---------------------------------------------------------------------------
# Serialization round trip and tamper rejection.
# ---------------------------------------------------------------------------


def test_result_round_trips_through_deterministic_json() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        model_calls=1,
        model_tokens=250,
        elapsed=timedelta(seconds=2),
        cost=Decimal("0.25"),
    )
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    encoded = result.to_json()
    parsed = ReuseExperimentResult.from_json(encoded)

    assert parsed == result
    assert parsed.to_json() == encoded
    assert parsed.schema_version == REUSE_EXPERIMENT_SCHEMA_VERSION


def test_invalid_result_round_trips_without_a_comparison() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))
    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT

    parsed = ReuseExperimentResult.from_json(result.to_json())

    assert parsed == result
    assert parsed.comparison is None


def test_result_json_rejects_tampered_verdict() -> None:
    import json

    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, cost=Decimal("2.00"))
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, cost=Decimal("3.00"))
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))
    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED

    raw = json.loads(result.to_json())
    raw["verdict"] = "warm_improved"
    with pytest.raises(ReuseExperimentError):
        ReuseExperimentResult.from_dict(raw)


def test_result_json_rejects_tampered_metrics() -> None:
    import json

    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    raw = json.loads(result.to_json())
    assert raw["comparison"] is not None
    raw["comparison"]["metrics"][0]["reuse"] = "0"
    with pytest.raises(Exception) as excinfo:
        ReuseExperimentResult.from_dict(raw)
    assert isinstance(excinfo.value, (ReuseExperimentError, ValueError))


def test_result_json_rejects_unknown_fields_and_bad_root() -> None:
    import json

    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    raw = json.loads(result.to_json())
    raw["bonus"] = "field"
    with pytest.raises(ReuseExperimentDeserializationError):
        ReuseExperimentResult.from_dict(raw)

    with pytest.raises(ReuseExperimentDeserializationError):
        ReuseExperimentResult.from_json("[1, 2, 3]")

    with pytest.raises(ReuseExperimentDeserializationError):
        ReuseExperimentResult.from_json("{malformed")

    with pytest.raises(TypeError):
        ReuseExperimentResult.from_json(b"bytes")  # type: ignore[arg-type]


def test_result_json_rejects_unsupported_schema_version() -> None:
    import json

    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    raw = json.loads(result.to_json())
    raw["schema_version"] = 99
    with pytest.raises(UnsupportedReuseExperimentSchemaVersionError):
        ReuseExperimentResult.from_dict(raw)


def test_result_json_rejects_empty_reasons_and_bad_classification() -> None:
    import json

    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    raw = json.loads(result.to_json())
    raw["reasons"] = []
    with pytest.raises(ReuseExperimentDeserializationError):
        ReuseExperimentResult.from_dict(raw)

    raw = json.loads(result.to_json())
    raw["cold_classification"] = "hot"
    with pytest.raises(ReuseExperimentDeserializationError):
        ReuseExperimentResult.from_dict(raw)


def test_metric_comparison_requires_a_canonical_metric_name() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    with pytest.raises(TypeError):
        result.metric_comparison("model_calls")  # type: ignore[arg-type]


def test_phase_request_validates_typed_fields() -> None:
    task_id = TaskId.create()
    request = PhaseExecutionRequest(
        experiment_id=uuid4(), phase=ExperimentPhase.COLD, task_id=task_id
    )
    assert request.phase is ExperimentPhase.COLD
    assert request.task_id is task_id

    with pytest.raises(TypeError):
        PhaseExecutionRequest(experiment_id=uuid4(), phase="cold", task_id=task_id)  # type: ignore[arg-type]
    with pytest.raises(ReuseExperimentError):
        PhaseExecutionRequest(
            experiment_id=UUID(int=0), phase=ExperimentPhase.COLD, task_id=task_id
        )
    with pytest.raises(TypeError):
        PhaseExecutionRequest(experiment_id=uuid4(), phase=ExperimentPhase.WARM, task_id=1)  # type: ignore[arg-type]


def test_scripted_runner_satisfies_the_phase_runner_port() -> None:
    task_id = TaskId.create()
    runner = ScriptedRunner(_evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN))
    assert isinstance(runner, PhaseRunner)
