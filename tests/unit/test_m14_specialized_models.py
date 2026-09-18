"""M14 specialist dataset/model/benchmark acceptance tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from agentx.cognition.router import ExecutionLevel
from agentx.core.ids import TaskId
from agentx.core.reuse_efficiency import ExecutionEvidenceOutcome
from agentx.specialized_models import (
    DistillationPipeline,
    GroundingExample,
    GroundingLabel,
    GroundingPrediction,
    GroundingSpecialistDataset,
    LightweightGroundingModel,
    LightweightRoutingModel,
    RoutingExample,
    RoutingSpecialistDataset,
    SpecialistBenchmark,
    SpecialistModelError,
    VerifierAssistanceExperiment,
)
from agentx.strategy_performance_evidence import StrategyPerformanceEvidence

_T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
_CORRELATION = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _grounding_example(
    ident: int,
    *,
    claim: str,
    evidence: str,
    label: GroundingLabel,
) -> GroundingExample:
    return GroundingExample(
        example_id=UUID(int=ident),
        claim=claim,
        evidence_text=evidence,
        label=label,
        provenance_source="tests.canonical_verifier",
        provenance_reference=f"verification:{ident}",
    )


def _performance(
    ident: int,
    *,
    level: ExecutionLevel,
    success: bool | None,
) -> StrategyPerformanceEvidence:
    if success is True:
        outcome = ExecutionEvidenceOutcome.VERIFIED_SUCCESS
        verification = True
    elif success is False:
        outcome = ExecutionEvidenceOutcome.FAILED
        verification = False
    else:
        outcome = ExecutionEvidenceOutcome.UNVERIFIED
        verification = None
    return StrategyPerformanceEvidence(
        evidence_id=UUID(int=ident),
        task_id=TaskId.create(),
        correlation_id=_CORRELATION,
        execution_level=level,
        outcome=outcome,
        observed_at=_T0 + timedelta(seconds=ident),
        elapsed=timedelta(milliseconds=10),
        model_calls=1,
        machine_actions=1,
        verification_passed=verification,
    )


def test_grounding_dataset_has_explicit_provenance_and_stable_version() -> None:
    first = _grounding_example(
        1,
        claim="file exists",
        evidence="native stat confirms file exists",
        label=GroundingLabel.GROUNDED,
    )
    second = _grounding_example(
        2,
        claim="window title is Report",
        evidence="observed window title is Settings",
        label=GroundingLabel.NOT_GROUNDED,
    )

    a = GroundingSpecialistDataset.build((second, first))
    b = GroundingSpecialistDataset.build((first, second))

    assert a.dataset_id == b.dataset_id
    assert a.dataset_id.startswith("sha256:")
    assert tuple(item.example_id for item in a.examples) == (
        UUID(int=1),
        UUID(int=2),
    )
    assert all(item.provenance_reference for item in a.examples)

    with pytest.raises(SpecialistModelError, match="duplicate"):
        GroundingSpecialistDataset.build((first, first))


def test_grounding_model_round_trips_safe_json_and_abstains_without_evidence() -> None:
    dataset = GroundingSpecialistDataset.build(
        (
            _grounding_example(
                10,
                claim="file present",
                evidence="native file present verified exists",
                label=GroundingLabel.GROUNDED,
            ),
            _grounding_example(
                11,
                claim="file present",
                evidence="native file missing verified absent",
                label=GroundingLabel.NOT_GROUNDED,
            ),
            _grounding_example(
                12,
                claim="window visible",
                evidence="window visible observed present",
                label=GroundingLabel.GROUNDED,
            ),
        )
    )
    model = LightweightGroundingModel.train(dataset)
    restored = LightweightGroundingModel.from_json(model.to_json())

    prediction = restored.predict(
        claim="window visible",
        evidence_text="window visible observed present",
    )
    assert prediction.label is GroundingLabel.GROUNDED
    assert prediction.advisory_only is True
    assert prediction.confidence > 0

    unknown = restored.predict(
        claim="novel unrelated token",
        evidence_text="zebra quantum marble",
    )
    assert unknown.label is None
    assert unknown.confidence == Decimal(0)


def test_grounding_model_rejects_corrupt_or_tampered_artifacts() -> None:
    dataset = GroundingSpecialistDataset.build(
        (
            _grounding_example(
                20,
                claim="a",
                evidence="verified a",
                label=GroundingLabel.GROUNDED,
            ),
        )
    )
    model = LightweightGroundingModel.train(dataset)
    encoded = json.loads(model.to_json())
    encoded["model_id"] = "sha256:tampered"

    with pytest.raises(SpecialistModelError, match="identity mismatch"):
        LightweightGroundingModel.from_json(json.dumps(encoded))
    with pytest.raises(SpecialistModelError, match="malformed"):
        LightweightGroundingModel.from_json("{not-json")


def test_routing_dataset_requires_canonical_verified_outcome() -> None:
    success = RoutingExample.from_performance(
        _performance(30, level=ExecutionLevel.L2_COMPILED, success=True),
        task_family="files",
        context_text="repeat deterministic file workflow",
        provenance_reference="strategy-evidence:30",
    )
    failure = RoutingExample.from_performance(
        _performance(31, level=ExecutionLevel.L4_PLANNED, success=False),
        task_family="files",
        context_text="repeat deterministic file workflow",
        provenance_reference="strategy-evidence:31",
    )
    dataset = RoutingSpecialistDataset.build((failure, success))
    assert len(dataset.examples) == 2

    with pytest.raises(SpecialistModelError, match="verified"):
        RoutingExample.from_performance(
            _performance(32, level=ExecutionLevel.L3_GUIDED, success=None),
            task_family="files",
            context_text="unknown outcome",
            provenance_reference="strategy-evidence:32",
        )


def test_routing_model_is_constrained_to_available_strategies_and_round_trips() -> None:
    dataset = RoutingSpecialistDataset.build(
        (
            RoutingExample.from_performance(
                _performance(40, level=ExecutionLevel.L2_COMPILED, success=True),
                task_family="files",
                context_text="repeat compiled deterministic file transform",
                provenance_reference="strategy-evidence:40",
            ),
            RoutingExample.from_performance(
                _performance(41, level=ExecutionLevel.L2_COMPILED, success=True),
                task_family="files",
                context_text="compiled deterministic file action",
                provenance_reference="strategy-evidence:41",
            ),
            RoutingExample.from_performance(
                _performance(42, level=ExecutionLevel.L4_PLANNED, success=True),
                task_family="research",
                context_text="compose multi step research plan",
                provenance_reference="strategy-evidence:42",
            ),
        )
    )
    model = LightweightRoutingModel.train(dataset)
    restored = LightweightRoutingModel.from_json(model.to_json())

    prediction = restored.predict(
        task_family="files",
        context_text="compiled deterministic file transform",
        available=(ExecutionLevel.L2_COMPILED, ExecutionLevel.L4_PLANNED),
    )
    assert prediction.level is ExecutionLevel.L2_COMPILED
    assert prediction.confidence > 0

    # Learned preference for L2 cannot create L2 if the caller did not permit it.
    constrained = restored.predict(
        task_family="files",
        context_text="compiled deterministic file transform",
        available=(ExecutionLevel.L4_PLANNED,),
    )
    assert constrained.level is None or constrained.level is ExecutionLevel.L4_PLANNED
    assert constrained.level is not ExecutionLevel.L2_COMPILED

    unknown = restored.predict(
        task_family="unknown-family",
        context_text="unseen zebra quartz",
        available=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
    )
    assert unknown.level is None


def test_routing_model_rejects_tampered_identity_and_unknown_execution_level() -> None:
    dataset = RoutingSpecialistDataset.build(
        (
            RoutingExample.from_performance(
                _performance(50, level=ExecutionLevel.L1_DIRECT, success=True),
                task_family="direct",
                context_text="known direct action",
                provenance_reference="strategy-evidence:50",
            ),
        )
    )
    model = LightweightRoutingModel.train(dataset)
    raw = json.loads(model.to_json())
    raw["model_id"] = "sha256:not-the-model"
    with pytest.raises(SpecialistModelError, match="identity mismatch"):
        LightweightRoutingModel.from_json(json.dumps(raw))

    raw = json.loads(model.to_json())
    raw["family_counts"][0][1] = "L99_PRIVILEGED"
    with pytest.raises(SpecialistModelError, match="unknown execution level"):
        LightweightRoutingModel.from_json(json.dumps(raw))


def test_verifier_assistance_tracks_false_positives_and_never_becomes_authority() -> None:
    report = VerifierAssistanceExperiment().run(
        predictions=(
            GroundingPrediction(
                label=GroundingLabel.GROUNDED,
                confidence=Decimal("0.9"),
            ),
            GroundingPrediction(
                label=GroundingLabel.GROUNDED,
                confidence=Decimal("0.8"),
            ),
            GroundingPrediction(
                label=GroundingLabel.NOT_GROUNDED,
                confidence=Decimal("0.7"),
            ),
            GroundingPrediction(label=None, confidence=Decimal(0)),
        ),
        truths=(True, False, False, True),
    )

    assert report.samples == 4
    assert report.true_positive == 1
    assert report.false_positive == 1
    assert report.true_negative == 1
    assert report.false_negative == 1
    assert report.precision == Decimal("0.5")
    assert report.recall == Decimal("0.5")
    assert report.sole_authority is False


def test_distillation_records_teacher_dataset_config_and_evaluation_lineage() -> None:
    dataset = GroundingSpecialistDataset.build(
        (
            _grounding_example(
                60,
                claim="target exists",
                evidence="canonical verifier observed target exists",
                label=GroundingLabel.GROUNDED,
            ),
            _grounding_example(
                61,
                claim="target exists",
                evidence="canonical verifier observed target missing",
                label=GroundingLabel.NOT_GROUNDED,
            ),
        )
    )

    model, artifact = DistillationPipeline().distill_grounding(
        teacher_id="canonical-verifier-fixture-v1",
        dataset=dataset,
        training_config={"algorithm": "token-count", "seed": 7},
        evaluation_reference="tests:m14-distillation",
    )

    assert artifact.teacher_id == "canonical-verifier-fixture-v1"
    assert artifact.dataset_id == dataset.dataset_id
    assert artifact.student_model_id == model.model_id
    assert artifact.training_config_id.startswith("sha256:")
    assert artifact.evaluation_reference == "tests:m14-distillation"


def test_specialist_benchmark_reports_positive_negative_and_insufficient_results_truthfully(
) -> None:
    benchmark = SpecialistBenchmark()

    improved = benchmark.compare_labels(
        name="grounding-controlled",
        baseline=(None, None, None, None),
        specialist=(True, False, True, False),
        truth=(True, False, True, False),
    )
    assert improved.samples == 4
    assert improved.baseline_correct == 0
    assert improved.specialist_correct == 4
    assert improved.disposition == "improved"

    regressed = benchmark.compare_labels(
        name="routing-controlled-regression",
        baseline=("a", "b"),
        specialist=("x", "x"),
        truth=("a", "b"),
    )
    assert regressed.disposition == "regressed"

    insufficient = benchmark.compare_labels(
        name="empty-controlled",
        baseline=(),
        specialist=(),
        truth=(),
    )
    assert insufficient.disposition == "insufficient_evidence"
    assert insufficient.baseline_accuracy is None
    assert insufficient.specialist_accuracy is None
