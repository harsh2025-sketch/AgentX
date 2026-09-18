"""M14 adaptive optimization unit/integration acceptance.

These tests use controlled canonical evidence. They do not claim real-provider,
real-user, or production statistical evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Lock, Thread
from uuid import UUID

import pytest

from agentx.adaptive_optimization import (
    AdaptiveOptimizationError,
    BanditConfig,
    ContextualBanditExperiment,
    DeterministicStrategyBaseline,
    EnvironmentKey,
    ExplicitCorrectionDataset,
    LoggedPolicyCase,
    OfflinePolicyEvaluator,
    OptimizationPolicy,
    OptimizationPolicyRuntime,
    OptimizationPolicyStore,
    PolicyEvaluationDisposition,
    PreferenceRankingExperiment,
    PreferenceScoringModel,
    SafeContextualBandit,
    SelectionSource,
    StrategyContext,
    StrategyOutcomeLedger,
    StrategyOutcomeRecord,
    StrategyStatisticsEngine,
)
from agentx.cognition.router import ExecutionLevel
from agentx.core.ids import TaskId
from agentx.core.reuse_efficiency import ExecutionEvidenceOutcome
from agentx.core.user_preferences import (
    PreferenceKey,
    PreferenceSource,
    UserPreference,
)
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.persistence import SQLiteDatabase, transaction
from agentx.strategy_performance_evidence import StrategyPerformanceEvidence

_T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
_CORRELATION = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _context(
    *,
    environment_id: str | None = "env-controlled",
    revision: str | None = "r1",
    family: str | None = "file-workflow",
    at: datetime = _T0,
) -> StrategyContext:
    return StrategyContext(
        environment=EnvironmentKey(environment_id, revision),
        task_family=family,
        captured_at=at,
    )


def _record(
    *,
    level: ExecutionLevel,
    success: bool | None,
    at: datetime,
    context: StrategyContext | None = None,
    elapsed_ms: int | None = 10,
    cost: Decimal | None = Decimal("0.01"),
    cost_unit: str | None = "USD",
    model_calls: int = 1,
    attempts: int = 1,
    confidence: Decimal | None = None,
    evidence_int: int | None = None,
) -> StrategyOutcomeRecord:
    if success is True:
        outcome = ExecutionEvidenceOutcome.VERIFIED_SUCCESS
        verification = True
    elif success is False:
        outcome = ExecutionEvidenceOutcome.FAILED
        verification = False
    else:
        outcome = ExecutionEvidenceOutcome.UNVERIFIED
        verification = None
    if cost is None:
        cost_unit = None
    evidence_id = (
        UUID(int=evidence_int) if evidence_int is not None else UUID(int=int(at.timestamp()) + 1)
    )
    performance = StrategyPerformanceEvidence(
        evidence_id=evidence_id,
        task_id=TaskId.create(),
        correlation_id=_CORRELATION,
        execution_level=level,
        outcome=outcome,
        observed_at=at,
        elapsed=None if elapsed_ms is None else timedelta(milliseconds=elapsed_ms),
        model_calls=model_calls,
        machine_actions=1,
        external_cost=cost,
        cost_unit=cost_unit,
        verification_passed=verification,
    )
    return StrategyOutcomeRecord(
        performance=performance,
        context=context or _context(at=at - timedelta(seconds=1)),
        attempt_count=attempts,
        predicted_confidence=confidence,
    )


def _journal(path: Path) -> EventJournal:
    return EventJournal(SQLiteDatabase(path.resolve()))


def test_deterministic_baseline_is_reproducible_and_does_not_adapt() -> None:
    baseline = DeterministicStrategyBaseline()
    context = _context()
    available = (ExecutionLevel.L3_GUIDED, ExecutionLevel.L5_EXPLORATORY)
    history = (
        _record(
            level=ExecutionLevel.L5_EXPLORATORY,
            success=True,
            at=_T0 + timedelta(seconds=2),
            evidence_int=1001,
        ),
    )

    first = baseline.choose(context=context, available=available, history=())
    second = baseline.choose(context=context, available=available, history=history)

    assert first == second
    assert first.level is ExecutionLevel.L3_GUIDED
    assert first.source is SelectionSource.BASELINE


def test_outcome_history_survives_real_reopen_and_is_canonical_truth(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    first = StrategyOutcomeLedger(_journal(path))
    success = _record(
        level=ExecutionLevel.L2_COMPILED,
        success=True,
        at=_T0 + timedelta(seconds=5),
        evidence_int=1101,
    )
    unverified = _record(
        level=ExecutionLevel.L4_PLANNED,
        success=None,
        at=_T0 + timedelta(seconds=6),
        evidence_int=1102,
    )

    first.append(success)
    first.append(unverified)

    reopened = StrategyOutcomeLedger(_journal(path))
    restored = reopened.read()
    assert restored == (success, unverified)
    assert restored[0].verified_success is True
    assert restored[1].verification_known is False


def test_concurrent_outcome_writers_preserve_complete_history(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    _journal(path).read()
    records = tuple(
        _record(
            level=ExecutionLevel.L1_DIRECT,
            success=index % 2 == 0,
            at=_T0 + timedelta(seconds=20 + index),
            evidence_int=1200 + index,
        )
        for index in range(6)
    )
    barrier = Barrier(len(records))
    lock = Lock()
    failures: list[BaseException] = []

    def writer(record: StrategyOutcomeRecord) -> None:
        try:
            barrier.wait()
            StrategyOutcomeLedger(_journal(path)).append(record)
        except BaseException as exc:
            with lock:
                failures.append(exc)

    threads = [Thread(target=writer, args=(record,)) for record in records]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert failures == []
    assert all(not thread.is_alive() for thread in threads)
    restored = StrategyOutcomeLedger(_journal(path)).read()
    assert {item.performance.evidence_id for item in restored} == {
        item.performance.evidence_id for item in records
    }


def test_environment_task_family_latency_cost_success_and_staleness_statistics() -> None:
    engine = StrategyStatisticsEngine()
    old = _record(
        level=ExecutionLevel.L1_DIRECT,
        success=True,
        at=_T0 - timedelta(days=30),
        context=_context(environment_id="env-old", revision="r0", family="old"),
        evidence_int=1301,
    )
    good = _record(
        level=ExecutionLevel.L1_DIRECT,
        success=True,
        at=_T0 + timedelta(seconds=1),
        context=_context(environment_id="env-a", revision="r1", family="files"),
        elapsed_ms=20,
        cost=Decimal("0.02"),
        model_calls=1,
        attempts=1,
        evidence_int=1302,
    )
    failed = _record(
        level=ExecutionLevel.L1_DIRECT,
        success=False,
        at=_T0 + timedelta(seconds=2),
        context=_context(environment_id="env-a", revision="r1", family="files"),
        elapsed_ms=40,
        cost=Decimal("0.04"),
        model_calls=2,
        attempts=2,
        evidence_int=1303,
    )
    unknown = _record(
        level=ExecutionLevel.L1_DIRECT,
        success=None,
        at=_T0 + timedelta(seconds=3),
        context=_context(environment_id=None, revision=None, family=None),
        elapsed_ms=None,
        cost=None,
        model_calls=0,
        attempts=1,
        evidence_int=1304,
    )
    records = (old, good, failed, unknown)

    stats = engine.summarize(
        records,
        strategy=ExecutionLevel.L1_DIRECT,
        now=_T0 + timedelta(days=1),
        max_age=timedelta(days=7),
    )

    assert stats.samples == 3
    assert stats.verified_successes == 1
    assert stats.verified_failures == 1
    assert stats.unverified == 1
    assert stats.success_rate == Decimal("0.5")
    assert stats.latency_samples == 2
    assert stats.mean_latency_microseconds == Decimal("30000")
    assert stats.cost_samples == 2
    assert stats.mean_external_cost == Decimal("0.03")
    assert stats.cost_unit == "USD"
    assert stats.model_calls_total == 3
    assert stats.attempts_total == 4
    assert stats.stale_excluded == 1

    environments = engine.by_environment(
        records,
        strategy=ExecutionLevel.L1_DIRECT,
        now=_T0 + timedelta(days=1),
        max_age=timedelta(days=7),
    )
    assert {(item.scope, item.revision) for item in environments} == {
        (None, None),
        ("env-a", "r1"),
    }
    families = engine.by_task_family(
        records,
        strategy=ExecutionLevel.L1_DIRECT,
        now=_T0 + timedelta(days=1),
        max_age=timedelta(days=7),
    )
    assert {item.scope for item in families} == {None, "files"}


def test_mixed_cost_units_remain_unknown_instead_of_being_summed() -> None:
    records = (
        _record(
            level=ExecutionLevel.L4_PLANNED,
            success=True,
            at=_T0 + timedelta(seconds=1),
            cost=Decimal("1"),
            cost_unit="USD",
            evidence_int=1401,
        ),
        _record(
            level=ExecutionLevel.L4_PLANNED,
            success=True,
            at=_T0 + timedelta(seconds=2),
            cost=Decimal("2"),
            cost_unit="credits",
            evidence_int=1402,
        ),
    )
    stats = StrategyStatisticsEngine().summarize(
        records,
        strategy=ExecutionLevel.L4_PLANNED,
        now=_T0 + timedelta(seconds=3),
    )

    assert stats.cost_samples == 2
    assert stats.mean_external_cost is None
    assert stats.cost_unit is None


def test_confidence_calibration_requires_verified_outcomes_and_minimum_samples() -> None:
    engine = StrategyStatisticsEngine()
    insufficient = tuple(
        _record(
            level=ExecutionLevel.L1_DIRECT,
            success=True,
            at=_T0 + timedelta(seconds=index + 1),
            confidence=Decimal("0.8"),
            evidence_int=1500 + index,
        )
        for index in range(2)
    )
    report = engine.calibration(insufficient, minimum_samples=3)
    assert report.sufficient is False
    assert report.brier_score is None

    complete = (
        *insufficient,
        _record(
            level=ExecutionLevel.L1_DIRECT,
            success=False,
            at=_T0 + timedelta(seconds=3),
            confidence=Decimal("0.4"),
            evidence_int=1503,
        ),
    )
    calibrated = engine.calibration(complete, minimum_samples=3)
    assert calibrated.sufficient is True
    assert calibrated.samples == 3
    assert calibrated.empirical_success_rate == Decimal(2) / Decimal(3)
    assert calibrated.brier_score is not None


def _bandit_cases() -> tuple[LoggedPolicyCase, ...]:
    context = _context(environment_id="env-bandit", revision="r1", family="compose")
    available = (ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED)
    rows: list[LoggedPolicyCase] = []
    # Chronological training 60%: L1 fails, L4 succeeds.
    for index, (level, success) in enumerate(
        (
            (ExecutionLevel.L1_DIRECT, False),
            (ExecutionLevel.L4_PLANNED, True),
            (ExecutionLevel.L1_DIRECT, False),
            (ExecutionLevel.L4_PLANNED, True),
            (ExecutionLevel.L1_DIRECT, False),
            (ExecutionLevel.L4_PLANNED, True),
            # Evaluation has logged support for both baseline (L1) and candidate (L4).
            (ExecutionLevel.L1_DIRECT, False),
            (ExecutionLevel.L4_PLANNED, True),
            (ExecutionLevel.L1_DIRECT, False),
            (ExecutionLevel.L4_PLANNED, True),
        )
    ):
        rows.append(
            LoggedPolicyCase(
                record=_record(
                    level=level,
                    success=success,
                    at=_T0 + timedelta(minutes=index + 1),
                    context=context,
                    evidence_int=1600 + index,
                ),
                available=available,
            )
        )
    return tuple(rows)


def test_contextual_bandit_is_seeded_bounded_and_improves_controlled_offline_cases() -> None:
    cases = _bandit_cases()
    config = BanditConfig(
        permitted=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
        seed=42,
        exploration_numerator=0,
        exploration_denominator=10,
        minimum_arm_samples=1,
    )
    experiment = ContextualBanditExperiment(config)

    first = experiment.run(cases, minimum_supported=2)
    second = experiment.run(cases, minimum_supported=2)

    assert first == second
    assert first.evaluation.disposition is PolicyEvaluationDisposition.IMPROVED
    assert first.evaluation.baseline_success_rate == Decimal(0)
    assert first.evaluation.candidate_success_rate == Decimal(1)
    assert first.evaluation.baseline_supported == 2
    assert first.evaluation.candidate_supported == 2
    assert first.evaluation.future_data_leakage is False


def test_bandit_can_only_choose_intersection_of_available_and_permitted() -> None:
    context = _context()
    history = tuple(case.record for case in _bandit_cases()[:6])
    bandit = SafeContextualBandit(
        BanditConfig(
            permitted=(ExecutionLevel.L4_PLANNED,),
            seed=7,
            exploration_numerator=0,
            exploration_denominator=1,
            minimum_arm_samples=1,
        )
    )

    decision = bandit.decide(
        context=context,
        available=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
        history=history,
    )
    assert decision.selection.level is ExecutionLevel.L4_PLANNED

    with pytest.raises(AdaptiveOptimizationError, match="permitted"):
        bandit.decide(
            context=context,
            available=(ExecutionLevel.L1_DIRECT,),
            history=history,
        )


def _correction(
    *,
    at: datetime,
    preferred: ExecutionLevel,
    rejected: ExecutionLevel | None,
    family: str = "compose",
    source: PreferenceSource = PreferenceSource.USER_CORRECTION,
) -> UserPreference:
    value: dict[str, object] = {
        "preferred_strategy": preferred.value,
        "task_family": family,
    }
    if rejected is not None:
        value["rejected_strategy"] = rejected.value
    return UserPreference.create(
        key=PreferenceKey.WORKFLOW_PREFERENCE,
        value=value,
        source=source,
        recorded_at=at,
    )


def test_explicit_correction_dataset_rejects_inferred_semantics_and_versions_data() -> None:
    correction = _correction(
        at=_T0,
        preferred=ExecutionLevel.L4_PLANNED,
        rejected=ExecutionLevel.L1_DIRECT,
    )
    merely_explicit = _correction(
        at=_T0 + timedelta(seconds=1),
        preferred=ExecutionLevel.L1_DIRECT,
        rejected=ExecutionLevel.L4_PLANNED,
        source=PreferenceSource.USER_EXPLICIT,
    )

    first = ExplicitCorrectionDataset.from_preferences((merely_explicit, correction))
    second = ExplicitCorrectionDataset.from_preferences((correction, merely_explicit))

    assert len(first.examples) == 1
    assert first.examples[0].preference_id == correction.preference_id
    assert first.dataset_id == second.dataset_id
    assert first.dataset_id.startswith("sha256:")


def test_preference_model_uses_only_explicit_corrections_and_low_data_falls_back() -> None:
    context = _context(family="compose")
    empty = ExplicitCorrectionDataset.from_preferences(())
    empty_model = PreferenceScoringModel.train(empty)
    fallback = empty_model.choose(
        context=context,
        available=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
    )
    assert fallback.level is ExecutionLevel.L1_DIRECT
    assert "baseline" in fallback.reason_code

    corrections = ExplicitCorrectionDataset.from_preferences(
        (
            _correction(
                at=_T0,
                preferred=ExecutionLevel.L4_PLANNED,
                rejected=ExecutionLevel.L1_DIRECT,
            ),
            _correction(
                at=_T0 + timedelta(seconds=1),
                preferred=ExecutionLevel.L4_PLANNED,
                rejected=ExecutionLevel.L1_DIRECT,
            ),
        )
    )
    model = PreferenceScoringModel.train(corrections)
    ranked = model.choose(
        context=context,
        available=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
    )
    assert ranked.level is ExecutionLevel.L4_PLANNED


def test_preference_ranking_is_prequential_and_truthfully_reports_cold_start_error() -> None:
    dataset = ExplicitCorrectionDataset.from_preferences(
        (
            _correction(
                at=_T0,
                preferred=ExecutionLevel.L4_PLANNED,
                rejected=ExecutionLevel.L1_DIRECT,
            ),
            _correction(
                at=_T0 + timedelta(seconds=1),
                preferred=ExecutionLevel.L4_PLANNED,
                rejected=ExecutionLevel.L1_DIRECT,
            ),
            _correction(
                at=_T0 + timedelta(seconds=2),
                preferred=ExecutionLevel.L4_PLANNED,
                rejected=ExecutionLevel.L1_DIRECT,
            ),
        )
    )
    report = PreferenceRankingExperiment().run(dataset)

    assert report.samples == 3
    # First sample has no prior user correction, so deterministic baseline loses it.
    assert report.correct == 2
    assert report.accuracy == Decimal(2) / Decimal(3)


def test_policy_promotion_restart_regression_and_rollback(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    first = OptimizationPolicyStore(_journal(path))
    baseline = OptimizationPolicy(
        policy_id=UUID("20000000-0000-4000-8000-000000000001"),
        revision=1,
        strategy_priority=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
        created_at=_T0,
        dataset_id="sha256:baseline",
        model_id="baseline-v1",
    )
    candidate = OptimizationPolicy(
        policy_id=UUID("20000000-0000-4000-8000-000000000002"),
        revision=1,
        strategy_priority=(ExecutionLevel.L4_PLANNED, ExecutionLevel.L1_DIRECT),
        created_at=_T0 + timedelta(seconds=1),
        dataset_id="sha256:candidate",
        model_id="candidate-v1",
    )
    first.stage(baseline, occurred_at=_T0)
    first.promote(
        baseline.policy_id,
        occurred_at=_T0 + timedelta(seconds=1),
        expected_active=None,
    )
    first.stage(candidate, occurred_at=_T0 + timedelta(seconds=2))
    first.promote(
        candidate.policy_id,
        occurred_at=_T0 + timedelta(seconds=3),
        expected_active=baseline.policy_id,
    )

    restarted = OptimizationPolicyStore(_journal(path))
    assert restarted.active_policy() == candidate

    restarted.rollback(
        occurred_at=_T0 + timedelta(seconds=4),
        expected_active=candidate.policy_id,
        target_policy_id=baseline.policy_id,
    )
    reopened = OptimizationPolicyStore(_journal(path))
    assert reopened.active_policy() == baseline

    runtime = OptimizationPolicyRuntime(reopened)
    selection = runtime.choose(
        context=_context(),
        available=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
    )
    assert selection.level is ExecutionLevel.L1_DIRECT


def test_policy_compare_and_swap_blocks_stale_concurrent_promotion(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    store = OptimizationPolicyStore(_journal(path))
    baseline = OptimizationPolicy(
        policy_id=UUID("21000000-0000-4000-8000-000000000001"),
        revision=1,
        strategy_priority=(ExecutionLevel.L1_DIRECT,),
        created_at=_T0,
    )
    candidate = OptimizationPolicy(
        policy_id=UUID("21000000-0000-4000-8000-000000000002"),
        revision=1,
        strategy_priority=(ExecutionLevel.L4_PLANNED,),
        created_at=_T0,
    )
    store.stage(baseline, occurred_at=_T0)
    store.stage(candidate, occurred_at=_T0)
    store.promote(
        baseline.policy_id,
        occurred_at=_T0 + timedelta(seconds=1),
        expected_active=None,
    )

    with pytest.raises(AdaptiveOptimizationError, match="changed"):
        store.promote(
            candidate.policy_id,
            occurred_at=_T0 + timedelta(seconds=2),
            expected_active=None,
        )


def test_corrupt_active_policy_fails_back_to_deterministic_baseline(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    journal = _journal(path)
    store = OptimizationPolicyStore(journal)
    policy = OptimizationPolicy(
        policy_id=UUID("22000000-0000-4000-8000-000000000001"),
        revision=1,
        strategy_priority=(ExecutionLevel.L4_PLANNED,),
        created_at=_T0,
    )
    store.stage(policy, occurred_at=_T0)
    store.promote(
        policy.policy_id,
        occurred_at=_T0 + timedelta(seconds=1),
        expected_active=None,
    )
    with journal.database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_event_journal SET event_json = ? WHERE sequence = "
            "(SELECT MAX(sequence) FROM agentx_event_journal)",
            ("{corrupt-policy-event",),
        )

    selection = OptimizationPolicyRuntime(OptimizationPolicyStore(_journal(path))).choose(
        context=_context(),
        available=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
    )
    assert selection.level is ExecutionLevel.L1_DIRECT
    assert selection.reason_code == "no-valid-active-policy"


def test_offline_evaluator_reports_insufficient_support_instead_of_improvement() -> None:
    report = OfflinePolicyEvaluator().evaluate(
        _bandit_cases()[:3],
        candidate=SafeContextualBandit(
            BanditConfig(
                seed=0,
                exploration_numerator=0,
                exploration_denominator=1,
                minimum_arm_samples=1,
            )
        ),
        minimum_supported=3,
    )
    assert report.disposition is PolicyEvaluationDisposition.INSUFFICIENT_EVIDENCE
