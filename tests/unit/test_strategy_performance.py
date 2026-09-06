"""Tests for the canonical A8.01 strategy-performance record and aggregations."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalOutcome
from agentx.core.events import VerificationPayload
from agentx.core.failure_taxonomy import CANONICAL_FAILURE_CATEGORIES, FailureCategory
from agentx.core.ids import (
    CapabilityId,
    EpisodeId,
    ProcedureId,
    StrategyPerformanceId,
    TaskId,
)
from agentx.core.strategy_performance import (
    STRATEGY_PERFORMANCE_SCHEMA_VERSION,
    ExecutionStrategyLevel,
    MeasuredValueAggregate,
    StrategyAggregationDimension,
    StrategyPerformanceAggregate,
    StrategyPerformanceDeserializationError,
    StrategyPerformanceGroup,
    StrategyPerformanceRecord,
    StrategyPerformanceValidationError,
    UnsupportedStrategyPerformanceSchemaVersionError,
    aggregate_strategy_records,
    group_strategy_records,
)

_LEVELS = tuple(ExecutionStrategyLevel)


def _verified(
    level: ExecutionStrategyLevel = ExecutionStrategyLevel.L2_COMPILED,
    *,
    index: int = 1,
    strategy_name: str | None = "resolve",
    strategy_version: str | None = "v1",
    scope: str | None = "web-scrape",
    environment: str | None = "windows-11",
    cost: float | None = 1.0,
    cost_unit: str | None = "tokens",
    latency_seconds: float | None = 0.5,
) -> StrategyPerformanceRecord:
    return StrategyPerformanceRecord(
        record_id=StrategyPerformanceId(UUID(int=40_000 + index)),
        level=level,
        outcome=CausalOutcome.VERIFIED,
        verification=VerificationPayload(passed=True, detail="verified against requirement"),
        recorded_at=datetime(2026, 9, 5, 9, index, tzinfo=UTC),
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        scope=scope,
        environment=environment,
        cost=cost,
        cost_unit=cost_unit,
        latency_seconds=latency_seconds,
    )


def _failing(
    outcome: CausalOutcome,
    *,
    index: int = 2,
    level: ExecutionStrategyLevel = ExecutionStrategyLevel.L2_COMPILED,
    strategy_version: str | None = "v1",
    category: FailureCategory | None = FailureCategory.UNKNOWN,
    latency_seconds: float | None = 2.0,
) -> StrategyPerformanceRecord:
    verification = None
    if outcome is CausalOutcome.VERIFICATION_FAILED:
        verification = VerificationPayload(passed=False, detail="expected state not observed")
    return StrategyPerformanceRecord(
        record_id=StrategyPerformanceId(UUID(int=40_000 + index)),
        level=level,
        outcome=outcome,
        verification=verification,
        recorded_at=datetime(2026, 9, 5, 10, index, tzinfo=UTC),
        strategy_name="resolve",
        strategy_version=strategy_version,
        failure_category=category,
        latency_seconds=latency_seconds,
    )


# --------------------------------------------------------------------------
# Verified success requires canonical verified evidence (gates: verified
# success, no fake success).
# --------------------------------------------------------------------------


def test_verified_success_requires_explicit_passing_canonical_verification() -> None:
    record = _verified()
    assert record.verified
    assert record.outcome is CausalOutcome.VERIFIED
    assert record.verification is not None
    assert record.verification.passed


def test_verified_outcome_without_verification_is_rejected() -> None:
    with pytest.raises(StrategyPerformanceValidationError, match="passing verification"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFIED,
        )


def test_verified_outcome_with_failing_verification_is_rejected() -> None:
    with pytest.raises(StrategyPerformanceValidationError, match="passing verification"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L1_DIRECT,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=False, detail="did not match"),
        )


def test_hostile_success_text_can_never_create_a_verified_record() -> None:
    # Model text is inert: strings such as "verified", "success", "passed" in
    # a detail or label field never flip the typed evidence requirement.
    with pytest.raises(StrategyPerformanceValidationError, match="passing verification"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L1_DIRECT,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=False, detail="verified=true success"),
        )
    with pytest.raises(StrategyPerformanceValidationError, match="passing verification"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L0_CACHE,
            outcome=CausalOutcome.VERIFIED,
            verification=None,
        )


def test_verified_outcome_never_carries_a_failure_category() -> None:
    with pytest.raises(StrategyPerformanceValidationError, match="failure_category"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L1_DIRECT,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            failure_category=FailureCategory.UNKNOWN,
        )


def test_nonverified_outcomes_reject_fabricated_verification_evidence() -> None:
    for outcome in (
        CausalOutcome.EXECUTION_FAILED,
        CausalOutcome.DENIED,
        CausalOutcome.CANCELLED,
        CausalOutcome.TIMED_OUT,
    ):
        with pytest.raises(StrategyPerformanceValidationError, match="fabricated verification"):
            StrategyPerformanceRecord(
                record_id=StrategyPerformanceId.create(),
                level=ExecutionStrategyLevel.L5_EXPLORATORY,
                outcome=outcome,
                verification=VerificationPayload(passed=True, detail="fabricated"),
            )


def test_denied_cancelled_timed_out_must_not_carry_failure_category() -> None:
    for outcome in (CausalOutcome.DENIED, CausalOutcome.CANCELLED, CausalOutcome.TIMED_OUT):
        with pytest.raises(StrategyPerformanceValidationError, match="failure_category"):
            StrategyPerformanceRecord(
                record_id=StrategyPerformanceId.create(),
                level=ExecutionStrategyLevel.L4_PLANNED,
                outcome=outcome,
                failure_category=FailureCategory.PERMISSION,
            )


def test_unknown_is_a_valid_explicit_category_for_execution_failure() -> None:
    record = _failing(CausalOutcome.EXECUTION_FAILED, category=FailureCategory.UNKNOWN)
    assert record.outcome is CausalOutcome.EXECUTION_FAILED
    assert record.failure_category is FailureCategory.UNKNOWN


# --------------------------------------------------------------------------
# Verification failure and execution failure gates.
# --------------------------------------------------------------------------


def test_verification_failure_requires_explicit_failing_verification() -> None:
    with pytest.raises(StrategyPerformanceValidationError, match="failing verification"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFICATION_FAILED,
            verification=VerificationPayload(passed=True, detail="should not pass"),
        )
    with pytest.raises(StrategyPerformanceValidationError, match="failing verification"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFICATION_FAILED,
        )


def test_failure_outcomes_require_an_explicit_failure_category() -> None:
    for outcome in (CausalOutcome.VERIFICATION_FAILED, CausalOutcome.EXECUTION_FAILED):
        with pytest.raises(StrategyPerformanceValidationError, match="failure_category"):
            StrategyPerformanceRecord(
                record_id=StrategyPerformanceId.create(),
                level=ExecutionStrategyLevel.L3_GUIDED,
                outcome=outcome,
                verification=(
                    VerificationPayload(passed=False, detail="no")
                    if outcome is CausalOutcome.VERIFICATION_FAILED
                    else None
                ),
            )


def test_verification_failure_with_category_is_well_formed() -> None:
    record = _failing(CausalOutcome.VERIFICATION_FAILED, category=FailureCategory.VERIFICATION)
    assert not record.verified
    assert record.verification is not None
    assert not record.verification.passed
    assert record.failure_category is FailureCategory.VERIFICATION


# --------------------------------------------------------------------------
# Scope / environment / different strategy versions.
# --------------------------------------------------------------------------


def test_scope_and_environment_are_recorded_and_roundtrip() -> None:
    record = _verified(scope="browser-automation", environment="windows-11")
    decoded = StrategyPerformanceRecord.from_json(record.to_json())
    assert decoded.scope == "browser-automation"
    assert decoded.environment == "windows-11"


def test_strategy_versions_are_recorded_and_compared_through_groups() -> None:
    v1 = _verified(index=10, strategy_version="v1", cost=9.0, latency_seconds=0.9)
    v2 = _verified(index=11, strategy_version="v2", cost=3.0, latency_seconds=0.4)
    groups = group_strategy_records(
        (v1, v2), dimension=StrategyAggregationDimension.STRATEGY_VERSION
    )
    by_key = {group.key: group for group in groups}
    assert by_key["v1"].aggregate.costs[0][1].total == 9.0
    assert by_key["v2"].aggregate.costs[0][1].total == 3.0
    assert by_key["v2"].aggregate.latency_seconds.mean == 0.4


def test_identity_refs_roundtrip() -> None:
    procedure_id = ProcedureId(UUID(int=5_001))
    capability_id = CapabilityId(UUID(int=6_001))
    task_id = TaskId(UUID(int=7_001))
    episode_id = EpisodeId(UUID(int=8_001))
    correlation_id = UUID(int=9_001)
    record = StrategyPerformanceRecord.create(
        level=ExecutionStrategyLevel.L2_COMPILED,
        outcome=CausalOutcome.VERIFIED,
        verification=VerificationPayload(passed=True, detail="ok"),
        procedure_id=procedure_id,
        procedure_revision=3,
        capability_id=capability_id,
        task_id=task_id,
        episode_id=episode_id,
        correlation_id=correlation_id,
    )
    decoded = StrategyPerformanceRecord.from_json(record.to_json())
    assert decoded.procedure_id == procedure_id
    assert decoded.procedure_revision == 3
    assert decoded.capability_id == capability_id
    assert decoded.task_id == task_id
    assert decoded.episode_id == episode_id
    assert decoded.correlation_id == correlation_id


def test_procedure_revision_requires_procedure_id() -> None:
    with pytest.raises(StrategyPerformanceValidationError, match="requires a procedure_id"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            procedure_revision=2,
        )


# --------------------------------------------------------------------------
# Cost / latency / malformed metrics gates.
# --------------------------------------------------------------------------


def test_cost_requires_unit_and_unit_requires_cost() -> None:
    with pytest.raises(StrategyPerformanceValidationError, match="cost requires a cost_unit"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            cost=1.5,
        )
    with pytest.raises(StrategyPerformanceValidationError, match="requires a measured cost"):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            cost_unit="tokens",
        )


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), -0.1])
def test_malformed_cost_is_rejected(bad_value: float) -> None:
    with pytest.raises(StrategyPerformanceValidationError):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            cost=bad_value,
            cost_unit="tokens",
        )


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), -1.0])
def test_malformed_latency_is_rejected(bad_value: float) -> None:
    with pytest.raises(StrategyPerformanceValidationError):
        StrategyPerformanceRecord(
            record_id=StrategyPerformanceId.create(),
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            latency_seconds=bad_value,
        )


def test_boolean_is_not_a_valid_measurement() -> None:
    with pytest.raises(TypeError):
        StrategyPerformanceRecord.create(
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            cost=True,  # bool is not a measurement (rejected at runtime)
            cost_unit="tokens",
        )


def test_unmeasured_fields_roundtrip_as_none() -> None:
    record = _verified(cost=None, cost_unit=None, latency_seconds=None)
    decoded = StrategyPerformanceRecord.from_json(record.to_json())
    assert decoded.cost is None
    assert decoded.cost_unit is None
    assert decoded.latency_seconds is None


def test_latency_and_cost_measured_fields_roundtrip() -> None:
    record = _verified(cost=42.25, cost_unit="calls", latency_seconds=3.75)
    decoded = StrategyPerformanceRecord.from_json(record.to_json())
    assert decoded.cost == 42.25
    assert decoded.cost_unit == "calls"
    assert decoded.latency_seconds == 3.75


def test_zero_latency_and_zero_cost_are_valid_measured_values() -> None:
    record = _verified(cost=0.0, cost_unit="tokens", latency_seconds=0.0)
    assert record.cost == 0.0
    assert record.latency_seconds == 0.0


def test_timestamps_roundtrip_utc_and_relationships_are_validated() -> None:
    started = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
    ended = datetime(2026, 9, 5, 9, 0, 30, tzinfo=UTC)
    record = StrategyPerformanceRecord.create(
        level=ExecutionStrategyLevel.L1_DIRECT,
        outcome=CausalOutcome.VERIFIED,
        verification=VerificationPayload(passed=True, detail="ok"),
        started_at=started,
        ended_at=ended,
    )
    decoded = StrategyPerformanceRecord.from_json(record.to_json())
    assert decoded.started_at == started
    assert decoded.ended_at == ended

    with pytest.raises(StrategyPerformanceValidationError, match="earlier than started_at"):
        StrategyPerformanceRecord.create(
            level=ExecutionStrategyLevel.L1_DIRECT,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            started_at=ended,
            ended_at=started,
        )
    with pytest.raises(StrategyPerformanceValidationError, match="requires started_at"):
        StrategyPerformanceRecord.create(
            level=ExecutionStrategyLevel.L1_DIRECT,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            ended_at=ended,
        )


# --------------------------------------------------------------------------
# Record identity, serialization, and schema discipline.
# --------------------------------------------------------------------------


def test_level_vocabulary_is_the_canonical_l0_to_l5_execution_levels() -> None:
    from agentx.cognition.router import CANONICAL_EXECUTION_LEVELS

    assert tuple(level.value for level in _LEVELS) == tuple(
        level.value for level in CANONICAL_EXECUTION_LEVELS
    )
    assert tuple(level.name for level in _LEVELS) == tuple(
        level.name for level in CANONICAL_EXECUTION_LEVELS
    )


def test_record_id_identity_and_immutability() -> None:
    record_id = StrategyPerformanceId(UUID(int=1234))
    record = StrategyPerformanceRecord(
        record_id=record_id,
        level=ExecutionStrategyLevel.L0_CACHE,
        outcome=CausalOutcome.VERIFIED,
        verification=VerificationPayload(passed=True, detail="ok"),
    )
    assert record.record_id == record_id
    with pytest.raises(AttributeError):
        record.strategy_name = "other"  # type: ignore[misc]


def test_deterministic_json_roundtrip_preserves_everything() -> None:
    record = _verified(
        strategy_name="resolve-fast",
        strategy_version="v9",
        scope="deep",
        environment="windows-server-2025",
        cost=7.5,
        cost_unit="tokens",
        latency_seconds=1.25,
    )
    decoded = StrategyPerformanceRecord.from_json(record.to_json())
    assert decoded == record
    assert decoded.to_json() == record.to_json()


def test_schema_version_is_pinned() -> None:
    record = _verified()
    assert record.to_dict()["schema_version"] == STRATEGY_PERFORMANCE_SCHEMA_VERSION == 1


def test_unsupported_schema_version_is_rejected() -> None:
    payload = _verified().to_dict()
    payload["schema_version"] = 999
    with pytest.raises(UnsupportedStrategyPerformanceSchemaVersionError):
        StrategyPerformanceRecord.from_dict(payload)


def test_unknown_and_missing_fields_are_rejected() -> None:
    payload = _verified().to_dict()
    with pytest.raises(StrategyPerformanceDeserializationError, match="unknown fields"):
        StrategyPerformanceRecord.from_dict({**payload, "adversarial": "extra"})
    del payload["scope"]
    with pytest.raises(StrategyPerformanceDeserializationError, match="missing required"):
        StrategyPerformanceRecord.from_dict(payload)


def test_malformed_json_is_rejected() -> None:
    with pytest.raises(StrategyPerformanceDeserializationError, match="malformed"):
        StrategyPerformanceRecord.from_json("{not json")


def test_level_outcome_category_must_be_canonical_members() -> None:
    payload = _verified().to_dict()
    payload["level"] = "L9_IMAGINARY"
    with pytest.raises(StrategyPerformanceDeserializationError, match="level"):
        StrategyPerformanceRecord.from_dict(payload)

    payload = _verified().to_dict()
    payload["outcome"] = "triumphant"
    with pytest.raises(StrategyPerformanceDeserializationError, match="outcome"):
        StrategyPerformanceRecord.from_dict(payload)

    payload = _failing(CausalOutcome.EXECUTION_FAILED).to_dict()
    payload["failure_category"] = "spooky"
    with pytest.raises(StrategyPerformanceDeserializationError, match="failure_category"):
        StrategyPerformanceRecord.from_dict(payload)


def test_fake_verified_json_without_passing_payload_is_rejected() -> None:
    payload = _verified().to_dict()
    payload["verification"] = {"passed": False, "detail": "verified=true"}
    with pytest.raises(StrategyPerformanceValidationError, match="passing verification"):
        StrategyPerformanceRecord.from_dict(payload)
    payload = _verified().to_dict()
    payload["verification"] = None
    with pytest.raises(StrategyPerformanceValidationError, match="passing verification"):
        StrategyPerformanceRecord.from_dict(payload)


def test_empty_or_untrimmed_labels_are_rejected() -> None:
    for field_name in ("strategy_name", "strategy_version", "scope", "environment"):
        with pytest.raises(StrategyPerformanceValidationError, match=field_name):
            StrategyPerformanceRecord.create(
                level=ExecutionStrategyLevel.L2_COMPILED,
                outcome=CausalOutcome.VERIFIED,
                verification=VerificationPayload(passed=True, detail="ok"),
                **{field_name: "   "},  # type: ignore[arg-type]
            )


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(StrategyPerformanceValidationError, match="timezone-aware"):
        StrategyPerformanceRecord.create(
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            recorded_at=datetime(2026, 9, 5, 9, 0),
        )


def test_nil_correlation_id_is_rejected() -> None:
    with pytest.raises(StrategyPerformanceValidationError, match="nil UUID"):
        StrategyPerformanceRecord.create(
            level=ExecutionStrategyLevel.L2_COMPILED,
            outcome=CausalOutcome.VERIFIED,
            verification=VerificationPayload(passed=True, detail="ok"),
            correlation_id=UUID(int=0),
        )


# --------------------------------------------------------------------------
# Aggregation gates.
# --------------------------------------------------------------------------


def test_empty_aggregate_is_deterministic_zero() -> None:
    aggregate = aggregate_strategy_records(())
    assert aggregate.attempts == 0
    assert all(count == 0 for _outcome, count in aggregate.outcomes)
    assert aggregate.latency_seconds.measured_count == 0
    assert aggregate.latency_seconds.mean is None
    assert aggregate.costs == ()


def test_aggregate_counts_verified_and_failure_outcomes() -> None:
    records = (
        _verified(index=1),
        _verified(index=2),
        _failing(CausalOutcome.VERIFICATION_FAILED, index=3),
        _failing(CausalOutcome.EXECUTION_FAILED, index=4),
        _failing(CausalOutcome.DENIED, index=5, category=None),
    )
    aggregate = aggregate_strategy_records(records)
    counts = dict(aggregate.outcomes)
    assert aggregate.attempts == 5
    assert counts[CausalOutcome.VERIFIED] == 2
    assert counts[CausalOutcome.VERIFICATION_FAILED] == 1
    assert counts[CausalOutcome.EXECUTION_FAILED] == 1
    assert counts[CausalOutcome.DENIED] == 1


def test_aggregate_reports_all_canonical_outcomes_including_zero_counts() -> None:
    aggregate = aggregate_strategy_records((_verified(index=1),))
    assert tuple(outcome for outcome, _count in aggregate.outcomes) == tuple(CausalOutcome)
    assert tuple(category for category, _count in aggregate.failure_categories) == tuple(
        CANONICAL_FAILURE_CATEGORIES
    )


def test_aggregate_failure_category_counts_only_observed_categories() -> None:
    records = (
        _failing(
            CausalOutcome.EXECUTION_FAILED,
            index=1,
            category=FailureCategory.PROCEDURE,
        ),
        _failing(
            CausalOutcome.VERIFICATION_FAILED,
            index=2,
            category=FailureCategory.VERIFICATION,
        ),
        _failing(
            CausalOutcome.EXECUTION_FAILED,
            index=3,
            category=FailureCategory.UNKNOWN,
        ),
    )
    counts = dict(aggregate_strategy_records(records).failure_categories)
    assert counts[FailureCategory.PROCEDURE] == 1
    assert counts[FailureCategory.VERIFICATION] == 1
    assert counts[FailureCategory.UNKNOWN] == 1
    assert counts[FailureCategory.CAPABILITY] == 0


def test_aggregate_latency_statistics_use_only_measured_records() -> None:
    records = (
        _verified(index=1, latency_seconds=1.0),
        _verified(index=2, latency_seconds=3.0),
        _verified(index=3, latency_seconds=None),
    )
    latency = aggregate_strategy_records(records).latency_seconds
    assert latency == MeasuredValueAggregate(
        measured_count=2, total=4.0, minimum=1.0, maximum=3.0, mean=2.0
    )


def test_aggregate_costs_are_kept_per_unit() -> None:
    records = (
        _verified(index=1, cost=10.0, cost_unit="tokens"),
        _verified(index=2, cost=30.0, cost_unit="tokens"),
        _verified(index=3, cost=2.5, cost_unit="usd"),
    )
    costs = aggregate_strategy_records(records).costs
    assert costs == (
        ("tokens", MeasuredValueAggregate(2, 40.0, 10.0, 30.0, 20.0)),
        ("usd", MeasuredValueAggregate(1, 2.5, 2.5, 2.5, 2.5)),
    )


def test_aggregate_ignores_input_order() -> None:
    a = _verified(index=1, cost=1.0, latency_seconds=0.1)
    b = _verified(index=2, cost=2.0, latency_seconds=0.2)
    assert aggregate_strategy_records((a, b)) == aggregate_strategy_records((b, a))


def test_group_records_is_deterministic_and_merges_equal_keys() -> None:
    records = (
        _verified(index=1, strategy_name="alpha", scope="s1", environment="e1"),
        _verified(index=2, strategy_name="alpha", scope="s1", environment="e1"),
        _verified(index=3, strategy_name="beta", scope="s2", environment="e1"),
        _verified(index=4, strategy_name=None, scope="s1", environment="e1"),
    )
    by_name = group_strategy_records(records, dimension=StrategyAggregationDimension.STRATEGY_NAME)
    assert tuple(group.key for group in by_name) == (None, "alpha", "beta")
    assert by_name[1].aggregate.attempts == 2
    assert by_name[1].aggregate.attempts == 2

    by_scope = group_strategy_records(records, dimension=StrategyAggregationDimension.SCOPE)
    assert tuple(group.key for group in by_scope) == ("s1", "s2")

    reversed_groups = group_strategy_records(
        tuple(reversed(records)), dimension=StrategyAggregationDimension.STRATEGY_NAME
    )
    assert reversed_groups == by_name


def test_group_by_level_follows_canonical_l0_to_l5_order() -> None:
    records = (
        _verified(index=1, level=ExecutionStrategyLevel.L5_EXPLORATORY),
        _verified(index=2, level=ExecutionStrategyLevel.L0_CACHE),
        _verified(index=3, level=ExecutionStrategyLevel.L2_COMPILED),
        _verified(index=4, level=ExecutionStrategyLevel.L5_EXPLORATORY),
    )
    groups = group_strategy_records(records, dimension=StrategyAggregationDimension.LEVEL)
    assert [group.key for group in groups] == [
        ExecutionStrategyLevel.L0_CACHE.value,
        ExecutionStrategyLevel.L2_COMPILED.value,
        ExecutionStrategyLevel.L5_EXPLORATORY.value,
    ]
    assert groups[2].aggregate.attempts == 2


def test_group_shape_contract() -> None:
    record = _verified(index=1)
    groups = group_strategy_records((record,), dimension=StrategyAggregationDimension.ENVIRONMENT)
    assert len(groups) == 1
    group = groups[0]
    assert isinstance(group, StrategyPerformanceGroup)
    assert group.key == "windows-11"
    assert isinstance(group.aggregate, StrategyPerformanceAggregate)
    assert group.aggregate.attempts == 1


def test_group_dimension_must_be_canonical_dimension() -> None:
    with pytest.raises(TypeError, match="dimension"):
        group_strategy_records((_verified(index=1),), dimension="level")  # type: ignore[arg-type]


def test_aggregation_rejects_non_record_inputs() -> None:
    with pytest.raises(TypeError, match="StrategyPerformanceRecord"):
        aggregate_strategy_records(("nope",))  # type: ignore[arg-type]


def test_execution_levels_mirror_router_contract_placement() -> None:
    # The A8.01 level vocabulary is the A2.07 canonical hierarchy; both order
    # and member values must stay aligned (asserted again in the architecture
    # test suite against the Router module itself).
    assert _LEVELS[0].value == "L0_CACHE"
    assert _LEVELS[5].value == "L5_EXPLORATORY"


def test_math_helpers_smoke() -> None:
    # Ensure nothing leaks through non-finite arithmetic when summing values.
    records = (_verified(index=1, cost=1e300, cost_unit="tokens"),)
    aggregate = aggregate_strategy_records(records)
    assert math.isfinite(aggregate.costs[0][1].total)
