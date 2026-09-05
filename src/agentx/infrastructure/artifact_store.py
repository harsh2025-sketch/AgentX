"""Durable storage for canonical inert ArtifactRecord references (C2.04).

The store persists and returns :class:`agentx.core.artifacts.ArtifactRecord`
values over the canonical :class:`agentx.infrastructure.persistence.SQLiteDatabase`
foundation. It owns reference metadata only — it is deliberately not a blob
platform. There is no content I/O here, no object storage, no filesystem
artifact manager, no downloader/uploader, no compression, no deduplication, no
media processing, and no parsing of any stored value.

Inertness is the contract: a stored reference (path, ``file:``/``http(s)``
URL, code fragment, privilege-looking string) is data. Insert, get, and list
never open, fetch, import, execute, resolve, or trust anything. The only
durable effects are SQLite rows in ``agentx_artifacts``.

Artifact records are immutable snapshots. The store is append-oriented:
re-inserting an existing ``artifact_id`` fails explicitly
(:class:`DuplicateArtifactError`) and never overwrites, merges, or dedupes —
even when the incoming record is byte-identical to the stored one. There is
deliberately no update or delete surface; lifecycle policy is owned by
future tasks, not by storage.

Correlation fields (``task_id``/``episode_id``) are inert canonical
identifiers used for deterministic filtered reads. They are labels, not
foreign keys: nothing is joined against the episode or task machinery.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from agentx.core.artifacts import (
    ArtifactKind,
    ArtifactRecord,
    ArtifactValidationError,
    _format_timestamp,
)
from agentx.core.ids import ArtifactId, EpisodeId, TaskId
from agentx.infrastructure.persistence import (
    PersistenceError,
    SQLiteDatabase,
    TransactionError,
    TransactionStateError,
)

_ARTIFACT_TABLE: Final = "agentx_artifacts"

_DUPLICATE_ERROR_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
    }
)


@contextmanager
def _write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run one serialized write transaction with commit-or-rollback semantics.

    Same rationale as the ProcedureStore's write transaction: a deferred
    read-then-write upgrade can fail with a stale-snapshot ``SQLITE_BUSY``
    under the WAL journal instead of waiting on the busy timeout, so the
    write lock is taken up front with ``BEGIN IMMEDIATE``. Migration
    metadata application in ``agentx.infrastructure.persistence`` uses the
    same pattern.
    """
    if connection.in_transaction:
        raise TransactionStateError("Nested SQLite transactions are not supported")

    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as exc:
        raise TransactionError(f"Unable to begin SQLite write transaction: {exc}") from exc

    try:
        yield connection
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error as rollback_error:
            raise TransactionError(
                f"SQLite transaction failed and rollback also failed: {rollback_error}"
            ) from rollback_error
        raise
    else:
        try:
            connection.commit()
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error as rollback_error:
                raise TransactionError(
                    f"SQLite commit failed and rollback also failed: {rollback_error}"
                ) from rollback_error
            raise TransactionError(f"Unable to commit SQLite transaction: {exc}") from exc


class ArtifactStoreError(PersistenceError):
    """Base error for durable artifact-store operations."""


class DuplicateArtifactError(ArtifactStoreError):
    """Raised when an ``artifact_id`` already exists; stored rows never merge."""


class ArtifactStoreStorageError(ArtifactStoreError):
    """Raised when SQLite cannot complete an artifact-store statement."""


