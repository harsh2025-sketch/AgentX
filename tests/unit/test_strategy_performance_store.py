"""Tests for the durable A8.01 StrategyPerformanceStore."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalOutcome
from agentx.core.events import VerificationPayload
from agentx.core.failure_taxonomy import FailureCategory
from agentx.core.ids import (
    CapabilityId,
    EpisodeId,
    ProcedureId,
    StrategyPerformanceId,
    TaskId,
)
from agentx.core.strategy_performance import (
    ExecutionStrategyLevel,
    StrategyAggregationDimension,
    StrategyPerformanceRecord,
    aggregate_strategy_records,
)
from agentx.infrastructure.persistence import _MIGRATIONS, SQLiteDatabase
from agentx.infrastructure.strategy_performance_store import (
    CorruptStrategyPerformanceError,
    DuplicateStrategyPerformanceError,
    StrategyPerformanceEntry,
    StrategyPerformanceStore,
)

_DB_NAME = "agentx.sqlite3"


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / _DB_NAME


def _store(tmp_path: Path) -> StrategyPerformanceStore:
    return StrategyPerformanceStore(SQLiteDatabase(_database_path(tmp_path)))


def _record(
    index: int,
    *,
    level: ExecutionStrategyLevel = ExecutionStrategyLevel.L2_COMPILED,
    outcome: CausalOutcome = CausalOutcome.VERIFIED,
    verification: VerificationPayload | None = None,
    failure_category: FailureCategory | None = None,
    strategy_name: str | None = "resolve",
    strategy_version: str | None = "v1",
    procedure_id: ProcedureId | None = None,
    capability_id: CapabilityId | None = None,
    task_id: TaskId | None = None,
    episode_id: EpisodeId | None = None,
    correlation_id: UUID | None = None,
    scope: str | None = "web-scrape",
    environment: str | None = "windows-11",
    cost: float | None = 1.0,
    cost_unit: str | None = "tokens",
    latency_seconds: float | None = 0.5,
    recorded_at: datetime | None = None,
) -> StrategyPerformanceRecord:
    if verification is None:
        if outcome is CausalOutcome.VERIFIED:
            verification = VerificationPayload(passed=True, detail="verified")
        elif outcome is CausalOutcome.VERIFICATION_FAILED:
            verification = VerificationPayload(passed=False, detail="not matched")
    return StrategyPerformanceRecord(
        record_id=StrategyPerformanceId(UUID(int=50_000 + index)),
        level=level,
        outcome=outcome,
        verification=verification,
        failure_category=failure_category,
        recorded_at=recorded_at or datetime(2026, 9, 5, 8, index % 60, tzinfo=UTC),
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        procedure_id=procedure_id,
        capability_id=capability_id,
        task_id=task_id,
        episode_id=episode_id,
        correlation_id=correlation_id,
        scope=scope,
        environment=environment,
        cost=cost,
        cost_unit=cost_unit,
        latency_seconds=latency_seconds,
    )


def _verified(index: int, **kwargs: object) -> StrategyPerformanceRecord:
    return _record(index, **kwargs)  # type: ignore[arg-type]


def _execution_failure(index: int, **kwargs: object) -> StrategyPerformanceRecord:
    return _record(
        index,
        outcome=CausalOutcome.EXECUTION_FAILED,
        failure_category=FailureCategory.PROCEDURE,
        strategy_version=None,
        cost=None,
        cost_unit=None,
        latency_seconds=None,
        **kwargs,  # type: ignore[arg-type]
    )


def _verification_failure(index: int, **kwargs: object) -> StrategyPerformanceRecord:
    return _record(
        index,
        outcome=CausalOutcome.VERIFICATION_FAILED,
        failure_category=FailureCategory.VERIFICATION,
        strategy_version=None,
        cost=None,
        cost_unit=None,
        latency_seconds=None,
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------
# Schema and append semantics.
# --------------------------------------------------------------------------


def test_fresh_database_creates_schema_via_registered_migration(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.read() == ()

    with store.database.connection() as connection:
        migration_rows = connection.execute(
            "SELECT version, name FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()
        table = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' "
            "AND name = 'agentx_strategy_performance'"
        ).fetchone()
        indexes = connection.execute(
            """
            SELECT name FROM sqlite_schema
            WHERE type = 'index' AND name LIKE 'agentx_strategy_performance_%'
            ORDER BY name
            """
        ).fetchall()

    assert [(row["version"], row["name"]) for row in migration_rows] == [
        (migration.version, migration.name) for migration in _MIGRATIONS
    ]
    assert table is not None
    assert [row["name"] for row in indexes] == [
        "agentx_strategy_performance_capability_sequence_idx",
        "agentx_strategy_performance_correlation_sequence_idx",
        "agentx_strategy_performance_episode_sequence_idx",
        "agentx_strategy_performance_level_sequence_idx",
        "agentx_strategy_performance_outcome_sequence_idx",
        "agentx_strategy_performance_procedure_sequence_idx",
        "agentx_strategy_performance_strategy_name_sequence_idx",
        "agentx_strategy_performance_task_sequence_idx",
    ]


def test_append_returns_increasing_durable_sequences(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = store.append(_verified(1))
    second = store.append(_verified(2))

    assert first == 1
    assert second == 2


def test_append_rejects_non_canonical_records(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(TypeError, match="StrategyPerformanceRecord"):
        store.append({"outcome": "verified"})  # type: ignore[arg-type]


def test_duplicate_append_is_rejected_and_store_is_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _verified(1)

    store.append(record)

    with pytest.raises(DuplicateStrategyPerformanceError):
        store.append(record)
    with pytest.raises(DuplicateStrategyPerformanceError):
        store.append(_verified(1, cost=99.0))

    assert len(store.read()) == 1
    assert store.get(record.record_id) == record


def test_get_returns_record_by_identity_and_none_when_absent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _verified(1)

    store.append(record)

    assert store.get(record.record_id) == record
    assert store.get(StrategyPerformanceId(UUID(int=999_999))) is None


def test_get_rejects_wrong_id_type(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(TypeError, match="StrategyPerformanceId"):
        store.get("not-an-id")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Persistence / restart gate.
# --------------------------------------------------------------------------


def test_history_survives_close_and_reopen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = (
        _verified(1),
        _execution_failure(2),
        _verification_failure(3),
    )
    for record in records:
        store.append(record)

    reopened = _store(tmp_path)
    entries = reopened.read()
    assert tuple(entry.record for entry in entries) == records
    assert reopened.get(records[0].record_id) == records[0]

    # Reopening again on the migrated file stays deterministic.
    reopened_again = StrategyPerformanceStore(SQLiteDatabase(_database_path(tmp_path)))
    assert reopened_again.read() == reopened.read()


def test_duplicate_rejected_after_restart(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _verified(1)
    store.append(record)

    reopened = _store(tmp_path)
    with pytest.raises(DuplicateStrategyPerformanceError):
        reopened.append(record)


# --------------------------------------------------------------------------
# Deterministic reads and filters.
# --------------------------------------------------------------------------


def test_read_returns_history_in_ascending_sequence_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_verified(1))
    store.append(_verified(2))
    store.append(_verified(3))

    entries = store.read()
    assert [entry.sequence for entry in entries] == [1, 2, 3]
    assert [entry.record for entry in entries] == [
        _verified(1),
        _verified(2),
        _verified(3),
    ]


def test_read_after_sequence_is_exclusive_and_limit_is_exact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(1, 6):
        store.append(_verified(index))

    tail = store.read(after_sequence=2)
    assert [entry.sequence for entry in tail] == [3, 4, 5]

    page = store.read(after_sequence=1, limit=2)
    assert [entry.sequence for entry in page] == [2, 3]

    empty = store.read(after_sequence=5)
    assert empty == ()


def test_read_filters_by_level_outcome_and_strategy_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cache_record = _record(1, level=ExecutionStrategyLevel.L0_CACHE, strategy_name="cache-hit")
    direct_ok = _record(2, level=ExecutionStrategyLevel.L1_DIRECT, strategy_name="direct")
    direct_failed = _execution_failure(
        3, level=ExecutionStrategyLevel.L1_DIRECT, strategy_name="direct"
    )
    exploratory = _verified(4, level=ExecutionStrategyLevel.L5_EXPLORATORY)

    store.append(cache_record)
    store.append(direct_ok)
    store.append(direct_failed)
    store.append(exploratory)

    assert [entry.record for entry in store.read(level=ExecutionStrategyLevel.L1_DIRECT)] == [
        direct_ok,
        direct_failed,
    ]
    assert [entry.record for entry in store.read(outcome=CausalOutcome.EXECUTION_FAILED)] == [
        direct_failed
    ]
    assert [entry.record for entry in store.read(strategy_name="cache-hit")] == [cache_record]
    assert store.read(strategy_name="absent") == ()
    assert [entry.record for entry in store.read(level=ExecutionStrategyLevel.L0_CACHE)] == [
        cache_record
    ]


def test_read_filters_by_identity_references(tmp_path: Path) -> None:
    procedure_id = ProcedureId(UUID(int=5_100))
    capability_id = CapabilityId(UUID(int=6_100))
    task_id = TaskId(UUID(int=7_100))
    episode_id = EpisodeId(UUID(int=8_100))
    correlation_id = UUID(int=9_100)

    store = _store(tmp_path)
    tagged = _verified(
        1,
        procedure_id=procedure_id,
        capability_id=capability_id,
        task_id=task_id,
        episode_id=episode_id,
        correlation_id=correlation_id,
    )
    store.append(tagged)
    store.append(_verified(2))

    assert [entry.record for entry in store.read(procedure_id=procedure_id)] == [tagged]
    assert [entry.record for entry in store.read(capability_id=capability_id)] == [tagged]
    assert [entry.record for entry in store.read(task_id=task_id)] == [tagged]
    assert [entry.record for entry in store.read(episode_id=episode_id)] == [tagged]
    assert [entry.record for entry in store.read(correlation_id=correlation_id)] == [tagged]
    assert store.read(task_id=TaskId(UUID(int=777))) == ()


def test_read_validates_filter_boundaries(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="after_sequence"):
        store.read(after_sequence=-1)
    with pytest.raises(ValueError, match="limit"):
        store.read(limit=0)
    with pytest.raises(ValueError, match="trimmed"):
        store.read(strategy_name="  ")
    with pytest.raises(TypeError, match="level"):
        store.read(level="L1_DIRECT")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="outcome"):
        store.read(outcome="verified")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="task_id"):
        store.read(task_id=UUID(int=1))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Aggregation gates through the store.
# --------------------------------------------------------------------------


def _populated_store(tmp_path: Path) -> StrategyPerformanceStore:
    store = _store(tmp_path)
    store.append(_verified(1, level=ExecutionStrategyLevel.L0_CACHE, cost=1.0, latency_seconds=0.1))
    store.append(
        _verified(
            2,
            level=ExecutionStrategyLevel.L1_DIRECT,
            strategy_version="v1",
            cost=4.0,
            latency_seconds=0.4,
        )
    )
    store.append(
        _verified(
            3,
            level=ExecutionStrategyLevel.L1_DIRECT,
            strategy_version="v2",
            cost=2.0,
            latency_seconds=0.2,
        )
    )
    store.append(_verification_failure(4, level=ExecutionStrategyLevel.L2_COMPILED))
    store.append(
        _execution_failure(
            5, level=ExecutionStrategyLevel.L5_EXPLORATORY, environment="windows-server"
        )
    )
    return store


def test_store_aggregate_matches_pure_core_aggregation(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)

    from_pure = aggregate_strategy_records(entry.record for entry in store.read())
    from_store = store.aggregate()

    assert from_store == from_pure

    filtered_pure = aggregate_strategy_records(
        entry.record for entry in store.read(level=ExecutionStrategyLevel.L1_DIRECT)
    )
    filtered_store = store.aggregate(level=ExecutionStrategyLevel.L1_DIRECT)
    assert filtered_store == filtered_pure
    assert filtered_store.attempts == 2


def test_store_aggregate_counts_attempts_outcomes_and_costs(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)

    aggregate = store.aggregate()
    counts = dict(aggregate.outcomes)
    assert aggregate.attempts == 5
    assert counts[CausalOutcome.VERIFIED] == 3
    assert counts[CausalOutcome.VERIFICATION_FAILED] == 1
    assert counts[CausalOutcome.EXECUTION_FAILED] == 1

    category_counts = dict(aggregate.failure_categories)
    assert category_counts[FailureCategory.VERIFICATION] == 1
    assert category_counts[FailureCategory.PROCEDURE] == 1
    assert category_counts[FailureCategory.UNKNOWN] == 0

    tokens = dict(aggregate.costs)["tokens"]
    assert tokens.measured_count == 3
    assert tokens.total == 7.0
    assert tokens.minimum == 1.0
    assert tokens.maximum == 4.0
    assert tokens.mean == pytest.approx(7.0 / 3.0)

    latency = aggregate.latency_seconds
    assert latency.measured_count == 3
    assert latency.total == pytest.approx(0.7)
    assert latency.minimum == 0.1
    assert latency.maximum == 0.4
    assert latency.mean == pytest.approx(0.7 / 3.0)


def test_store_aggregate_respects_outcome_filter(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)

    failed = store.aggregate(outcome=CausalOutcome.EXECUTION_FAILED)
    assert failed.attempts == 1
    assert dict(failed.failure_categories)[FailureCategory.PROCEDURE] == 1

    verified = store.aggregate(outcome=CausalOutcome.VERIFIED)
    assert verified.attempts == 3
    assert dict(verified.failure_categories)[FailureCategory.PROCEDURE] == 0


def test_store_grouped_aggregation_over_levels(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)

    groups = store.aggregate_grouped(dimension=StrategyAggregationDimension.LEVEL)
    assert [group.key for group in groups] == [
        ExecutionStrategyLevel.L0_CACHE.value,
        ExecutionStrategyLevel.L1_DIRECT.value,
        ExecutionStrategyLevel.L2_COMPILED.value,
        ExecutionStrategyLevel.L5_EXPLORATORY.value,
    ]
    by_key = {group.key: group.aggregate for group in groups}
    assert by_key[ExecutionStrategyLevel.L0_CACHE.value].attempts == 1
    assert by_key[ExecutionStrategyLevel.L1_DIRECT.value].attempts == 2
    assert by_key[ExecutionStrategyLevel.L2_COMPILED.value].attempts == 1
    assert by_key[ExecutionStrategyLevel.L5_EXPLORATORY.value].attempts == 1
    l1 = by_key[ExecutionStrategyLevel.L1_DIRECT.value]
    assert l1.costs[0][1].measured_count == 2


def test_store_grouped_aggregation_by_version_and_environment(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)

    versions = store.aggregate_grouped(dimension=StrategyAggregationDimension.STRATEGY_VERSION)
    by_version = {group.key: group.aggregate for group in versions}
    assert by_version[None].attempts == 2  # the two failure records carry no version
    assert by_version["v1"].attempts == 2  # verified L0 + L1 records
    assert by_version["v1"].costs[0][1].total == 5.0
    assert by_version["v2"].attempts == 1
    assert by_version["v2"].costs[0][1].total == 2.0

    environments = store.aggregate_grouped(
        dimension=StrategyAggregationDimension.ENVIRONMENT,
        outcome=CausalOutcome.EXECUTION_FAILED,
    )
    assert [(group.key, group.aggregate.attempts) for group in environments] == [
        ("windows-server", 1)
    ]

    all_environments = store.aggregate_grouped(dimension=StrategyAggregationDimension.ENVIRONMENT)
    assert [(group.key, group.aggregate.attempts) for group in all_environments] == [
        ("windows-11", 4),
        ("windows-server", 1),
    ]


def test_store_grouped_requires_canonical_dimension(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    with pytest.raises(TypeError, match="dimension"):
        store.aggregate_grouped(dimension="scope")  # type: ignore[arg-type]


def test_read_returns_canonical_entry_type(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    entry = store.read()[0]
    assert isinstance(entry, StrategyPerformanceEntry)
    assert isinstance(entry.sequence, int)
    assert isinstance(entry.record, StrategyPerformanceRecord)


# --------------------------------------------------------------------------
# Corruption detection (measured facts stay canonical or nothing is returned).
# --------------------------------------------------------------------------


def test_corrupt_json_row_is_detected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_verified(1))

    with store.database.connection() as connection:
        connection.execute(
            "UPDATE agentx_strategy_performance SET record_json = '{bad json' WHERE record_id = ?",
            (_verified(1).record_id.to_str(),),
        )
        connection.commit()

    with pytest.raises(CorruptStrategyPerformanceError):
        store.read()


def test_column_json_mismatch_is_detected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_verified(1))

    with store.database.connection() as connection:
        connection.execute(
            "UPDATE agentx_strategy_performance SET outcome = 'execution_failed' "
            "WHERE record_id = ?",
            (_verified(1).record_id.to_str(),),
        )
        connection.commit()

    with pytest.raises(CorruptStrategyPerformanceError):
        store.read()


def test_truncated_json_is_detected_on_get(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_verified(1))

    with store.database.connection() as connection:
        connection.execute(
            "UPDATE agentx_strategy_performance SET record_json = '{}' WHERE record_id = ?",
            (_verified(1).record_id.to_str(),),
        )
        connection.commit()

    with pytest.raises(CorruptStrategyPerformanceError):
        store.get(_verified(1).record_id)
