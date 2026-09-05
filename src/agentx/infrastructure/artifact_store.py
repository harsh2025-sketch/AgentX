"""SQLite-backed persistence for canonical inert ArtifactRecord values.

ArtifactStore stores metadata/references only. It never opens, fetches, imports,
executes, or otherwise interprets an artifact locator. Reads are deterministic
and historical artifact data grants no authority.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from agentx.core.artifacts import ArtifactRecord, ArtifactValidationError
from agentx.core.ids import ArtifactId, TaskId
from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase, transaction

__all__ = [
    "ArtifactEntry",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactStoreStorageError",
    "CorruptArtifactRecordError",
    "DuplicateArtifactError",
]

_ARTIFACT_TABLE: Final = "agentx_artifacts"
_DUPLICATE_ERROR_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
    }
)


class ArtifactStoreError(PersistenceError):
    """Base error for C2.04 ArtifactStore operations."""


class DuplicateArtifactError(ArtifactStoreError):
    """Raised when an ArtifactId already exists."""


class ArtifactStoreStorageError(ArtifactStoreError):
    """Raised when SQLite cannot complete an ArtifactStore operation."""


class CorruptArtifactRecordError(ArtifactStoreError):
    """Raised when persisted artifact data cannot reconstruct canonically."""

    def __init__(self, *, sequence: int, artifact_id: str) -> None:
        self.sequence = sequence
        self.artifact_id = artifact_id
        super().__init__(
            f"Stored artifact row sequence={sequence} artifact_id={artifact_id!r} is corrupt"
        )


@dataclass(frozen=True, slots=True)
class ArtifactEntry:
    """One durable ArtifactStore entry in canonical append order."""

    sequence: int
    record: ArtifactRecord

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("sequence must be a positive int")
        if not isinstance(self.record, ArtifactRecord):
            raise TypeError("record must be an ArtifactRecord")


@dataclass(frozen=True, slots=True)
class ArtifactStore:
    """Register and deterministically read artifact metadata from SQLite."""

    database: SQLiteDatabase

    def register(self, record: ArtifactRecord) -> int:
        """Atomically register one artifact and return its durable sequence."""

        if not isinstance(record, ArtifactRecord):
            raise TypeError("record must be a canonical ArtifactRecord")

        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    cursor = connection.execute(
                        f"INSERT INTO {_ARTIFACT_TABLE} "
                        "(artifact_id, task_id, correlation_id, artifact_json) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            record.artifact_id.to_str(),
                            record.task_id.to_str() if record.task_id is not None else None,
                            str(record.correlation_id)
                            if record.correlation_id is not None
                            else None,
                            record.to_json(),
                        ),
                    )
                    sequence = cursor.lastrowid
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) in _DUPLICATE_ERROR_CODES:
                    raise DuplicateArtifactError(
                        f"Artifact {record.artifact_id} already exists"
                    ) from exc
                raise ArtifactStoreStorageError(
                    f"Unable to register artifact {record.artifact_id}"
                ) from exc
            except sqlite3.Error as exc:
                raise ArtifactStoreStorageError(
                    f"Unable to register artifact {record.artifact_id}"
                ) from exc

        if not isinstance(sequence, int) or sequence <= 0:
            raise ArtifactStoreStorageError("SQLite did not return a valid artifact sequence")
        return sequence

    def get(self, artifact_id: ArtifactId) -> ArtifactRecord | None:
        """Return one artifact record by identity, or ``None`` when absent."""

        _require_artifact_id(artifact_id)
        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"SELECT sequence, artifact_id, task_id, correlation_id, artifact_json "
                    f"FROM {_ARTIFACT_TABLE} WHERE artifact_id = ?",
                    (artifact_id.to_str(),),
                ).fetchone()
            except sqlite3.Error as exc:
                raise ArtifactStoreStorageError(f"Unable to read artifact {artifact_id}") from exc

        if row is None:
            return None
        return _decode_row(row).record

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[ArtifactEntry, ...]:
        """Return deterministic ascending-sequence artifact entries.

        ``after_sequence`` is exclusive. Filtering is deliberately limited to
        stable TaskId/correlation dimensions; C2.04 implements no Hive retrieval.
        """

        _validate_read_arguments(
            after_sequence=after_sequence,
            limit=limit,
            task_id=task_id,
            correlation_id=correlation_id,
        )
        clauses = ["sequence > ?"]
        parameters: list[object] = [after_sequence]
        if task_id is not None:
            clauses.append("task_id = ?")
            parameters.append(task_id.to_str())
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            parameters.append(str(correlation_id))

        sql = (
            "SELECT sequence, artifact_id, task_id, correlation_id, artifact_json "
            f"FROM {_ARTIFACT_TABLE} WHERE {' AND '.join(clauses)} "
            "ORDER BY sequence ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)

        with self.database.connection() as connection:
            try:
                rows = connection.execute(sql, tuple(parameters)).fetchall()
            except sqlite3.Error as exc:
                raise ArtifactStoreStorageError("Unable to read ArtifactStore") from exc

        return tuple(_decode_row(row) for row in rows)


def _require_artifact_id(value: ArtifactId) -> None:
    if not isinstance(value, ArtifactId):
        raise TypeError("artifact_id must be a canonical ArtifactId")


def _validate_read_arguments(
    *,
    after_sequence: int,
    limit: int | None,
    task_id: TaskId | None,
    correlation_id: UUID | None,
) -> None:
    if type(after_sequence) is not int or after_sequence < 0:
        raise ValueError("after_sequence must be a non-negative int")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("limit must be a positive int or None")
    if task_id is not None and not isinstance(task_id, TaskId):
        raise TypeError("task_id must be a TaskId or None")
    if correlation_id is not None:
        if not isinstance(correlation_id, UUID):
            raise TypeError("correlation_id must be a UUID or None")
        if correlation_id.int == 0:
            raise ValueError("correlation_id must not be the nil UUID")


def _decode_row(row: sqlite3.Row) -> ArtifactEntry:
    sequence = int(row["sequence"])
    artifact_id = str(row["artifact_id"])
    try:
        record = ArtifactRecord.from_json(str(row["artifact_json"]))
    except ArtifactValidationError as exc:
        raise CorruptArtifactRecordError(sequence=sequence, artifact_id=artifact_id) from exc

    expected_task = record.task_id.to_str() if record.task_id is not None else None
    expected_correlation = str(record.correlation_id) if record.correlation_id is not None else None
    if record.artifact_id.to_str() != artifact_id:
        raise CorruptArtifactRecordError(sequence=sequence, artifact_id=artifact_id)
    if row["task_id"] != expected_task:
        raise CorruptArtifactRecordError(sequence=sequence, artifact_id=artifact_id)
    if row["correlation_id"] != expected_correlation:
        raise CorruptArtifactRecordError(sequence=sequence, artifact_id=artifact_id)
    return ArtifactEntry(sequence=sequence, record=record)
