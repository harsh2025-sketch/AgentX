"""Tests for the durable C2.06 negative-experience store."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentx.core.episodes import EpisodeOutcome
from agentx.core.ids import EpisodeId, NegativeExperienceId, TaskId
from agentx.core.negative_experience import (
    AttemptKind,
    AttemptReference,
    FailureReference,
    NegativeExperienceRecord,
)
from agentx.infrastructure.negative_experience_store import (
    CorruptNegativeExperienceError,
    DuplicateNegativeExperienceError,
    NegativeExperienceStore,
)
from agentx.infrastructure.persistence import _MIGRATIONS, SQLiteDatabase, transaction


def _database(tmp_path: Path) -> SQLiteDatabase:
    return SQLiteDatabase(path=tmp_path / "agentx.sqlite3")


def _record(
    *,
    reference: str = "elevated shell",
    reason_code: str = "timeout",
    episode_id: EpisodeId | None = None,
    task_id: TaskId | None = None,
    observed_at: datetime | None = None,
) -> NegativeExperienceRecord:
    return NegativeExperienceRecord.create(
        attempt=AttemptReference(kind=AttemptKind.APPROACH, reference=reference),
        failure=FailureReference(reason_code=reason_code),
        episode_id=episode_id,
        task_id=task_id,
        observed_at=observed_at or datetime.now(UTC),
    )


def test_migration_ownership_is_v8_after_immutable_v1_through_v7() -> None:
    assert tuple((migration.version, migration.name) for migration in _MIGRATIONS[:8]) == (
        (1, "create_persistence_metadata"),
        (2, "create_event_journal"),
        (3, "create_knowledge_store"),
        (4, "create_episode_store"),
        (5, "create_procedure_store"),
        (6, "create_artifact_and_audit_stores"),
        (7, "create_knowledge_integrity"),
        (8, "create_negative_experience_store"),
    )


def test_append_and_get_round_trip(tmp_path: Path) -> None:
    store = NegativeExperienceStore(database=_database(tmp_path))
    record = _record()

    sequence = store.append(record)

    assert sequence == 1
    assert store.get(record.negative_experience_id) == record


def test_get_absent_identity_returns_none(tmp_path: Path) -> None:
    store = NegativeExperienceStore(database=_database(tmp_path))

    assert store.get(NegativeExperienceId.create()) is None


def test_duplicate_identity_is_explicitly_rejected(tmp_path: Path) -> None:
    store = NegativeExperienceStore(database=_database(tmp_path))
    record = _record()
    store.append(record)

    with pytest.raises(DuplicateNegativeExperienceError):
        store.append(record)

    assert len(store.read()) == 1


def test_equivalent_but_distinct_failures_are_both_remembered(tmp_path: Path) -> None:
    """Two identical failed attempts are two facts, not one deduplicated policy."""
    store = NegativeExperienceStore(database=_database(tmp_path))
    first = _record(reference="same approach", reason_code="timeout")
    second = _record(reference="same approach", reason_code="timeout")

    store.append(first)
    store.append(second)

    stored = store.read()
    assert [entry.record.negative_experience_id for entry in stored] == [
        first.negative_experience_id,
        second.negative_experience_id,
    ]


def test_history_is_deterministic_across_reads(tmp_path: Path) -> None:
    store = NegativeExperienceStore(database=_database(tmp_path))
    records = [_record(reference=f"approach-{index}") for index in range(5)]
    for record in records:
        store.append(record)

    first = store.read()
    second = store.read()

    assert first == second
    assert [entry.sequence for entry in first] == [1, 2, 3, 4, 5]
    assert [entry.record for entry in first] == records


def test_read_supports_exact_association_filters_and_bounds(tmp_path: Path) -> None:
    store = NegativeExperienceStore(database=_database(tmp_path))
    episode_id = EpisodeId.create()
    task_id = TaskId.create()
    linked = _record(episode_id=episode_id, task_id=task_id)
    unlinked = _record()
    store.append(linked)
    store.append(unlinked)

    assert [entry.record for entry in store.read(episode_id=episode_id)] == [linked]
    assert [entry.record for entry in store.read(task_id=task_id)] == [linked]
    assert [entry.record for entry in store.read(after_sequence=1)] == [unlinked]
    assert [entry.record for entry in store.read(limit=1)] == [linked]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"after_sequence": -1}, ValueError),
        ({"limit": 0}, ValueError),
        ({"episode_id": "nope"}, TypeError),
        ({"task_id": "nope"}, TypeError),
    ],
)
def test_read_rejects_invalid_arguments(
    tmp_path: Path, kwargs: dict[str, object], expected: type[Exception]
) -> None:
    store = NegativeExperienceStore(database=_database(tmp_path))

    with pytest.raises(expected):
        store.read(**kwargs)  # type: ignore[arg-type]


def test_append_rejects_non_canonical_input(tmp_path: Path) -> None:
    store = NegativeExperienceStore(database=_database(tmp_path))

    with pytest.raises(TypeError):
        store.append("failed approach")  # type: ignore[arg-type]


def test_restart_durability(tmp_path: Path) -> None:
    database = _database(tmp_path)
    record = _record()
    NegativeExperienceStore(database=database).append(record)

    reopened = NegativeExperienceStore(database=SQLiteDatabase(path=database.path))

    assert reopened.get(record.negative_experience_id) == record
    assert [entry.record for entry in reopened.read()] == [record]


def test_corrupt_payload_fails_closed(tmp_path: Path) -> None:
    database = _database(tmp_path)
    store = NegativeExperienceStore(database=database)
    record = _record()
    store.append(record)

    with database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_negative_experiences SET record_json = ?",
            ('{"schema_version": 1}',),
        )

    with pytest.raises(CorruptNegativeExperienceError):
        store.get(record.negative_experience_id)
    with pytest.raises(CorruptNegativeExperienceError):
        store.read()


def test_tampered_association_column_fails_closed(tmp_path: Path) -> None:
    database = _database(tmp_path)
    store = NegativeExperienceStore(database=database)
    record = _record(task_id=TaskId.create())
    store.append(record)

    with database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_negative_experiences SET task_id = ?",
            (TaskId.create().to_str(),),
        )

    with pytest.raises(CorruptNegativeExperienceError):
        store.read()


def test_empty_persisted_payload_is_rejected_by_schema(tmp_path: Path) -> None:
    database = _database(tmp_path)
    NegativeExperienceStore(database=database).append(_record())

    with database.connection() as connection:  # noqa: SIM117
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE agentx_negative_experiences SET record_json = ''")


def test_stored_outcome_preserves_canonical_failed_vocabulary(tmp_path: Path) -> None:
    store = NegativeExperienceStore(database=_database(tmp_path))
    record = _record()
    store.append(record)

    stored = store.get(record.negative_experience_id)

    assert stored is not None
    assert stored.observed_outcome is EpisodeOutcome.FAILED
