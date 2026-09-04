"""Durable append-only history for canonical AgentX events.

The journal records immutable event data in SQLite sequence order. Reading or
replaying history only reconstructs :class:`agentx.core.events.Event`
objects; it never publishes them, invokes capabilities, or grants authority.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from agentx.core.events import Event, EventValidationError
from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase, transaction

_EVENT_JOURNAL_TABLE: Final = "agentx_event_journal"


class EventJournalError(PersistenceError):
    """Base error for durable event-journal operations."""


class DuplicateEventError(EventJournalError):
    """Raised when an event_id already exists in the append-only journal."""


class EventJournalStorageError(EventJournalError):
    """Raised when SQLite cannot complete a journal read or append statement."""


class CorruptJournalEntryError(EventJournalError):
    """Raised when persisted journal data cannot reconstruct its canonical Event."""

    def __init__(self, *, sequence: int, event_id: str) -> None:
        self.sequence = sequence
        self.event_id = event_id
        super().__init__(f"Journal entry sequence {sequence} for event_id {event_id!r} is corrupt")


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One persisted Event paired with its durable journal sequence."""

    sequence: int
    event: Event


@dataclass(frozen=True, slots=True)
class EventJournal:
    """Append and deterministically replay canonical Events from SQLite."""

    database: SQLiteDatabase

    def append(self, event: Event) -> int:
        """Atomically append one Event and return its committed journal sequence.

        Duplicate ``event_id`` values fail explicitly and never create a second
        record. The returned sequence is assigned durably by SQLite, not by an
        in-memory counter.
        """
        if not isinstance(event, Event):
            raise TypeError("event must be a canonical Event")

        event_json = event.to_json()
        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    cursor = connection.execute(
                        f"INSERT INTO {_EVENT_JOURNAL_TABLE} (event_id, event_json) VALUES (?, ?)",
                        (str(event.event_id), event_json),
                    )
                    sequence = cursor.lastrowid
                    if sequence is None:
                        raise EventJournalStorageError(
                            "SQLite did not return a journal sequence for appended event"
                        )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                    raise DuplicateEventError(
                        f"Event {event.event_id} already exists in the journal"
                    ) from exc
                raise EventJournalStorageError(
                    f"Unable to append event {event.event_id} to the journal"
                ) from exc
            except sqlite3.Error as exc:
                raise EventJournalStorageError(
                    f"Unable to append event {event.event_id} to the journal"
                ) from exc

        return sequence

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[JournalEntry, ...]:
        """Read entries after an exclusive sequence boundary in ascending order."""
        _validate_read_boundary(after_sequence=after_sequence, limit=limit)

        sql = (
            f"SELECT sequence, event_id, event_json FROM {_EVENT_JOURNAL_TABLE} "
            "WHERE sequence > ? ORDER BY sequence ASC"
        )
        parameters: tuple[int, ...]
        if limit is None:
            parameters = (after_sequence,)
        else:
            sql = f"{sql} LIMIT ?"
            parameters = (after_sequence, limit)

        with self.database.connection() as connection:
            try:
                rows = connection.execute(sql, parameters).fetchall()
            except sqlite3.Error as exc:
                raise EventJournalStorageError("Unable to read the event journal") from exc

        return tuple(_decode_row(row) for row in rows)

    def replay(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[Event, ...]:
        """Reconstruct historical Events without publishing or executing them."""
        return tuple(entry.event for entry in self.read(after_sequence=after_sequence, limit=limit))


def _validate_read_boundary(*, after_sequence: int, limit: int | None) -> None:
    if (
        not isinstance(after_sequence, int)
        or isinstance(after_sequence, bool)
        or after_sequence < 0
    ):
        raise ValueError("after_sequence must be a non-negative integer")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise ValueError("limit must be a positive integer or None")


def _decode_row(row: sqlite3.Row) -> JournalEntry:
    sequence_raw = row["sequence"]
    event_id_raw = row["event_id"]
    event_json_raw = row["event_json"]

    if not isinstance(sequence_raw, int) or isinstance(sequence_raw, bool) or sequence_raw <= 0:
        raise CorruptJournalEntryError(sequence=0, event_id="<invalid>")
    if not isinstance(event_id_raw, str) or not event_id_raw:
        raise CorruptJournalEntryError(sequence=sequence_raw, event_id="<invalid>")
    if not isinstance(event_json_raw, str) or not event_json_raw:
        raise CorruptJournalEntryError(sequence=sequence_raw, event_id=event_id_raw)

    try:
        event = Event.from_json(event_json_raw)
    except EventValidationError as exc:
        raise CorruptJournalEntryError(
            sequence=sequence_raw,
            event_id=event_id_raw,
        ) from exc

    if str(event.event_id) != event_id_raw:
        raise CorruptJournalEntryError(sequence=sequence_raw, event_id=event_id_raw)

    return JournalEntry(sequence=sequence_raw, event=event)


__all__ = [
    "CorruptJournalEntryError",
    "DuplicateEventError",
    "EventJournal",
    "EventJournalError",
    "EventJournalStorageError",
    "JournalEntry",
]
