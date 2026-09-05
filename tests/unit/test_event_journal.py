"""Tests for the durable AgentX EventJournal."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock, Thread
from uuid import UUID

import pytest

from agentx.core.events import (
    ActionPayload,
    Event,
    EventType,
    UnsupportedEventSchemaVersionError,
)
from agentx.infrastructure.event_bus import EventBus
from agentx.infrastructure.event_journal import (
    CorruptJournalEntryError,
    DuplicateEventError,
    EventJournal,
    EventJournalStorageError,
)
from agentx.infrastructure.persistence import (
    _MIGRATIONS,
    SQLiteDatabase,
    UnsupportedSchemaVersionError,
    _apply_migrations,
    transaction,
)


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "agentx.sqlite3"


def _journal(tmp_path: Path) -> EventJournal:
    return EventJournal(SQLiteDatabase(_database_path(tmp_path)))


def _event(label: str, *, timestamp: datetime | None = None) -> Event:
    return Event.create(
        event_type=EventType.TASK_CREATED,
        source="tests.event_journal",
        task_id=f"task-{label}",
        metadata={"label": label},
        timestamp=timestamp,
    )


def test_fresh_journal_creation_via_migration(tmp_path: Path) -> None:
    database = SQLiteDatabase(_database_path(tmp_path))
    journal = EventJournal(database)

    assert journal.read() == ()

    with database.connection() as connection:
        migrations = connection.execute(
            "SELECT version, name FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()
        table = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name = 'agentx_event_journal'"
        ).fetchone()

    assert [(row["version"], row["name"]) for row in migrations][:2] == [
        (1, "create_persistence_metadata"),
        (2, "create_event_journal"),
    ]
    # Only the journal's own migrations are pinned by number; later migrations
    # appended by concurrent store tasks must not break this test.
    assert [row["version"] for row in migrations] == list(range(1, len(_MIGRATIONS) + 1))
    assert table is not None


def test_existing_c105_v1_database_migrates_forward(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        _apply_migrations(connection, _MIGRATIONS[:1])
    finally:
        connection.close()

    journal = EventJournal(SQLiteDatabase(path))
    assert journal.read() == ()

    with SQLiteDatabase(path).connection() as migrated:
        versions = migrated.execute(
            "SELECT version FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()

    # The v1 database must migrate fully forward to the latest schema.
    assert [row["version"] for row in versions] == list(range(1, len(_MIGRATIONS) + 1))


def test_append_one_event(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    event = _event("one")

    sequence = journal.append(event)

    assert sequence == 1
    entries = journal.read()
    assert len(entries) == 1
    assert entries[0].sequence == 1
    assert entries[0].event == event


def test_append_multiple_events_uses_monotonic_sequence(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    events = tuple(_event(str(index)) for index in range(4))

    sequences = tuple(journal.append(event) for event in events)

    assert sequences == (1, 2, 3, 4)
    assert tuple(entry.sequence for entry in journal.read()) == sequences


def test_read_order_is_sequence_not_event_timestamp(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    first = _event("first", timestamp=datetime(2030, 1, 1, tzinfo=UTC))
    second = _event("second", timestamp=datetime(2020, 1, 1, tzinfo=UTC))

    journal.append(first)
    journal.append(second)

    assert journal.replay() == (first, second)


def test_canonical_event_round_trip_preserves_payload_metadata_and_chain(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    root = _event("root")
    action = Event.create(
        event_type=EventType.ACTION_REQUESTED,
        source="tests.event_journal",
        correlation_id=root.correlation_id,
        causation_id=root.event_id,
        task_id="task-root",
        payload=ActionPayload(
            name="filesystem.inspect",
            data={"path": "C:/AgentX/data", "nested": {"safe": True}},
        ),
        metadata={"attempt": 2, "tags": ["journal", "round-trip"]},
    )

    journal.append(root)
    journal.append(action)
    restored = journal.replay()[1]

    assert restored == action
    assert restored.correlation_id == root.correlation_id
    assert restored.causation_id == root.event_id
    assert restored.metadata == action.metadata
    assert restored.payload == action.payload


def test_restart_preserves_history_and_next_sequence(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    first_journal = EventJournal(SQLiteDatabase(path))
    first = _event("first")
    assert first_journal.append(first) == 1

    reopened = EventJournal(SQLiteDatabase(path))
    second = _event("second")

    assert reopened.append(second) == 2
    assert reopened.replay() == (first, second)


def test_duplicate_event_id_fails_without_second_record(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    event = _event("duplicate")
    first_sequence = journal.append(event)

    with pytest.raises(DuplicateEventError, match=str(event.event_id)):
        journal.append(event)

    entries = journal.read()
    assert first_sequence == 1
    assert len(entries) == 1
    assert entries[0].event == event


def test_failed_append_does_not_partially_persist(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    assert journal.read() == ()

    with journal.database.connection() as connection, transaction(connection):
        connection.execute(
            """
            CREATE TRIGGER reject_event_journal_insert
            BEFORE INSERT ON agentx_event_journal
            BEGIN
                SELECT RAISE(ABORT, 'forced journal append failure');
            END
            """
        )

    with pytest.raises(EventJournalStorageError, match="Unable to append"):
        journal.append(_event("rejected"))

    assert journal.read() == ()


def test_empty_journal_read_and_replay(tmp_path: Path) -> None:
    journal = _journal(tmp_path)

    assert journal.read() == ()
    assert journal.replay() == ()


def test_after_sequence_is_exclusive(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    events = tuple(_event(str(index)) for index in range(3))
    sequences = tuple(journal.append(event) for event in events)

    entries = journal.read(after_sequence=sequences[0])
    replayed = journal.replay(after_sequence=sequences[1])

    assert tuple(entry.sequence for entry in entries) == sequences[1:]
    assert tuple(entry.event for entry in entries) == events[1:]
    assert replayed == events[2:]


def test_limit_bounds_results(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    events = tuple(_event(str(index)) for index in range(4))
    for event in events:
        journal.append(event)

    assert tuple(entry.event for entry in journal.read(limit=2)) == events[:2]
    assert journal.replay(after_sequence=1, limit=2) == events[1:3]


@pytest.mark.parametrize(
    ("after_sequence", "limit"),
    [(-1, None), (True, None), (0, 0), (0, -1), (0, True)],
)
def test_invalid_read_boundaries_are_rejected(
    tmp_path: Path,
    after_sequence: int,
    limit: int | None,
) -> None:
    with pytest.raises(ValueError):
        _journal(tmp_path).read(after_sequence=after_sequence, limit=limit)


def test_malformed_stored_event_fails_with_row_context(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    event = _event("corrupt")
    sequence = journal.append(event)

    with journal.database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_event_journal SET event_json = ? WHERE sequence = ?",
            ("{not-json", sequence),
        )

    with pytest.raises(CorruptJournalEntryError) as exc_info:
        journal.replay()

    assert exc_info.value.sequence == sequence
    assert exc_info.value.event_id == str(event.event_id)
    assert "not-json" not in str(exc_info.value)


def test_unsupported_event_schema_is_reported_as_corrupt_with_canonical_cause(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    event = _event("future-event-schema")
    sequence = journal.append(event)
    encoded = event.to_dict()
    encoded["schema_version"] = 999

    with journal.database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_event_journal SET event_json = ? WHERE sequence = ?",
            (json.dumps(encoded), sequence),
        )

    with pytest.raises(CorruptJournalEntryError) as exc_info:
        journal.replay()

    assert isinstance(exc_info.value.__cause__, UnsupportedEventSchemaVersionError)


def test_stored_event_id_mismatch_is_corruption(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    event = _event("mismatch")
    sequence = journal.append(event)
    different_id = str(UUID("11111111-1111-4111-8111-111111111111"))

    with journal.database.connection() as connection, transaction(connection):
        connection.execute(
            "UPDATE agentx_event_journal SET event_id = ? WHERE sequence = ?",
            (different_id, sequence),
        )

    with pytest.raises(CorruptJournalEntryError) as exc_info:
        journal.read()

    assert exc_info.value.sequence == sequence
    assert exc_info.value.event_id == different_id


def test_newer_database_schema_rejection_propagates(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    assert journal.read() == ()
    future_version = len(_MIGRATIONS) + 1

    with journal.database.connection() as connection, transaction(connection):
        connection.execute(
            "INSERT INTO agentx_schema_migrations (version, name) VALUES (?, ?)",
            (future_version, "future_schema"),
        )

    with pytest.raises(UnsupportedSchemaVersionError, match="newer than supported"):
        journal.read()


def test_concurrent_independent_writers_preserve_unique_sequence(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    EventJournal(SQLiteDatabase(path)).read()
    events = tuple(_event(f"writer-{index}") for index in range(6))
    barrier = Barrier(len(events))
    result_lock = Lock()
    sequences: list[int] = []
    failures: list[BaseException] = []

    def writer(event: Event) -> None:
        try:
            barrier.wait()
            sequence = EventJournal(SQLiteDatabase(path)).append(event)
        except BaseException as exc:  # captured for assertion in the parent thread
            with result_lock:
                failures.append(exc)
        else:
            with result_lock:
                sequences.append(sequence)

    threads = [Thread(target=writer, args=(event,)) for event in events]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert sorted(sequences) == list(range(1, len(events) + 1))

    entries = EventJournal(SQLiteDatabase(path)).read()
    assert tuple(entry.sequence for entry in entries) == tuple(range(1, len(events) + 1))
    assert {entry.event.event_id for entry in entries} == {event.event_id for event in events}


def test_replay_does_not_publish_to_event_bus_or_subscribers(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    event = _event("history-only")
    journal.append(event)
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(received.append)

    replayed = journal.replay()

    assert replayed == (event,)
    assert received == []


def test_replayed_action_event_is_data_not_execution_authority(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    action = Event.create(
        event_type=EventType.ACTION_REQUESTED,
        source="tests.event_journal",
        payload=ActionPayload(
            name="dangerous.example",
            data={"command": "do-not-execute", "permission": False},
        ),
    )
    executed: list[str] = []

    journal.append(action)
    replayed = journal.replay()

    assert replayed == (action,)
    assert executed == []


def test_event_journal_import_does_not_create_database_or_files(tmp_path: Path) -> None:
    working_directory = tmp_path / "import sandbox"
    working_directory.mkdir()

    result = subprocess.run(
        [sys.executable, "-I", "-c", "import agentx.infrastructure.event_journal"],
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
