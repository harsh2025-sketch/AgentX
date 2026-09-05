"""Durable storage and integrity operations for canonical AgentX knowledge.

C2.02 owns the durable ``KnowledgeRecord`` store. C2.08 adds one canonical,
fail-closed lifecycle policy plus explicit contradiction and supersession
relationships without changing the schema-v1 ``KnowledgeRecord`` payload.

Knowledge state remains DATA, never authority. Status, contradiction, and
supersession operations never grant Permission, create AuthorityContext,
bypass ActionGate, lower risk, enlarge budgets, clear EmergencyStop, execute a
Capability, mutate Task state, publish Events, or decide which claim is true.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
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
from agentx.core.knowledge_integrity import (
    KnowledgeContradiction,
    KnowledgeStatusTransitionKind,
    KnowledgeSupersession,
    validate_knowledge_status_transition,
)
from agentx.infrastructure.persistence import (
    PersistenceError,
    SQLiteDatabase,
    TransactionError,
    TransactionStateError,
    transaction,
)

_KNOWLEDGE_TABLE: Final = "agentx_knowledge"
_CONTRADICTION_TABLE: Final = "agentx_knowledge_contradictions"
_SUPERSESSION_TABLE: Final = "agentx_knowledge_supersessions"

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
    """Raised when an integrity operation references knowledge that is not stored."""


class KnowledgeStoreStorageError(KnowledgeStoreError):
    """Raised when SQLite cannot complete a knowledge-store statement."""


class DuplicateKnowledgeRelationshipError(KnowledgeStoreError):
    """Base error for an already-persisted canonical relationship."""


class DuplicateContradictionError(DuplicateKnowledgeRelationshipError):
    """Raised when the same symmetric contradiction is recorded twice."""


class DuplicateSupersessionError(DuplicateKnowledgeRelationshipError):
    """Raised when the same directional supersession is recorded twice."""


class KnowledgeSupersessionCycleError(KnowledgeStoreError):
    """Raised when adding a supersession edge would create a directed cycle."""


class CorruptKnowledgeRecordError(KnowledgeStoreError):
    """Raised when persisted knowledge data cannot reconstruct its canonical record."""

    def __init__(self, *, knowledge_id: str) -> None:
        self.knowledge_id = knowledge_id
        super().__init__(f"Stored knowledge row {knowledge_id!r} is corrupt")


class CorruptKnowledgeRelationshipError(KnowledgeStoreError):
    """Raised when a persisted contradiction/supersession row is inconsistent."""

    def __init__(self, *, relationship: str) -> None:
        self.relationship = relationship
        super().__init__(f"Stored knowledge {relationship} relationship is corrupt")


@dataclass(frozen=True, slots=True)
class KnowledgeStore:
    """Store knowledge and apply explicit knowledge-integrity operations."""

    database: SQLiteDatabase

    def insert(self, record: KnowledgeRecord) -> None:
        """Atomically store one record without changing its lifecycle state."""
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
        """Return one stored record or ``None``. Retrieval grants zero authority."""
        _require_knowledge_id(knowledge_id)

        with self.database.connection() as connection:
            try:
                row = _select_knowledge_row(connection, knowledge_id)
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError(
                    f"Unable to read knowledge record {knowledge_id}"
                ) from exc

        if row is None:
            return None
        return _decode_row(row)

    def list_records(self) -> tuple[KnowledgeRecord, ...]:
        """Return every stored record ordered by creation time then identity."""
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
        """Apply one explicit canonical lifecycle transition atomically.

        ``CONFLICTED`` and ``SUPERSEDED`` cannot be entered through this generic
        method. They require ``record_contradiction(..., mark_conflicted=True)``
        and ``apply_supersession(...)`` respectively, which bind the exceptional
        state to a persisted relationship in the same transaction.

        For C2.02 compatibility, an explicit request to enter ``VERIFIED`` may
        omit ``verified_at``; only then the store records the operation time in
        UTC. A same-status VERIFIED request may explicitly refresh that
        historical timestamp. These timestamp mechanics never decide whether a
        transition is legal: evidence count, provenance wording, source kind,
        model output, timestamps, and hostile content cannot promote a record.
        Moving away from VERIFIED preserves its historical timestamp.
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
                with _write_transaction(connection):
                    row = _select_knowledge_row(connection, knowledge_id)
                    if row is None:
                        raise KnowledgeNotFoundError(
                            f"No stored knowledge record {knowledge_id} to update"
                        )
                    current = _decode_row(row)
                    validate_knowledge_status_transition(current.status, status)

                    if current.status is status:
                        if status is KnowledgeStatus.VERIFIED and verified_at is not None:
                            updated = replace(current, verified_at=verified_at)
                            _persist_record(connection, updated)
                            return updated
                        return current

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
                    _persist_record(connection, updated)
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError(
                    f"Unable to update status for knowledge record {knowledge_id}"
                ) from exc

        return updated

    def record_contradiction(
        self,
        relationship: KnowledgeContradiction,
        *,
        mark_conflicted: bool = False,
    ) -> KnowledgeContradiction:
        """Persist one symmetric contradiction, optionally conflicting both sides.

        Merely recording the relationship never changes either status. Setting
        ``mark_conflicted=True`` is an explicit request to persist the
        relationship and transition BOTH records to ``CONFLICTED`` atomically.
        No winner is selected and neither historical record is deleted.
        """
        if not isinstance(relationship, KnowledgeContradiction):
            raise TypeError("relationship must be a KnowledgeContradiction")
        if not isinstance(mark_conflicted, bool):
            raise TypeError("mark_conflicted must be a bool")

        with self.database.connection() as connection:
            try:
                with _write_transaction(connection):
                    first = _require_stored_record(connection, relationship.first_knowledge_id)
                    second = _require_stored_record(connection, relationship.second_knowledge_id)
                    _reject_duplicate_contradiction(connection, relationship)

                    if mark_conflicted:
                        validate_knowledge_status_transition(
                            first.status,
                            KnowledgeStatus.CONFLICTED,
                            kind=KnowledgeStatusTransitionKind.CONTRADICTION,
                        )
                        validate_knowledge_status_transition(
                            second.status,
                            KnowledgeStatus.CONFLICTED,
                            kind=KnowledgeStatusTransitionKind.CONTRADICTION,
                        )

                    connection.execute(
                        f"INSERT INTO {_CONTRADICTION_TABLE} "
                        "(first_knowledge_id, second_knowledge_id) VALUES (?, ?)",
                        (
                            relationship.first_knowledge_id.to_str(),
                            relationship.second_knowledge_id.to_str(),
                        ),
                    )

                    if mark_conflicted:
                        if first.status is not KnowledgeStatus.CONFLICTED:
                            _persist_record(
                                connection,
                                replace(first, status=KnowledgeStatus.CONFLICTED),
                            )
                        if second.status is not KnowledgeStatus.CONFLICTED:
                            _persist_record(
                                connection,
                                replace(second, status=KnowledgeStatus.CONFLICTED),
                            )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) in _DUPLICATE_ERROR_CODES:
                    raise DuplicateContradictionError(
                        "Knowledge contradiction already exists"
                    ) from exc
                raise KnowledgeStoreStorageError("Unable to record contradiction") from exc
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError("Unable to record contradiction") from exc

        return relationship

    def apply_supersession(self, relationship: KnowledgeSupersession) -> KnowledgeSupersession:
        """Persist supersession and mark the historical record SUPERSEDED atomically.

        The relationship is directional: ``replacement_knowledge_id``
        supersedes ``superseded_knowledge_id``. Cycles fail closed before any
        write. The older record remains stored and retrievable; its content,
        scope, provenance, identity, and historical verification timestamp are
        preserved.
        """
        if not isinstance(relationship, KnowledgeSupersession):
            raise TypeError("relationship must be a KnowledgeSupersession")

        with self.database.connection() as connection:
            try:
                with _write_transaction(connection):
                    _require_stored_record(connection, relationship.replacement_knowledge_id)
                    historical = _require_stored_record(
                        connection, relationship.superseded_knowledge_id
                    )
                    _reject_duplicate_supersession(connection, relationship)
                    _reject_supersession_cycle(connection, relationship)
                    validate_knowledge_status_transition(
                        historical.status,
                        KnowledgeStatus.SUPERSEDED,
                        kind=KnowledgeStatusTransitionKind.SUPERSESSION,
                    )

                    connection.execute(
                        f"INSERT INTO {_SUPERSESSION_TABLE} "
                        "(replacement_knowledge_id, superseded_knowledge_id) VALUES (?, ?)",
                        (
                            relationship.replacement_knowledge_id.to_str(),
                            relationship.superseded_knowledge_id.to_str(),
                        ),
                    )
                    if historical.status is not KnowledgeStatus.SUPERSEDED:
                        _persist_record(
                            connection,
                            replace(historical, status=KnowledgeStatus.SUPERSEDED),
                        )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) in _DUPLICATE_ERROR_CODES:
                    raise DuplicateSupersessionError(
                        "Knowledge supersession already exists"
                    ) from exc
                raise KnowledgeStoreStorageError("Unable to apply supersession") from exc
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError("Unable to apply supersession") from exc

        return relationship

    def list_contradictions(self) -> tuple[KnowledgeContradiction, ...]:
        """Return all contradictions in deterministic canonical pair order."""
        with self.database.connection() as connection:
            try:
                rows = connection.execute(
                    f"SELECT c.first_knowledge_id, c.second_knowledge_id, "
                    "k1.knowledge_id AS first_exists, k2.knowledge_id AS second_exists "
                    f"FROM {_CONTRADICTION_TABLE} AS c "
                    f"LEFT JOIN {_KNOWLEDGE_TABLE} AS k1 "
                    "ON k1.knowledge_id = c.first_knowledge_id "
                    f"LEFT JOIN {_KNOWLEDGE_TABLE} AS k2 "
                    "ON k2.knowledge_id = c.second_knowledge_id "
                    "ORDER BY c.first_knowledge_id ASC, c.second_knowledge_id ASC"
                ).fetchall()
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError("Unable to read contradictions") from exc

        return tuple(_decode_contradiction_row(row) for row in rows)

    def list_supersessions(self) -> tuple[KnowledgeSupersession, ...]:
        """Return all directional supersessions in deterministic identity order."""
        with self.database.connection() as connection:
            try:
                rows = connection.execute(
                    f"SELECT s.replacement_knowledge_id, s.superseded_knowledge_id, "
                    "kr.knowledge_id AS replacement_exists, "
                    "kh.knowledge_id AS superseded_exists "
                    f"FROM {_SUPERSESSION_TABLE} AS s "
                    f"LEFT JOIN {_KNOWLEDGE_TABLE} AS kr "
                    "ON kr.knowledge_id = s.replacement_knowledge_id "
                    f"LEFT JOIN {_KNOWLEDGE_TABLE} AS kh "
                    "ON kh.knowledge_id = s.superseded_knowledge_id "
                    "ORDER BY s.replacement_knowledge_id ASC, s.superseded_knowledge_id ASC"
                ).fetchall()
            except sqlite3.Error as exc:
                raise KnowledgeStoreStorageError("Unable to read supersessions") from exc

        return tuple(_decode_supersession_row(row) for row in rows)


