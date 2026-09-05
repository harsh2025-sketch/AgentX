"""SQLite-backed durable storage for canonical NegativeExperienceRecord data (C2.06).

The store is append-only and deterministic. It stores historical evidence that
an approach failed; it never suppresses retries, prohibits actions, or grants
authority. Reading a stored failure has no effect other than returning data.

Corruption fails closed: a persisted row that cannot reconstruct its canonical
record raises :class:`CorruptNegativeExperienceError` rather than returning
partial or repaired data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from agentx.core.ids import EpisodeId, NegativeExperienceId, TaskId
from agentx.core.negative_experience import (
    NegativeExperienceRecord,
    NegativeExperienceValidationError,
)
from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase, transaction

_NEGATIVE_EXPERIENCE_TABLE: Final = "agentx_negative_experiences"

_DUPLICATE_ERROR_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
    }
)

__all__ = [
    "CorruptNegativeExperienceError",
    "DuplicateNegativeExperienceError",
    "NegativeExperienceEntry",
    "NegativeExperienceStore",
    "NegativeExperienceStoreError",
    "NegativeExperienceStoreStorageError",
]


class NegativeExperienceStoreError(PersistenceError):
    """Base error for durable negative-experience operations."""


class DuplicateNegativeExperienceError(NegativeExperienceStoreError):
    """Raised when a negative_experience_id already exists in the append-only store."""


class NegativeExperienceStoreStorageError(NegativeExperienceStoreError):
    """Raised when SQLite cannot complete a negative-experience statement."""


class CorruptNegativeExperienceError(NegativeExperienceStoreError):
    """Raised when a persisted row cannot reconstruct its canonical record."""

    def __init__(self, *, sequence: int, negative_experience_id: str) -> None:
        self.sequence = sequence
        self.negative_experience_id = negative_experience_id
        super().__init__(
            f"Negative experience row sequence {sequence} for {negative_experience_id!r} is corrupt"
        )


@dataclass(frozen=True, slots=True)
class NegativeExperienceEntry:
    """One persisted negative experience paired with its durable store sequence."""

    sequence: int
    record: NegativeExperienceRecord


@dataclass(frozen=True, slots=True)
class NegativeExperienceStore:
    """Append and deterministically read remembered failed approaches."""

    database: SQLiteDatabase

    def append(self, record: NegativeExperienceRecord) -> int:
        """Atomically append one record and return its committed durable sequence."""
        if not isinstance(record, NegativeExperienceRecord):
            raise TypeError("record must be a canonical NegativeExperienceRecord")

        parameters = (
            record.negative_experience_id.to_str(),
            None if record.episode_id is None else record.episode_id.to_str(),
            None if record.task_id is None else record.task_id.to_str(),
            record.to_json(),
        )

        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    cursor = connection.execute(
                        f"""
                        INSERT INTO {_NEGATIVE_EXPERIENCE_TABLE}
                            (negative_experience_id, episode_id, task_id, record_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        parameters,
                    )
                    sequence = cursor.lastrowid
                    if sequence is None:  # pragma: no cover - defensive
                        raise NegativeExperienceStoreStorageError(
                            "SQLite did not return a sequence for the appended record"
                        )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) in _DUPLICATE_ERROR_CODES:
                    raise DuplicateNegativeExperienceError(
                        f"Negative experience {record.negative_experience_id} already exists"
                    ) from exc
                raise NegativeExperienceStoreStorageError(
                    f"Unable to append negative experience {record.negative_experience_id}"
                ) from exc
            except sqlite3.Error as exc:
                raise NegativeExperienceStoreStorageError(
                    f"Unable to append negative experience {record.negative_experience_id}"
                ) from exc

        return sequence

    def get(self, negative_experience_id: NegativeExperienceId) -> NegativeExperienceRecord | None:
        """Return one record by canonical identity, or None when it is absent."""
        if not isinstance(negative_experience_id, NegativeExperienceId):
            raise TypeError("negative_experience_id must be a NegativeExperienceId")

        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"""
                    SELECT sequence, negative_experience_id, episode_id, task_id, record_json
                    FROM {_NEGATIVE_EXPERIENCE_TABLE}
                    WHERE negative_experience_id = ?
                    """,
                    (negative_experience_id.to_str(),),
                ).fetchone()
            except sqlite3.Error as exc:
                raise NegativeExperienceStoreStorageError(
                    f"Unable to get negative experience {negative_experience_id}"
                ) from exc

        if row is None:
            return None
        return _decode_row(row).record

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        episode_id: EpisodeId | None = None,
        task_id: TaskId | None = None,
    ) -> tuple[NegativeExperienceEntry, ...]:
        """Read records in ascending durable sequence order.

        ``after_sequence`` is exclusive. Filters are restricted to stable
        canonical associations; no ranking, scoring, or similarity exists here.
        """
        _validate_read_boundary(after_sequence=after_sequence, limit=limit)
        if episode_id is not None and not isinstance(episode_id, EpisodeId):
            raise TypeError("episode_id must be an EpisodeId or None")
        if task_id is not None and not isinstance(task_id, TaskId):
            raise TypeError("task_id must be a TaskId or None")

        clauses = ["sequence > ?"]
        parameters: list[int | str] = [after_sequence]
        if episode_id is not None:
            clauses.append("episode_id = ?")
            parameters.append(episode_id.to_str())
        if task_id is not None:
            clauses.append("task_id = ?")
            parameters.append(task_id.to_str())

        sql = (
            "SELECT sequence, negative_experience_id, episode_id, task_id, record_json "
            f"FROM {_NEGATIVE_EXPERIENCE_TABLE} WHERE {' AND '.join(clauses)} "
            "ORDER BY sequence ASC"
        )
        if limit is not None:
            sql = f"{sql} LIMIT ?"
            parameters.append(limit)

        with self.database.connection() as connection:
            try:
                rows = connection.execute(sql, tuple(parameters)).fetchall()
            except sqlite3.Error as exc:
                raise NegativeExperienceStoreStorageError(
                    "Unable to read negative experience history"
                ) from exc

        return tuple(_decode_row(row) for row in rows)


