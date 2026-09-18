"""AX-540 end-to-end M14 controlled acceptance.

This is deterministic controlled evidence, not a claim of real-provider,
real-user, or statistically representative production improvement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from agentx.adaptive_optimization import (
    BanditConfig,
    ContextualBanditExperiment,
    EnvironmentKey,
    LoggedPolicyCase,
    OptimizationPolicy,
    OptimizationPolicyRuntime,
    OptimizationPolicyStore,
    PolicyEvaluationDisposition,
    StrategyContext,
    StrategyOutcomeLedger,
    StrategyOutcomeRecord,
    StrategyStatisticsEngine,
)
from agentx.cognition.router import ExecutionLevel
from agentx.core.ids import TaskId
from agentx.core.reuse_efficiency import ExecutionEvidenceOutcome
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.specialized_models import (
    GroundingExample,
    GroundingLabel,
    GroundingSpecialistDataset,
    LightweightGroundingModel,
    SpecialistBenchmark,
)
from agentx.strategy_performance_evidence import StrategyPerformanceEvidence

_T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
_CORRELATION = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _journal(path: Path) -> EventJournal:
    return EventJournal(SQLiteDatabase(path.resolve()))


def _outcome(
    *,
    ident: int,
    level: ExecutionLevel,
    success: bool,
    context: StrategyContext,
) -> StrategyOutcomeRecord:
    return StrategyOutcomeRecord(
        performance=StrategyPerformanceEvidence(
            evidence_id=UUID(int=ident),
            task_id=TaskId.create(),
            correlation_id=_CORRELATION,
            execution_level=level,
            outcome=(
                ExecutionEvidenceOutcome.VERIFIED_SUCCESS
                if success
                else ExecutionEvidenceOutcome.FAILED
            ),
            observed_at=_T0 + timedelta(minutes=ident),
            elapsed=timedelta(milliseconds=10 if success else 20),
            model_calls=0 if level is ExecutionLevel.L1_DIRECT else 1,
            machine_actions=1,
            external_cost=None,
            cost_unit=None,
            verification_passed=success,
        ),
        context=context,
        predicted_confidence=Decimal("0.8") if success else Decimal("0.3"),
    )


def test_m14_controlled_e2e_history_experiment_promotion_degradation_rollback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agentx.sqlite3"
    context = StrategyContext(
        environment=EnvironmentKey("env-m14-controlled", "r1"),
        task_family="compose",
        captured_at=_T0,
    )
    available = (ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED)
    rows = tuple(
        _outcome(ident=ident, level=level, success=success, context=context)
        for ident, (level, success) in enumerate(
            (
                (ExecutionLevel.L1_DIRECT, False),
                (ExecutionLevel.L4_PLANNED, True),
                (ExecutionLevel.L1_DIRECT, False),
                (ExecutionLevel.L4_PLANNED, True),
                (ExecutionLevel.L1_DIRECT, False),
                (ExecutionLevel.L4_PLANNED, True),
                (ExecutionLevel.L1_DIRECT, False),
                (ExecutionLevel.L4_PLANNED, True),
                (ExecutionLevel.L1_DIRECT, False),
                (ExecutionLevel.L4_PLANNED, True),
            ),
            start=100,
        )
    )

    ledger = StrategyOutcomeLedger(_journal(path))
    for row in rows:
        ledger.append(row)
    reopened_history = StrategyOutcomeLedger(_journal(path)).read()
    assert reopened_history == rows

    stats = StrategyStatisticsEngine().summarize(
        reopened_history,
        strategy=ExecutionLevel.L4_PLANNED,
        now=_T0 + timedelta(days=1),
    )
    assert stats.verified_successes == 5
    assert stats.success_rate == Decimal(1)

    cases = tuple(LoggedPolicyCase(record=row, available=available) for row in rows)
    experiment = ContextualBanditExperiment(
        BanditConfig(
            permitted=available,
            seed=14,
            exploration_numerator=0,
            exploration_denominator=1,
            minimum_arm_samples=1,
        )
    ).run(cases, minimum_supported=2)
    assert experiment.evaluation.disposition is PolicyEvaluationDisposition.IMPROVED

    store = OptimizationPolicyStore(_journal(path))
    baseline = OptimizationPolicy(
        policy_id=UUID("30000000-0000-4000-8000-000000000001"),
        revision=1,
        strategy_priority=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
        created_at=_T0 + timedelta(hours=1),
        dataset_id="controlled-baseline",
        model_id="deterministic-baseline",
    )
    candidate = OptimizationPolicy(
        policy_id=UUID("30000000-0000-4000-8000-000000000002"),
        revision=1,
        strategy_priority=(ExecutionLevel.L4_PLANNED, ExecutionLevel.L1_DIRECT),
        created_at=_T0 + timedelta(hours=1, seconds=1),
        dataset_id="controlled-history-v1",
        model_id="bandit-controlled-v1",
    )
    store.stage(baseline, occurred_at=_T0 + timedelta(hours=1))
    store.promote(
        baseline.policy_id,
        occurred_at=_T0 + timedelta(hours=1, seconds=1),
        expected_active=None,
    )
    store.stage(candidate, occurred_at=_T0 + timedelta(hours=1, seconds=2))
    store.promote(
        candidate.policy_id,
        occurred_at=_T0 + timedelta(hours=1, seconds=3),
        expected_active=baseline.policy_id,
    )
    assert OptimizationPolicyStore(_journal(path)).active_policy() == candidate

    selected = OptimizationPolicyRuntime(OptimizationPolicyStore(_journal(path))).choose(
        context=context, available=available
    )
    assert selected.level is ExecutionLevel.L4_PLANNED

    # A later controlled regression does not auto-promote or mutate authority.
    degraded = tuple(
        LoggedPolicyCase(
            record=_outcome(
                ident=200 + index,
                level=(ExecutionLevel.L4_PLANNED if index % 2 else ExecutionLevel.L1_DIRECT),
                success=index % 2 == 0,
                context=context,
            ),
            available=available,
        )
        for index in range(10)
    )
    degraded_report = ContextualBanditExperiment(
        BanditConfig(
            permitted=available,
            seed=14,
            exploration_numerator=0,
            exploration_denominator=1,
            minimum_arm_samples=1,
        )
    ).run(degraded, minimum_supported=2)
    assert degraded_report.evaluation.disposition in {
        PolicyEvaluationDisposition.REGRESSED,
        PolicyEvaluationDisposition.SAME,
        PolicyEvaluationDisposition.INSUFFICIENT_EVIDENCE,
    }

    restarted = OptimizationPolicyStore(_journal(path))
    restarted.rollback(
        occurred_at=_T0 + timedelta(hours=2),
        expected_active=candidate.policy_id,
        target_policy_id=baseline.policy_id,
    )
    assert OptimizationPolicyStore(_journal(path)).active_policy() == baseline


def test_m14_controlled_specialist_lifecycle_dataset_train_benchmark_fallback() -> None:
    dataset = GroundingSpecialistDataset.build(
        (
            GroundingExample(
                example_id=UUID(int=401),
                claim="file exists",
                evidence_text="native stat confirms file exists present",
                label=GroundingLabel.GROUNDED,
                provenance_source="canonical-verifier",
                provenance_reference="controlled:401",
            ),
            GroundingExample(
                example_id=UUID(int=402),
                claim="file exists",
                evidence_text="native stat confirms file missing absent",
                label=GroundingLabel.NOT_GROUNDED,
                provenance_source="canonical-verifier",
                provenance_reference="controlled:402",
            ),
            GroundingExample(
                example_id=UUID(int=403),
                claim="window visible",
                evidence_text="native observation confirms window visible present",
                label=GroundingLabel.GROUNDED,
                provenance_source="canonical-verifier",
                provenance_reference="controlled:403",
            ),
        )
    )
    model = LightweightGroundingModel.train(dataset)
    restored = LightweightGroundingModel.from_json(model.to_json())

    predictions = (
        restored.predict(
            claim="file exists",
            evidence_text="native stat confirms file exists present",
        ).label,
        restored.predict(
            claim="file exists",
            evidence_text="native stat confirms file missing absent",
        ).label,
    )
    report = SpecialistBenchmark().compare_labels(
        name="m14-grounding-controlled",
        baseline=(None, None),
        specialist=predictions,
        truth=(GroundingLabel.GROUNDED, GroundingLabel.NOT_GROUNDED),
    )
    assert report.disposition == "improved"

    unknown = restored.predict(
        claim="unseen claim",
        evidence_text="zebra quasar",
    )
    assert unknown.label is None
    assert unknown.confidence == Decimal(0)
