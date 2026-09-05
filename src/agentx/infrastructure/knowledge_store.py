"""Durable storage for canonical AgentX knowledge records (C2.02).

The store persists and returns :class:`agentx.core.knowledge.KnowledgeRecord`
data over the canonical :class:`agentx.infrastructure.persistence.SQLiteDatabase`
foundation. It owns no lifecycle policy: insert/get/list are data-only, and
the only mutation is the explicit ``update_status`` call, which records a
caller-chosen canonical status verbatim.

Reading or writing knowledge never grants Permission, changes RiskLevel,
increases a ResourceEnvelope, bypasses the Action Gate, clears an
EmergencyStop, executes a Capability, changes Task state, publishes Events, or
appends to the EventJournal. Hostile content is inert data.

Query capability is deliberately minimal — identity lookup plus deterministic
full iteration. Semantic retrieval, ranking, and filtering are owned by Hive
retrieval (C2.09).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeStatus,
    KnowledgeValidationError,
    _format_timestamp,
)
from agentx.infrastructure.persistence import (
    PersistenceError,
    SQLiteDatabase,
    transaction,
)

_KNOWLEDGE_TABLE: Final = "agentx_knowledge"

_DUPLICATE_ERROR_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
    }
)


class KnowledgeStoreError(PersistenceError):
    """Base error for durable knowledge-store operations."""


class DuplicateKnowledgeError(KnowledgeStoreError):
    """Raised when a knowledge_id already exists in the store."""


class KnowledgeNotFoundError(KnowledgeStoreError):
    """Raised when an explicit update targets a knowledge_id that is not stored."""


class KnowledgeStoreStorageError(KnowledgeStoreError):
    """Raised when SQLite cannot complete a knowledge-store statement."""


class CorruptKnowledgeRecordError(KnowledgeStoreError):
    """Raised when persisted knowledge data cannot reconstruct its canonical record."""

    def __init__(self, *, knowledge_id: str) -> None:
        self.knowledge_id = knowledge_id
        super().__init__(f"Stored knowledge row {knowledge_id!r} is corrupt")


@dataclass(frozen=True, slots=True)
class KnowledgeStore:
    """Store and deterministically read canonical knowledge records from SQLite."""

    database: SQLiteDatabase

    def insert(self, record: KnowledgeRecord) -> None:
        """Atomically store one record. Inserting never changes any stored status.

        Duplicate ``knowledge_id`` fails explicitly with
        :class:`DuplicateKnowledgeError`; the existing row is never silently
        overwritten, and re-persisting identical content under a new identity
        never increases trust — records do not aggregate.
        """
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("record must be a canonical KnowledgeRecord")

        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    connection.execute(
                        f"INSERT INTO {_KNOWLEDGE_TABLE} "
                        "(knowledge_id, created_at_utc, record_json) VALUES (?, ?, ?)",
                        (
                            record.knowledge_id.to_str(),
                            _format_timestamp(record.created_at),
                            record.to_json(),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) in _DUPLICATE_ERROR_CODES:
                    raise DuplicateKnowledgeError(
                        f"Knowledge record {record.knowledge_id} already exists"
                    ) from exc
                raise KnowledgeStoreStorageError(
                    f"Unable to store knowledge record {record.knowledge_id}"
                ) from exc
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError(
                    f"Unable to store knowledge record {record.knowledge_id}"
                ) from exc

    def get(self, knowledge_id: KnowledgeId) -> KnowledgeRecord | None:
        """Return the stored record for ``knowledge_id`` or ``None`` if absent.

        Retrieval is data-only: it reconstructs exactly what was persisted,
        including a historical ``VERIFIED`` status, and grants zero authority.
        """
        _require_knowledge_id(knowledge_id)

        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"SELECT knowledge_id, created_at_utc, record_json FROM {_KNOWLEDGE_TABLE} "
                    "WHERE knowledge_id = ?",
                    (knowledge_id.to_str(),),
                ).fetchone()
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError(
                    f"Unable to read knowledge record {knowledge_id}"
                ) from exc

        if row is None:
            return None
        return _decode_row(row)

    def list_records(self) -> tuple[KnowledgeRecord, ...]:
        """Return every stored record in deterministic order.

        Ordering is ``(created_at, knowledge_id)`` ascending — a total order,
        because identities are unique — so iteration is stable across
        restarts, processes, and insertion order.
        """
        with self.database.connection() as connection:
            try:
                rows = connection.execute(
                    f"SELECT knowledge_id, created_at_utc, record_json FROM {_KNOWLEDGE_TABLE} "
                    "ORDER BY created_at_utc ASC, knowledge_id ASC"
                ).fetchall()
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError("Unable to read the knowledge store") from exc

        return tuple(_decode_row(row) for row in rows)

    def update_status(
        self,
        knowledge_id: KnowledgeId,
        status: KnowledgeStatus,
        *,
        verified_at: datetime | None = None,
    ) -> KnowledgeRecord:
        """Explicitly set a stored record's status and return the updated record.

        Semantics:

            - The transition is recorded verbatim. The store performs no
              automatic promotion, demotion, aggregation, or lifecycle-rule
              validation — status policy is owned by future Hive tasks
              (C2.08), and verification evidence by C2.07.
            - Target ``VERIFIED`` requires an explicit verification timestamp;
              when ``verified_at`` is omitted it defaults to ``now`` (UTC).
              This records WHEN verification happened — it is not authority.
            - Any other target status preserves the existing ``verified_at``
              as historical data (moving away from VERIFIED does not erase
              when verification last occurred). Passing ``verified_at`` with a
              non-VERIFIED target is rejected as ambiguous.
            - Unknown ``knowledge_id`` raises :class:`KnowledgeNotFoundError`
              and writes nothing.

        The returned record is the newly persisted value; the previously
        returned record objects remain immutable snapshots of history.
        """
        _require_knowledge_id(knowledge_id)
        if not isinstance(status, KnowledgeStatus):
            raise KnowledgeValidationError("status must be a KnowledgeStatus")

        if status is not KnowledgeStatus.VERIFIED and verified_at is not None:
            raise KnowledgeValidationError(
                "verified_at may only be provided when the target status is VERIFIED"
            )

        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    row = connection.execute(
                        f"SELECT knowledge_id, created_at_utc, record_json FROM {_KNOWLEDGE_TABLE} "
                        "WHERE knowledge_id = ?",
                        (knowledge_id.to_str(),),
                    ).fetchone()
                    if row is None:
                        raise KnowledgeNotFoundError(
                            f"No stored knowledge record {knowledge_id} to update"
                        )
                    current = _decode_row(row)
                    new_verified_at = (
                        current.verified_at
                        if status is not KnowledgeStatus.VERIFIED
                        else (datetime.now(UTC) if verified_at is None else verified_at)
                    )
                    updated = replace(
                        current,
                        status=status,
                        verified_at=new_verified_at,
                    )
                    connection.execute(
                        f"UPDATE {_KNOWLEDGE_TABLE} SET record_json = ? WHERE knowledge_id = ?",
                        (updated.to_json(), knowledge_id.to_str()),
                    )
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError(
                    f"Unable to update status for knowledge record {knowledge_id}"
                ) from exc

        return updated


def _require_knowledge_id(knowledge_id: KnowledgeId) -> None:
    if not isinstance(knowledge_id, KnowledgeId):
        raise TypeError("knowledge_id must be a canonical KnowledgeId")


def _decode_row(row: sqlite3.Row) -> KnowledgeRecord:
    """Reconstruct one record from a stored row, rejecting malformed data."""
    knowledge_id = str(row["knowledge_id"])
    record_json = str(row["record_json"])
    try:
        record = KnowledgeRecord.from_json(record_json)
    except KnowledgeValidationError as exc:
        raise CorruptKnowledgeRecordError(knowledge_id=knowledge_id) from exc
    if record.knowledge_id.to_str() != knowledge_id:
        raise CorruptKnowledgeRecordError(knowledge_id=knowledge_id)
    if _format_timestamp(record.created_at) != str(row["created_at_utc"]):
        raise CorruptKnowledgeRecordError(knowledge_id=knowledge_id)
    return record