def _validate_read_boundary(*, after_sequence: int, limit: int | None) -> None:
    if (
        not isinstance(after_sequence, int)
        or isinstance(after_sequence, bool)
        or after_sequence < 0
    ):
        raise ValueError("after_sequence must be a non-negative integer")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise ValueError("limit must be a positive integer or None")


def _decode_row(row: sqlite3.Row) -> NegativeExperienceEntry:
    sequence_raw = row["sequence"]
    identity_raw = row["negative_experience_id"]
    episode_id_raw = row["episode_id"]
    task_id_raw = row["task_id"]
    record_json_raw = row["record_json"]

    if not isinstance(sequence_raw, int) or isinstance(sequence_raw, bool) or sequence_raw <= 0:
        raise CorruptNegativeExperienceError(sequence=0, negative_experience_id="<invalid>")
    if not isinstance(identity_raw, str) or not identity_raw:
        raise CorruptNegativeExperienceError(
            sequence=sequence_raw, negative_experience_id="<invalid>"
        )
    if not isinstance(record_json_raw, str) or not record_json_raw:
        raise CorruptNegativeExperienceError(
            sequence=sequence_raw, negative_experience_id=identity_raw
        )

    try:
        record = NegativeExperienceRecord.from_json(record_json_raw)
    except NegativeExperienceValidationError as exc:
        raise CorruptNegativeExperienceError(
            sequence=sequence_raw, negative_experience_id=identity_raw
        ) from exc

    expected_episode_id = None if record.episode_id is None else record.episode_id.to_str()
    expected_task_id = None if record.task_id is None else record.task_id.to_str()
    if (
        record.negative_experience_id.to_str() != identity_raw
        or expected_episode_id != episode_id_raw
        or expected_task_id != task_id_raw
    ):
        raise CorruptNegativeExperienceError(
            sequence=sequence_raw, negative_experience_id=identity_raw
        )

    return NegativeExperienceEntry(sequence=sequence_raw, record=record)
