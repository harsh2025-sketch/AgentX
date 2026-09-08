"""Durable storage for canonical AgentX procedure records (C2.03).

The store persists and returns :class:`agentx.core.procedures.ProcedureRecord`
revisions over the canonical :class:`agentx.infrastructure.persistence.SQLiteDatabase`
foundation. It owns no procedure semantics: no Procedure Graph IR, no
interpreter, no compiler, no candidate promotion, and no lifecycle policy.
Insert/get/history/list are data-only, and the mutation surface is the explicit
``update_status`` call, which records a caller-chosen canonical status
verbatim, plus its compare-and-set variant ``update_status_if_current`` — the
narrow seam through which the explicit promotion transaction (N2.10) commits a
status change only while the stored revision is still exactly the record the
caller validated.

Reading or writing procedure records never grants Permission, changes
RiskLevel, enlarges a ResourceEnvelope, bypasses the Action Gate, clears an
EmergencyStop, executes a Capability or the procedure itself, changes Task
state, publishes Events, or appends to the EventJournal. Historical procedure
records — including hostile payloads and records whose status is ACTIVE — are
inert data. Payload content is never parsed or interpreted here.

Revision sequencing is a storage-level integrity rule, not lifecycle policy:
revisions of one procedure identity are append-only, contiguous, and start at
1. A stored revision is never silently overwritten; re-storing an existing
``(procedure_id, revision)`` pair fails explicitly, and skipping ahead or
back-filling a revision number fails explicitly.

Query capability is deliberately minimal — identity/version lookup, per-
procedure revision history, and deterministic full iteration. Retrieval,
ranking, matching, and any graph semantics are owned by future tasks (the
Procedure Graph IR is A3.01; candidate-skill lifecycle/trust is C3.09).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

from agentx.core.ids import ProcedureId
from agentx.core.procedures import (
    ProcedureRecord,
    ProcedureStatus,
    ProcedureValidationError,
    _format_timestamp,
)
from agentx.infrastructure.persistence import (
    PersistenceError,
    SQLiteDatabase,
    TransactionError,
    TransactionStateError,
)

_PROCEDURE_TABLE: Final = "agentx_procedures"

_DUPLICATE_ERROR_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
    }
)


class ProcedureStoreError(PersistenceError):
    """Base error for durable procedure-store operations."""


class DuplicateProcedureRevisionError(ProcedureStoreError):
    """Raised when a stored (procedure_id, revision) pair is stored again."""


class ProcedureRevisionSequenceError(ProcedureStoreError):
    """Raised when a revision is not exactly the next append-only revision."""


class ProcedureNotFoundError(ProcedureStoreError):
    """Raised when an explicit update targets a revision that is not stored."""


class ProcedureStaleRecordError(ProcedureStoreError):
    """Raised when a compare-and-set update no longer matches the stored revision.

    The stored ``(procedure_id, revision)`` row changed after the caller read
    the ``expected_current`` record (a concurrent status transition, for
    example), so the conditional write is refused and nothing is persisted.
    The caller must re-read the current record and re-derive its decision.
    """

    def __init__(self, *, procedure_id: str, revision: int) -> None:
        self.procedure_id = procedure_id
        self.revision = revision
        super().__init__(
            f"Procedure {procedure_id!r} revision {revision} changed since the caller "
            "read it; the conditional status update was refused and nothing was written"
        )


class ProcedureStoreStorageError(ProcedureStoreError):
    """Raised when SQLite cannot complete a procedure-store statement."""


class CorruptProcedureRecordError(ProcedureStoreError):
    """Raised when persisted procedure data cannot reconstruct its canonical record."""

    def __init__(self, *, procedure_id: str, revision: int) -> None:
        self.procedure_id = procedure_id
        self.revision = revision
        super().__init__(f"Stored procedure row ({procedure_id!r}, revision {revision}) is corrupt")


@dataclass(frozen=True, slots=True)
class ProcedureStore:
    """Store and deterministically read canonical procedure records from SQLite."""

    database: SQLiteDatabase

    def insert(self, record: ProcedureRecord) -> None:
        """Atomically store one new revision. Existing revisions are never overwritten.

        Semantics:

            - The pair ``(procedure_id, revision)`` is the storage identity.
              Storing a pair that already exists fails explicitly with
              :class:`DuplicateProcedureRevisionError`; the existing row is
              never silently overwritten or merged.
            - Revisions of one procedure identity are append-only and
              contiguous: the first stored revision must be ``1`` and each
              next revision must be exactly ``max(stored) + 1``. Gaps,
              back-fills, and out-of-order revisions fail explicitly with
              :class:`ProcedureRevisionSequenceError` and write nothing.
            - Inserting never changes any stored status. New revisions are
              data the moment they are stored — even a record whose status is
              ``ACTIVE`` grants zero authority by existing.

        The check-then-write sequence runs inside one ``BEGIN IMMEDIATE``
        transaction so concurrent writers cannot interleave a duplicate or an
        out-of-order revision between the check and the insert.
        """
        if not isinstance(record, ProcedureRecord):
            raise TypeError("record must be a canonical ProcedureRecord")

        with self.database.connection() as connection:
            try:
                with _write_transaction(connection):
                    _reject_duplicate(connection, record)
                    _reject_nonsequential_revision(connection, record)
                    connection.execute(
                        f"INSERT INTO {_PROCEDURE_TABLE} "
                        "(procedure_id, revision, created_at_utc, status, record_json) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            record.procedure_id.to_str(),
                            record.revision,
                            _format_timestamp(record.created_at),
                            record.status.value,
                            record.to_json(),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                # BEGIN IMMEDIATE serializes writers, so the explicit checks
                # above normally catch duplicates first. The primary-key
                # constraint remains the final fail-closed authority.
                if getattr(exc, "sqlite_errorcode", None) in _DUPLICATE_ERROR_CODES:
                    raise DuplicateProcedureRevisionError(
                        f"Procedure {record.procedure_id} revision {record.revision} already exists"
                    ) from exc
                raise ProcedureStoreStorageError(
                    f"Unable to store procedure {record.procedure_id} revision {record.revision}"
                ) from exc
            except sqlite3.Error as exc:
                raise ProcedureStoreStorageError(
                    f"Unable to store procedure {record.procedure_id} revision {record.revision}"
                ) from exc

    def get(self, procedure_id: ProcedureId, revision: int) -> ProcedureRecord | None:
        """Return the stored revision or ``None`` if that identity/version is absent.

        Retrieval is data-only: it reconstructs exactly what was persisted —
        including a historical ``ACTIVE`` status or a hostile payload — and
        grants zero authority.
        """
        _require_procedure_id(procedure_id)
        _require_revision(revision)

        with self.database.connection() as connection:
            try:
                row = _select_revision(connection, procedure_id, revision)
            except sqlite3.Error as exc:
                raise ProcedureStoreStorageError(
                    f"Unable to read procedure {procedure_id} revision {revision}"
                ) from exc

        if row is None:
            return None
        return _decode_row(row)

    def history(self, procedure_id: ProcedureId) -> tuple[ProcedureRecord, ...]:
        """Return every stored revision of one procedure in ascending revision order.

        Ordering is by the explicit ``revision`` number — a total, gap-free
        order by construction — so history is stable across restarts,
        processes, and insertion order. An unknown identity yields an empty
        tuple.
        """
        _require_procedure_id(procedure_id)

        with self.database.connection() as connection:
            try:
                rows = connection.execute(
                    f"SELECT procedure_id, revision, created_at_utc, status, record_json "
                    f"FROM {_PROCEDURE_TABLE} WHERE procedure_id = ? ORDER BY revision ASC",
                    (procedure_id.to_str(),),
                ).fetchall()
            except sqlite3.Error as exc:
                raise ProcedureStoreStorageError(
                    f"Unable to read history for procedure {procedure_id}"
                ) from exc

        return tuple(_decode_row(row) for row in rows)

    def list_records(self) -> tuple[ProcedureRecord, ...]:
        """Return every stored revision in deterministic order.

        Ordering is ``(procedure_id, revision)`` ascending — the storage
        primary key, a total order — so iteration is stable across restarts,
        processes, and insertion order.
        """
        with self.database.connection() as connection:
            try:
                rows = connection.execute(
                    f"SELECT procedure_id, revision, created_at_utc, status, record_json "
                    f"FROM {_PROCEDURE_TABLE} ORDER BY procedure_id ASC, revision ASC"
                ).fetchall()
            except sqlite3.Error as exc:
                raise ProcedureStoreStorageError("Unable to read the procedure store") from exc

        return tuple(_decode_row(row) for row in rows)

    def update_status(
        self,
        procedure_id: ProcedureId,
        revision: int,
        status: ProcedureStatus,
        *,
        updated_at: datetime | None = None,
    ) -> ProcedureRecord:
        """Explicitly set a stored revision's status and return the updated record.

        Semantics:

            - The transition is recorded verbatim. The store performs no
              automatic promotion, retirement, aggregation, or lifecycle-rule
              validation — candidate-skill lifecycle and trust policy are
              owned by C3.09, never by storage.
            - ``updated_at`` records WHEN the explicit transition happened;
              when omitted it defaults to ``now`` (UTC). It is historical
              data, not a grant of authority.
            - Marking a revision ``ACTIVE`` is an explicit caller act and
              still yields inert data: an ACTIVE record cannot grant
              Permission, lower Risk, enlarge a ResourceEnvelope, bypass the
              Action Gate, clear an EmergencyStop, execute itself, or mutate
              a Task.
            - Unknown ``(procedure_id, revision)`` raises
              :class:`ProcedureNotFoundError` and writes nothing.

        The returned record is the newly persisted value; previously returned
        record objects remain immutable snapshots of history.
        """
        _require_procedure_id(procedure_id)
        _require_revision(revision)
        if not isinstance(status, ProcedureStatus):
            raise ProcedureValidationError("status must be a ProcedureStatus")

        with self.database.connection() as connection:
            try:
                with _write_transaction(connection):
                    row = _select_revision(connection, procedure_id, revision)
                    if row is None:
                        raise ProcedureNotFoundError(
                            f"No stored procedure {procedure_id} revision {revision} to update"
                        )
                    current = _decode_row(row)
                    updated = replace(
                        current,
                        status=status,
                        updated_at=datetime.now(UTC) if updated_at is None else updated_at,
                    )
                    connection.execute(
                        f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
                        "WHERE procedure_id = ? AND revision = ?",
                        (
                            updated.status.value,
                            updated.to_json(),
                            procedure_id.to_str(),
                            revision,
                        ),
                    )
            except sqlite3.Error as exc:
                raise ProcedureStoreStorageError(
                    f"Unable to update status for procedure {procedure_id} revision {revision}"
                ) from exc

        return updated

    def update_status_if_current(
        self,
        procedure_id: ProcedureId,
        revision: int,
        status: ProcedureStatus,
        *,
        expected_current: ProcedureRecord,
        updated_at: datetime | None = None,
    ) -> ProcedureRecord:
        """Compare-and-set: set ``status`` only while the stored record is still
        exactly ``expected_current``, and return the updated record.

        This is the race-free mutation seam for explicit lifecycle transactions
        (the promotion transaction, N2.10). It exists because a check-then-write
        split across two store calls has a race window: another writer could
        retire or re-status the revision between the caller's read and an
        unconditional ``update_status``, and the stale decision would then
        overwrite newer state.

        Semantics:

            - The stored row is re-read and canonically compared with
              ``expected_current`` INSIDE the same ``BEGIN IMMEDIATE``
              transaction that performs the write, so no concurrent writer
              (thread or process) can change the row between the comparison
              and the commit. Equality is full canonical record equality:
              identity, revision, payload, status, scope, and timestamps must
              all still match what the caller validated.
            - On any mismatch the transaction is rolled back, nothing is
              written, and :class:`ProcedureStaleRecordError` is raised; the
              caller must re-read and re-derive its decision. An absent
              ``(procedure_id, revision)`` pair raises
              :class:`ProcedureNotFoundError`.
            - Like ``update_status``, the store performs no lifecycle-rule
              validation: recording a caller-chosen status verbatim is
              storage, never policy and never authority. The new status is
              still inert data.

        The returned record is the newly persisted value; previously returned
        record objects remain immutable snapshots of history.
        """
        _require_procedure_id(procedure_id)
        _require_revision(revision)
        if not isinstance(status, ProcedureStatus):
            raise ProcedureValidationError("status must be a ProcedureStatus")
        if not isinstance(expected_current, ProcedureRecord):
            raise TypeError("expected_current must be a canonical ProcedureRecord")

        with self.database.connection() as connection:
            try:
                with _write_transaction(connection):
                    row = _select_revision(connection, procedure_id, revision)
                    if row is None:
                        raise ProcedureNotFoundError(
                            f"No stored procedure {procedure_id} revision {revision} to update"
                        )
                    current = _decode_row(row)
                    if current != expected_current:
                        raise ProcedureStaleRecordError(
                            procedure_id=procedure_id.to_str(), revision=revision
                        )
                    updated = replace(
                        current,
                        status=status,
                        updated_at=datetime.now(UTC) if updated_at is None else updated_at,
                    )
                    connection.execute(
                        f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
                        "WHERE procedure_id = ? AND revision = ?",
                        (
                            updated.status.value,
                            updated.to_json(),
                            procedure_id.to_str(),
                            revision,
                        ),
                    )
            except sqlite3.Error as exc:
                raise ProcedureStoreStorageError(
                    f"Unable to update status for procedure {procedure_id} revision {revision}"
                ) from exc

        return updated


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@contextmanager
def _write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run one serialized write transaction with commit-or-rollback semantics.

    Unlike the shared deferred ``transaction()`` helper, this begins with
    ``BEGIN IMMEDIATE`` so the write lock is acquired BEFORE any read. The
    store's insert/update paths are check-then-write sequences; under the WAL
    journal a deferred read-then-write transaction can fail with a
    stale-snapshot ``SQLITE_BUSY`` once another writer commits between the
    check and the write. Taking the write lock up front (waiting on the
    configured busy timeout) makes the whole check-write unit race-free
    across concurrent writers. The migration machinery in
    ``agentx.infrastructure.persistence`` uses the same BEGIN IMMEDIATE
    pattern for the same reason.
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


def _select_revision(
    connection: sqlite3.Connection,
    procedure_id: ProcedureId,
    revision: int,
) -> sqlite3.Row | None:
    row: sqlite3.Row | None = connection.execute(
        f"SELECT procedure_id, revision, created_at_utc, status, record_json "
        f"FROM {_PROCEDURE_TABLE} WHERE procedure_id = ? AND revision = ?",
        (procedure_id.to_str(), revision),
    ).fetchone()
    return row


def _reject_duplicate(connection: sqlite3.Connection, record: ProcedureRecord) -> None:
    existing = connection.execute(
        f"SELECT 1 FROM {_PROCEDURE_TABLE} WHERE procedure_id = ? AND revision = ?",
        (record.procedure_id.to_str(), record.revision),
    ).fetchone()
    if existing is not None:
        raise DuplicateProcedureRevisionError(
            f"Procedure {record.procedure_id} revision {record.revision} already exists"
        )


def _reject_nonsequential_revision(
    connection: sqlite3.Connection,
    record: ProcedureRecord,
) -> None:
    row = connection.execute(
        f"SELECT MAX(revision) AS max_revision FROM {_PROCEDURE_TABLE} WHERE procedure_id = ?",
        (record.procedure_id.to_str(),),
    ).fetchone()
    stored_max = 0 if row is None or row["max_revision"] is None else int(row["max_revision"])
    expected = stored_max + 1
    if record.revision != expected:
        raise ProcedureRevisionSequenceError(
            f"Procedure {record.procedure_id} revisions are append-only and contiguous: "
            f"next revision is {expected}, got {record.revision}"
        )


def _require_procedure_id(procedure_id: ProcedureId) -> None:
    if not isinstance(procedure_id, ProcedureId):
        raise TypeError("procedure_id must be a canonical ProcedureId")


def _require_revision(revision: int) -> None:
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise ProcedureValidationError("revision must be an integer")
    if revision < 1:
        raise ProcedureValidationError("revision must be a positive integer (first revision is 1)")


def _decode_row(row: sqlite3.Row) -> ProcedureRecord:
    """Reconstruct one record from a stored row, rejecting malformed data."""
    procedure_id = str(row["procedure_id"])
    raw_revision = row["revision"]
    try:
        revision = int(raw_revision)
    except (TypeError, ValueError) as exc:
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=-1) from exc

    record_json = str(row["record_json"])
    try:
        record = ProcedureRecord.from_json(record_json)
    except ProcedureValidationError as exc:
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision) from exc
    if record.procedure_id.to_str() != procedure_id:
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision)
    if record.revision != revision:
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision)
    if _format_timestamp(record.created_at) != str(row["created_at_utc"]):
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision)
    if record.status.value != str(row["status"]):
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision)
    return record
