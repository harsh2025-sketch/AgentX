"""SQLite-backed append-only persistence for inert audit-history snapshots.

AuditStore persists :class:`agentx.core.audit_records.AuditRecordSnapshot`
values over the canonical SQLite substrate. It has no update/delete API, grants
no authority, executes nothing, and has no EventJournal/EventBus coupling.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from agentx.core.audit_records import AuditRecordSnapshot, AuditRecordValidationError
from agentx.core.ids import TaskId
from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase, transaction

__all__ = [
    "AuditEntry",
    "AuditStore",
    "AuditStoreError",
    "AuditStoreStorageError",
    "CorruptAuditRecordError",
    "DuplicateAuditRecordError",
]

_AUDIT_TABLE: Final = "agentx_audit_records"
_DUPLICATE_ERROR_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
    }
)


class AuditStoreError(PersistenceError):
    """Base error for C2.04 AuditStore operations."""


class DuplicateAuditRecordError(AuditStoreError):
    """Raised when an audit identity already exists."""


class AuditStoreStorageError(AuditStoreError):
    """Raised when SQLite cannot complete an AuditStore operation."""


class CorruptAuditRecordError(AuditStoreError):
    """Raised when persisted audit history cannot reconstruct canonically."""

    def __init__(self, *, sequence: int, audit_id: str) -> None:
        self.sequence = sequence
        self.audit_id = audit_id
        super().__init__(
            f"Stored audit row sequence={sequence} audit_id={audit_id!r} is corrupt"
        )


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One immutable durable audit entry in append order."""

    sequence: int
    record: AuditRecordSnapshot

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("sequence must be a positive int")
        if not isinstance(self.record, AuditRecordSnapshot):
            raise TypeError("record must be an AuditRecordSnapshot")


@dataclass(frozen=True, slots=True)
class AuditStore:
    """Append and deterministically read immutable audit history."""

    database: SQLiteDatabase

    def append(self, record: AuditRecordSnapshot) -> int:
        """Atomically append one historical audit snapshot."""

        if not isinstance(record, AuditRecordSnapshot):
            raise TypeError("record must be an AuditRecordSnapshot")

        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    cursor = connection.execute(
                        f"INSERT INTO {_AUDIT_TABLE} "
                        "(audit_id, task_id, correlation_id, audit_json) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            str(record.audit_id),
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
                    raise DuplicateAuditRecordError(
                        f"Audit record {record.audit_id} already exists"
                    ) from exc
                raise AuditStoreStorageError(
                    f"Unable to append audit record {record.audit_id}"
                ) from exc
            except sqlite3.Error as exc:
                raise AuditStoreStorageError(
                    f"Unable to append audit record {record.audit_id}"
                ) from exc

        if not isinstance(sequence, int) or sequence <= 0:
            raise AuditStoreStorageError("SQLite did not return a valid audit sequence")
        return sequence

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[AuditEntry, ...]:
        """Return immutable audit history in ascending durable sequence order."""

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
            "SELECT sequence, audit_id, task_id, correlation_id, audit_json "
            f"FROM {_AUDIT_TABLE} WHERE {' AND '.join(clauses)} "
            "ORDER BY sequence ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)

        with self.database.connection() as connection:
            try:
                rows = connection.execute(sql, tuple(parameters)).fetchall()
            except sqlite3.Error as exc:
                raise AuditStoreStorageError("Unable to read AuditStore") from exc

        return tuple(_decode_row(row) for row in rows)


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


def _decode_row(row: sqlite3.Row) -> AuditEntry:
    sequence = int(row["sequence"])
    audit_id = str(row["audit_id"])
    try:
        record = AuditRecordSnapshot.from_json(str(row["audit_json"]))
    except AuditRecordValidationError as exc:
        raise CorruptAuditRecordError(sequence=sequence, audit_id=audit_id) from exc

    expected_task = record.task_id.to_str() if record.task_id is not None else None
    expected_correlation = (
        str(record.correlation_id) if record.correlation_id is not None else None
    )
    if str(record.audit_id) != audit_id:
        raise CorruptAuditRecordError(sequence=sequence, audit_id=audit_id)
    if row["task_id"] != expected_task:
        raise CorruptAuditRecordError(sequence=sequence, audit_id=audit_id)
    if row["correlation_id"] != expected_correlation:
        raise CorruptAuditRecordError(sequence=sequence, audit_id=audit_id)
    return AuditEntry(sequence=sequence, record=record)