class CorruptArtifactRecordError(ArtifactStoreError):
    """Raised when a persisted artifact row cannot reconstruct its canonical record."""

    def __init__(self, *, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        super().__init__(f"Artifact row for artifact_id {artifact_id!r} is corrupt")


def _optional_id(id_obj: ArtifactId | TaskId | EpisodeId | None) -> str | None:
    return None if id_obj is None else id_obj.to_str()


@dataclass(frozen=True, slots=True)
class ArtifactStore:
    """Store and deterministically read canonical artifact references from SQLite."""

    database: SQLiteDatabase

    def insert(self, record: ArtifactRecord) -> None:
        """Atomically store one new artifact reference. Existing identities never change.

        A stored reference is inert text: inserting never opens, fetches,
        imports, executes, resolves, or trusts the reference, the media type,
        the digest, or any other field. Re-inserting an existing
        ``artifact_id`` raises :class:`DuplicateArtifactError` and leaves the
        stored row untouched — including when the incoming record is
        identical, which is a duplicate identity claim, not deduplication.
        """
        if not isinstance(record, ArtifactRecord):
            raise TypeError("record must be a canonical ArtifactRecord")

        values = (
            record.artifact_id.to_str(),
            record.kind.value,
            _format_timestamp(record.created_at),
            _optional_id(record.task_id),
            _optional_id(record.episode_id),
            record.to_json(),
        )

        with self.database.connection() as connection:
            try:
                with _write_transaction(connection):
                    connection.execute(
                        f"""
                        INSERT INTO {_ARTIFACT_TABLE}
                            (artifact_id, kind, created_at_utc, task_id, episode_id, record_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) in _DUPLICATE_ERROR_CODES:
                    raise DuplicateArtifactError(
                        f"Artifact {record.artifact_id} already exists in the ArtifactStore"
                    ) from exc
                raise ArtifactStoreStorageError(
                    f"Unable to store artifact {record.artifact_id}"
                ) from exc
            except sqlite3.Error as exc:
                raise ArtifactStoreStorageError(
                    f"Unable to store artifact {record.artifact_id}"
                ) from exc

    def get(self, artifact_id: ArtifactId) -> ArtifactRecord | None:
        """Return one artifact reference by canonical identity, or None when absent.

        Retrieval reconstructs exactly what was persisted — hostile text
        included — and never dereferences the reference.
        """
        if not isinstance(artifact_id, ArtifactId):
            raise TypeError("artifact_id must be an ArtifactId")

        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"""
                    SELECT artifact_id, kind, created_at_utc, task_id, episode_id, record_json
                    FROM {_ARTIFACT_TABLE}
                    WHERE artifact_id = ?
                    """,
                    (artifact_id.to_str(),),
                ).fetchone()
            except sqlite3.Error as exc:
                raise ArtifactStoreStorageError(f"Unable to get artifact {artifact_id}") from exc

        if row is None:
            return None
        return _decode_row(row)

    def list_records(
        self,
        *,
        kind: ArtifactKind | None = None,
        task_id: TaskId | None = None,
        episode_id: EpisodeId | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        """Return stored references in deterministic ``artifact_id`` ascending order.

        Ordering is by the storage primary key — canonical lowercase UUID
        strings — so enumeration is a total order that is stable across
        restarts, processes, and insertion order. Optional filters accept
        only the stable kind vocabulary and inert correlation identities;
        there is no retrieval, ranking, or matching policy in storage.
        """
        if kind is not None and not isinstance(kind, ArtifactKind):
            raise TypeError("kind must be an ArtifactKind or None")
        if task_id is not None and not isinstance(task_id, TaskId):
            raise TypeError("task_id must be a TaskId or None")
        if episode_id is not None and not isinstance(episode_id, EpisodeId):
            raise TypeError("episode_id must be an EpisodeId or None")

        clauses: list[str] = []
        parameters: list[str] = []
        if kind is not None:
            clauses.append("kind = ?")
            parameters.append(kind.value)
        if task_id is not None:
            clauses.append("task_id = ?")
            parameters.append(task_id.to_str())
        if episode_id is not None:
            clauses.append("episode_id = ?")
            parameters.append(episode_id.to_str())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        with self.database.connection() as connection:
            try:
                rows = connection.execute(
                    f"""
                    SELECT artifact_id, kind, created_at_utc, task_id, episode_id, record_json
                    FROM {_ARTIFACT_TABLE}{where}
                    ORDER BY artifact_id ASC
                    """,
                    tuple(parameters),
                ).fetchall()
            except sqlite3.Error as exc:
                raise ArtifactStoreStorageError("Unable to read the ArtifactStore") from exc

        return tuple(_decode_row(row) for row in rows)


def _decode_row(row: sqlite3.Row) -> ArtifactRecord:
    artifact_id_raw = row["artifact_id"]
    kind_raw = row["kind"]
    created_at_raw = row["created_at_utc"]
    task_id_raw = row["task_id"]
    episode_id_raw = row["episode_id"]
    record_json_raw = row["record_json"]

    if not isinstance(artifact_id_raw, str) or not artifact_id_raw:
        raise CorruptArtifactRecordError(artifact_id="<invalid>")
    if not isinstance(kind_raw, str) or not kind_raw:
        raise CorruptArtifactRecordError(artifact_id=artifact_id_raw)
    if not isinstance(created_at_raw, str) or not created_at_raw:
        raise CorruptArtifactRecordError(artifact_id=artifact_id_raw)
    if task_id_raw is not None and (not isinstance(task_id_raw, str) or not task_id_raw):
        raise CorruptArtifactRecordError(artifact_id=artifact_id_raw)
    if episode_id_raw is not None and (not isinstance(episode_id_raw, str) or not episode_id_raw):
        raise CorruptArtifactRecordError(artifact_id=artifact_id_raw)
    if not isinstance(record_json_raw, str) or not record_json_raw:
        raise CorruptArtifactRecordError(artifact_id=artifact_id_raw)

    try:
        record = ArtifactRecord.from_json(record_json_raw)
    except ArtifactValidationError as exc:
        raise CorruptArtifactRecordError(artifact_id=artifact_id_raw) from exc

    if (
        record.artifact_id.to_str() != artifact_id_raw
        or record.kind.value != kind_raw
        or _format_timestamp(record.created_at) != created_at_raw
        or _optional_id(record.task_id) != task_id_raw
        or _optional_id(record.episode_id) != episode_id_raw
    ):
        raise CorruptArtifactRecordError(artifact_id=artifact_id_raw)

    return record


__all__ = [
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactStoreStorageError",
    "CorruptArtifactRecordError",
    "DuplicateArtifactError",
]