@contextmanager
def _write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Serialize check-then-write integrity operations with ``BEGIN IMMEDIATE``."""
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


def _select_knowledge_row(
    connection: sqlite3.Connection, knowledge_id: KnowledgeId
) -> sqlite3.Row | None:
    row: sqlite3.Row | None = connection.execute(
        f"SELECT knowledge_id, created_at_utc, record_json FROM {_KNOWLEDGE_TABLE} "
        "WHERE knowledge_id = ?",
        (knowledge_id.to_str(),),
    ).fetchone()
    return row


def _require_stored_record(
    connection: sqlite3.Connection, knowledge_id: KnowledgeId
) -> KnowledgeRecord:
    row = _select_knowledge_row(connection, knowledge_id)
    if row is None:
        raise KnowledgeNotFoundError(f"No stored knowledge record {knowledge_id}")
    return _decode_row(row)


def _persist_record(connection: sqlite3.Connection, record: KnowledgeRecord) -> None:
    connection.execute(
        f"UPDATE {_KNOWLEDGE_TABLE} SET record_json = ? WHERE knowledge_id = ?",
        (record.to_json(), record.knowledge_id.to_str()),
    )


def _reject_duplicate_contradiction(
    connection: sqlite3.Connection, relationship: KnowledgeContradiction
) -> None:
    row = connection.execute(
        f"SELECT 1 FROM {_CONTRADICTION_TABLE} "
        "WHERE first_knowledge_id = ? AND second_knowledge_id = ?",
        (
            relationship.first_knowledge_id.to_str(),
            relationship.second_knowledge_id.to_str(),
        ),
    ).fetchone()
    if row is not None:
        raise DuplicateContradictionError("Knowledge contradiction already exists")


def _reject_duplicate_supersession(
    connection: sqlite3.Connection, relationship: KnowledgeSupersession
) -> None:
    row = connection.execute(
        f"SELECT 1 FROM {_SUPERSESSION_TABLE} "
        "WHERE replacement_knowledge_id = ? AND superseded_knowledge_id = ?",
        (
            relationship.replacement_knowledge_id.to_str(),
            relationship.superseded_knowledge_id.to_str(),
        ),
    ).fetchone()
    if row is not None:
        raise DuplicateSupersessionError("Knowledge supersession already exists")


def _reject_supersession_cycle(
    connection: sqlite3.Connection, relationship: KnowledgeSupersession
) -> None:
    # Adding R -> H is cyclic exactly when an existing path H -> ... -> R exists.
    row = connection.execute(
        f"WITH RECURSIVE descendants(knowledge_id) AS ("
        f"SELECT superseded_knowledge_id FROM {_SUPERSESSION_TABLE} "
        "WHERE replacement_knowledge_id = ? "
        "UNION "
        f"SELECT s.superseded_knowledge_id FROM {_SUPERSESSION_TABLE} AS s "
        "JOIN descendants AS d ON s.replacement_knowledge_id = d.knowledge_id"
        ") SELECT 1 FROM descendants WHERE knowledge_id = ? LIMIT 1",
        (
            relationship.superseded_knowledge_id.to_str(),
            relationship.replacement_knowledge_id.to_str(),
        ),
    ).fetchone()
    if row is not None:
        raise KnowledgeSupersessionCycleError(
            "Knowledge supersession would create a directed cycle"
        )


def _require_knowledge_id(knowledge_id: KnowledgeId) -> None:
    if not isinstance(knowledge_id, KnowledgeId):
        raise TypeError("knowledge_id must be a canonical KnowledgeId")


def _decode_row(row: sqlite3.Row) -> KnowledgeRecord:
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


def _decode_contradiction_row(row: sqlite3.Row) -> KnowledgeContradiction:
    if row["first_exists"] is None or row["second_exists"] is None:
        raise CorruptKnowledgeRelationshipError(relationship="contradiction")
    raw = {
        "first_knowledge_id": str(row["first_knowledge_id"]),
        "second_knowledge_id": str(row["second_knowledge_id"]),
    }
    try:
        relationship = KnowledgeContradiction.from_dict(raw)
    except KnowledgeValidationError as exc:
        raise CorruptKnowledgeRelationshipError(relationship="contradiction") from exc
    if relationship.to_dict() != raw:
        raise CorruptKnowledgeRelationshipError(relationship="contradiction")
    return relationship


def _decode_supersession_row(row: sqlite3.Row) -> KnowledgeSupersession:
    if row["replacement_exists"] is None or row["superseded_exists"] is None:
        raise CorruptKnowledgeRelationshipError(relationship="supersession")
    raw = {
        "replacement_knowledge_id": str(row["replacement_knowledge_id"]),
        "superseded_knowledge_id": str(row["superseded_knowledge_id"]),
    }
    try:
        return KnowledgeSupersession.from_dict(raw)
    except KnowledgeValidationError as exc:
        raise CorruptKnowledgeRelationshipError(relationship="supersession") from exc
