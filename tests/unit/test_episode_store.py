"""Tests for the durable AgentX EpisodeStore."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock, Thread
from typing import cast
from uuid import UUID

import pytest

from agentx.core.episodes import (
    EpisodeDeserializationError,
    EpisodeOutcome,
    EpisodeRecord,
    UnsupportedEpisodeSchemaVersionError,
)
from agentx.core.events import Event, EventType
from agentx.core.ids import EpisodeId, TaskId
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.episode_store import (
    CorruptEpisodeError,
    DuplicateEpisodeError,
    EpisodeStore,
)
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.persistence import (
    _MIGRATIONS,
    SQLiteDatabase,
    _apply_migrations,
    transaction,
)
from agentx.kernel.action_gate import ActionGate, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.risk import assess_risk


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "agentx.sqlite3"


def _store(tmp_path: Path) -> EpisodeStore:
    return EpisodeStore(SQLiteDatabase(_database_path(tmp_path)))


def _episode(
    index: int,
    *,
    task_id: TaskId | None = None,
    correlation_id: UUID | None = None,
    summary: str | None = None,
    supporting_event_ids: tuple[UUID, ...] = (),
    created_at: datetime | None = None,
) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=EpisodeId(UUID(int=10_000 + index)),
        outcome=EpisodeOutcome.SUCCEEDED,
        summary=summary or f"Meaningful historical episode {index}.",
        created_at=created_at or datetime(2026, 9, 5, 8, index, tzinfo=UTC),
        task_id=task_id,
        correlation_id=correlation_id,
        supporting_event_ids=supporting_event_ids,
    )


def test_fresh_database_creates_episode_schema_via_registered_migration(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.read() == ()

    with store.database.connection() as connection:
        migration_rows = connection.execute(
            "SELECT version, name FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()
        table = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name = 'agentx_episodes'"
        ).fetchone()
        indexes = connection.execute(
            """
            SELECT name FROM sqlite_schema
            WHERE type = 'index' AND name LIKE 'agentx_episodes_%'
            ORDER BY name
            """
        ).fetchall()

    assert [(row["version"], row["name"]) for row in migration_rows] == [
        (migration.version, migration.name) for migration in _MIGRATIONS
    ]
    assert table is not None
    assert [row["name"] for row in indexes] == [
        "agentx_episodes_correlation_sequence_idx",
        "agentx_episodes_task_sequence_idx",
    ]


def test_existing_v2_database_migrates_without_rewriting_historical_schema(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        _apply_migrations(connection, _MIGRATIONS[:2])
        before = connection.execute(
            """
            SELECT name, sql FROM sqlite_schema
            WHERE type = 'table' AND name IN ('agentx_schema_migrations', 'agentx_event_journal')
            ORDER BY name
            """
        ).fetchall()
        before_schema = [(row["name"], row["sql"]) for row in before]
    finally:
        connection.close()

    store = EpisodeStore(SQLiteDatabase(path))
    assert store.read() == ()

    with store.database.connection() as migrated:
        after = migrated.execute(
            """
            SELECT name, sql FROM sqlite_schema
            WHERE type = 'table' AND name IN ('agentx_schema_migrations', 'agentx_event_journal')
            ORDER BY name
            """
        ).fetchall()
        versions = migrated.execute(
            "SELECT version, name FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()

    assert [(row["name"], row["sql"]) for row in after] == before_schema
    assert [(row["version"], row["name"]) for row in versions[:2]] == [
        (1, "create_persistence_metadata"),
        (2, "create_event_journal"),
    ]
    assert any(row["name"] == "create_episode_store" for row in versions)


def test_append_and_get_by_episode_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    episode = _episode(1)

    sequence = store.append(episode)

    assert sequence == 1
    assert store.get(episode.episode_id) == episode
    assert store.get(EpisodeId(UUID(int=99_999))) is None


def test_read_order_is_durable_sequence_not_episode_created_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _episode(1, created_at=datetime(2030, 1, 1, tzinfo=UTC))
    second = _episode(2, created_at=datetime(2020, 1, 1, tzinfo=UTC))

    first_sequence = store.append(first)
    second_sequence = store.append(second)
    entries = store.read()

    assert (first_sequence, second_sequence) == (1, 2)
    assert tuple(entry.sequence for entry in entries) == (1, 2)
    assert tuple(entry.episode for entry in entries) == (first, second)


def test_after_sequence_and_limit_are_deterministic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    episodes = tuple(_episode(index) for index in range(1, 5))
    sequences = tuple(store.append(episode) for episode in episodes)

    entries = store.read(after_sequence=sequences[0], limit=2)

    assert tuple(entry.sequence for entry in entries) == sequences[1:3]
    assert tuple(entry.episode for entry in entries) == episodes[1:3]


@pytest.mark.parametrize(
    ("after_sequence", "limit"),
    [(-1, None), (True, None), (0, 0), (0, -1), (0, True)],
)
def test_invalid_read_boundaries_fail_closed(
    tmp_path: Path,
    after_sequence: int,
    limit: int | None,
) -> None:
    with pytest.raises(ValueError):
        _store(tmp_path).read(after_sequence=after_sequence, limit=limit)


def test_task_and_correlation_filters_use_stable_associations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task_a = TaskId(UUID(int=20_001))
    task_b = TaskId(UUID(int=20_002))
    correlation_a = UUID(int=30_001)
    correlation_b = UUID(int=30_002)
    episodes = (
        _episode(1, task_id=task_a, correlation_id=correlation_a),
        _episode(2, task_id=task_a, correlation_id=correlation_b),
        _episode(3, task_id=task_b, correlation_id=correlation_a),
    )
    for episode in episodes:
        store.append(episode)

    assert tuple(entry.episode for entry in store.read(task_id=task_a)) == episodes[:2]
    assert tuple(entry.episode for entry in store.read(correlation_id=correlation_a)) == (
        episodes[0],
        episodes[2],
    )
    assert tuple(
        entry.episode for entry in store.read(task_id=task_a, correlation_id=correlation_a)
    ) == (episodes[0],)


def test_duplicate_episode_identity_fails_without_second_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    episode = _episode(1)
    assert store.append(episode) == 1

    with pytest.raises(DuplicateEpisodeError, match=episode.episode_id.to_str()):
        store.append(episode)

    assert tuple(entry.episode for entry in store.read()) == (episode,)


def test_restart_preserves_episode_and_next_sequence(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    first_store = EpisodeStore(SQLiteDatabase(path))
    first = _episode(1)
    assert first_store.append(first) == 1

    reopened = EpisodeStore(SQLiteDatabase(path))
    second = _episode(2)

    assert reopened.get(first.episode_id) == first
    assert reopened.append(second) == 2
    assert tuple(entry.episode for entry in reopened.read()) == (first, second)


def test_malformed_persisted_episode_is_reported_with_safe_row_context(tmp_path: Path) -> None:
    store = _store(tmp_path)
    episode = _episode(1)
    sequence = store.append(episode)

    with store.database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_episodes SET episode_json = ? WHERE sequence = ?",
            ("{not-json", sequence),
        )

    with pytest.raises(CorruptEpisodeError) as exc_info:
        store.read()

    assert exc_info.value.sequence == sequence
    assert exc_info.value.episode_id == episode.episode_id.to_str()
    assert "not-json" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, EpisodeDeserializationError)


def test_unsupported_episode_schema_is_corrupt_with_canonical_cause(tmp_path: Path) -> None:
    store = _store(tmp_path)
    episode = _episode(1)
    sequence = store.append(episode)
    encoded = episode.to_dict()
    encoded["schema_version"] = 999

    with store.database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_episodes SET episode_json = ? WHERE sequence = ?",
            (json.dumps(encoded), sequence),
        )

    with pytest.raises(CorruptEpisodeError) as exc_info:
        store.get(episode.episode_id)

    assert isinstance(exc_info.value.__cause__, UnsupportedEpisodeSchemaVersionError)


def test_indexed_episode_identity_mismatch_is_corruption(tmp_path: Path) -> None:
    store = _store(tmp_path)
    episode = _episode(1)
    sequence = store.append(episode)
    different_id = EpisodeId(UUID(int=88_888)).to_str()

    with store.database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_episodes SET episode_id = ? WHERE sequence = ?",
            (different_id, sequence),
        )

    with pytest.raises(CorruptEpisodeError) as exc_info:
        store.read()

    assert exc_info.value.sequence == sequence
    assert exc_info.value.episode_id == different_id


def test_indexed_task_or_correlation_mismatch_is_corruption(tmp_path: Path) -> None:
    task_id = TaskId(UUID(int=20_001))
    correlation_id = UUID(int=30_001)
    store = _store(tmp_path)
    episode = _episode(1, task_id=task_id, correlation_id=correlation_id)
    sequence = store.append(episode)

    with store.database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_episodes SET task_id = ? WHERE sequence = ?",
            (TaskId(UUID(int=20_999)).to_str(), sequence),
        )

    with pytest.raises(CorruptEpisodeError):
        store.read()


def test_concurrent_independent_writers_preserve_unique_sequences(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    EpisodeStore(SQLiteDatabase(path)).read()
    episodes = tuple(_episode(index) for index in range(1, 7))
    barrier = Barrier(len(episodes))
    result_lock = Lock()
    sequences: list[int] = []
    failures: list[BaseException] = []

    def writer(episode: EpisodeRecord) -> None:
        try:
            barrier.wait()
            sequence = EpisodeStore(SQLiteDatabase(path)).append(episode)
        except BaseException as exc:  # captured for assertion in the parent thread
            with result_lock:
                failures.append(exc)
        else:
            with result_lock:
                sequences.append(sequence)

    threads = [Thread(target=writer, args=(episode,)) for episode in episodes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert sorted(sequences) == list(range(1, len(episodes) + 1))
    entries = EpisodeStore(SQLiteDatabase(path)).read()
    assert tuple(entry.sequence for entry in entries) == tuple(range(1, len(episodes) + 1))
    assert {entry.episode.episode_id for entry in entries} == {
        episode.episode_id for episode in episodes
    }


def test_event_journal_and_episode_store_do_not_call_each_other(tmp_path: Path) -> None:
    database = SQLiteDatabase(_database_path(tmp_path))
    journal = EventJournal(database)
    store = EpisodeStore(database)
    event = Event.create(event_type=EventType.TASK_COMPLETED, source="tests.episode_store")

    journal.append(event)
    assert store.read() == ()

    episode = _episode(1, supporting_event_ids=(event.event_id,))
    store.append(episode)

    assert tuple(entry.episode for entry in store.read()) == (episode,)
    assert tuple(entry.event for entry in journal.read()) == (event,)


def test_reading_successful_history_executes_nothing_and_mutates_no_task(tmp_path: Path) -> None:
    task_id = TaskId(UUID(int=20_001))
    task = Task.create("Retain current task state.", task_id=task_id, status=TaskStatus.RUNNING)
    store = _store(tmp_path)
    episode = _episode(1, task_id=task_id, summary="Previously completed action succeeded.")
    executed: list[str] = []
    store.append(episode)

    restored = store.get(episode.episode_id)

    assert restored == episode
    assert executed == []
    assert task.status is TaskStatus.RUNNING


def test_historical_r4_episode_is_not_action_gate_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    episode = _episode(1, summary="A previously authorized R4 destructive action succeeded.")
    store.append(episode)
    restored = store.get(episode.episode_id)
    assert restored is not None
    request = GateRequest(
        operation="destructive.example",
        required_permission=Permission.DESTRUCTIVE,
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=True,
            destructive=True,
        ),
    )

    historical_data = cast(AuthorityContext | None, restored)
    with pytest.raises(TypeError, match="AuthorityContext"):
        ActionGate().evaluate(request, historical_data)


def test_reading_episode_cannot_clear_emergency_stop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    episode = _episode(1, summary="Historical safety-related experience.")
    store.append(episode)
    emergency_stop = EmergencyStop()
    emergency_stop.request_stop()

    assert store.get(episode.episode_id) == episode
    assert emergency_stop.stop_requested is True


def test_episode_store_import_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    working_directory = tmp_path / "import sandbox"
    working_directory.mkdir()

    result = subprocess.run(
        [sys.executable, "-I", "-c", "import agentx.infrastructure.episode_store"],
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert list(working_directory.iterdir()) == []
