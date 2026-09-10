"""Atomic ProcedureStore activation seam for Wave-2 lifecycle transactions.

This module owns storage transaction mechanics only. Replacement eligibility,
rollback eligibility, lifecycle policy, verification, promotion and execution
remain owned by their canonical boundaries. The seam receives exact immutable
records chosen by those callers and performs one serialized retire+activate
transaction while preserving append-only history.

A RETIRED record is terminal here: it is never accepted as an activation
target. Rollback to historical RETIRED behaviour must materialize a NEW
contiguous CANDIDATE record before calling this seam.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime

from agentx.core.procedures import ProcedureRecord, ProcedureStatus, _format_timestamp
from agentx.infrastructure.procedure_store import (
    _PROCEDURE_TABLE,
    ProcedureStore,
    ProcedureStoreStorageError,
    _decode_row,
    _write_transaction,
)


class ProcedureActivationError(Exception):
    """Base error for one atomic storage-level activation transaction."""


class ProcedureActivationConflict(ProcedureActivationError):  # noqa: N818
    """Persisted state no longer matches the caller's exact evidence."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureActivationResult:
    """Exact persisted records produced by a successful storage transaction."""

    retired_record: ProcedureRecord
    active_record: ProcedureRecord


def _history_in_transaction(
    connection: sqlite3.Connection,
    procedure_id_text: str,
) -> tuple[ProcedureRecord, ...]:
    rows = connection.execute(
        f"SELECT procedure_id, revision, created_at_utc, status, record_json "
        f"FROM {_PROCEDURE_TABLE} WHERE procedure_id = ? ORDER BY revision ASC",
        (procedure_id_text,),
    ).fetchall()
    return tuple(_decode_row(row) for row in rows)


def _persist_retired(
    connection: sqlite3.Connection,
    record: ProcedureRecord,
) -> None:
    connection.execute(
        f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
        "WHERE procedure_id = ? AND revision = ?",
        (
            record.status.value,
            record.to_json(),
            record.procedure_id.to_str(),
            record.revision,
        ),
    )


def _persist_existing_activation(
    connection: sqlite3.Connection,
    record: ProcedureRecord,
) -> None:
    connection.execute(
        f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
        "WHERE procedure_id = ? AND revision = ?",
        (
            record.status.value,
            record.to_json(),
            record.procedure_id.to_str(),
            record.revision,
        ),
    )


def _persist_new_activation(
    connection: sqlite3.Connection,
    record: ProcedureRecord,
) -> None:
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


def _validate_expected_history(
    expected_history: tuple[ProcedureRecord, ...],
    expected_active: ProcedureRecord,
) -> None:
    if not isinstance(expected_history, tuple) or not expected_history:
        raise TypeError("expected_history must be a non-empty tuple")
    if any(not isinstance(record, ProcedureRecord) for record in expected_history):
        raise TypeError("expected_history must contain only ProcedureRecord values")
    procedure_id = expected_active.procedure_id
    if any(record.procedure_id != procedure_id for record in expected_history):
        raise ValueError("expected_history must contain exactly one ProcedureId")
    revisions = tuple(record.revision for record in expected_history)
    if revisions != tuple(range(1, len(revisions) + 1)):
        raise ValueError("expected_history must be contiguous and start at revision 1")
    active_records = tuple(
        record for record in expected_history if record.status is ProcedureStatus.ACTIVE
    )
    if len(active_records) > 1:
        raise ValueError("expected_history cannot contain multiple ACTIVE revisions")
    if active_records != (expected_active,):
        raise ValueError("expected_active must be the sole ACTIVE record in expected_history")


def _validate_postconditions(
    *,
    before: tuple[ProcedureRecord, ...],
    after: tuple[ProcedureRecord, ...],
    retired: ProcedureRecord,
    activated: ProcedureRecord,
    target_must_exist: bool,
) -> None:
    expected_revisions = tuple(record.revision for record in before)
    if not target_must_exist:
        expected_revisions += (activated.revision,)
    if tuple(record.revision for record in after) != expected_revisions:
        raise ProcedureActivationConflict("procedure revision history changed unexpectedly")

    active_records = tuple(record for record in after if record.status is ProcedureStatus.ACTIVE)
    if active_records != (activated,):
        raise ProcedureActivationConflict(
            "atomic transition did not produce exactly one ACTIVE revision"
        )

    after_by_revision = {record.revision: record for record in after}
    for original in before:
        persisted = after_by_revision[original.revision]
        if original.revision == retired.revision:
            if persisted != retired:
                raise ProcedureActivationConflict("retired record postcondition mismatch")
            continue
        if target_must_exist and original.revision == activated.revision:
            if persisted != activated:
                raise ProcedureActivationConflict("activated record postcondition mismatch")
            continue
        if persisted != original:
            raise ProcedureActivationConflict(
                "unrelated historical revision changed during activation"
            )


def activate_procedure_revision_atomically(
    store: ProcedureStore,
    *,
    expected_history: tuple[ProcedureRecord, ...],
    expected_active: ProcedureRecord,
    target_candidate: ProcedureRecord,
    target_must_exist: bool,
    require_target_latest: bool,
    transitioned_at: datetime,
) -> ProcedureActivationResult:
    """Retire one exact ACTIVE record and activate one exact CANDIDATE atomically.

    Preconditions are re-read and compared byte-for-byte inside the serialized
    write transaction. Existing targets must still equal the exact supplied
    CANDIDATE. New targets must be the exact next contiguous revision. Any
    exception after BEGIN is allowed to escape the shared transaction context,
    which rolls back every tentative write.
    """
    if not isinstance(store, ProcedureStore):
        raise TypeError("store must be a ProcedureStore")
    if not isinstance(expected_active, ProcedureRecord):
        raise TypeError("expected_active must be a ProcedureRecord")
    if not isinstance(target_candidate, ProcedureRecord):
        raise TypeError("target_candidate must be a ProcedureRecord")
    if type(target_must_exist) is not bool:
        raise TypeError("target_must_exist must be bool")
    if type(require_target_latest) is not bool:
        raise TypeError("require_target_latest must be bool")
    if not isinstance(transitioned_at, datetime) or transitioned_at.tzinfo is None:
        raise TypeError("transitioned_at must be timezone-aware datetime")
    if expected_active.status is not ProcedureStatus.ACTIVE:
        raise ValueError("expected_active must be ACTIVE")
    if target_candidate.status is not ProcedureStatus.CANDIDATE:
        raise ValueError("target_candidate must be CANDIDATE; RETIRED is terminal")
    if target_candidate.procedure_id != expected_active.procedure_id:
        raise ValueError("active and target must share ProcedureId")
    if target_candidate.revision == expected_active.revision:
        raise ValueError("active and target revisions must differ")
    _validate_expected_history(expected_history, expected_active)

    procedure_id = expected_active.procedure_id
    procedure_id_text = procedure_id.to_str()

    with store.database.connection() as connection:
        try:
            with _write_transaction(connection):
                history = _history_in_transaction(connection, procedure_id_text)
                if history != expected_history:
                    raise ProcedureActivationConflict(
                        "procedure history changed concurrently after caller assessment"
                    )

                active_records = tuple(
                    record for record in history if record.status is ProcedureStatus.ACTIVE
                )
                if len(active_records) > 1:
                    raise ProcedureActivationConflict(
                        "persisted history contains multiple ACTIVE revisions"
                    )
                if active_records != (expected_active,):
                    raise ProcedureActivationConflict(
                        "expected record is not the sole current ACTIVE revision"
                    )

                target_in_store = next(
                    (record for record in history if record.revision == target_candidate.revision),
                    None,
                )
                max_revision = history[-1].revision
                if target_must_exist:
                    if target_in_store is None:
                        raise ProcedureActivationConflict("target candidate disappeared")
                    if target_in_store != target_candidate:
                        raise ProcedureActivationConflict("target candidate changed concurrently")
                    if target_in_store.status is not ProcedureStatus.CANDIDATE:
                        raise ProcedureActivationConflict("stored target is no longer CANDIDATE")
                    if require_target_latest and target_candidate.revision != max_revision:
                        raise ProcedureActivationConflict(
                            "forward replacement target is no longer latest"
                        )
                else:
                    if target_in_store is not None:
                        raise ProcedureActivationConflict(
                            "new target revision appeared concurrently"
                        )
                    if target_candidate.revision != max_revision + 1:
                        raise ProcedureActivationConflict(
                            "new target revision is not the next contiguous revision"
                        )

                retired = replace(
                    expected_active,
                    status=ProcedureStatus.RETIRED,
                    updated_at=transitioned_at,
                )
                activated = replace(
                    target_candidate,
                    status=ProcedureStatus.ACTIVE,
                    updated_at=transitioned_at,
                )

                _persist_retired(connection, retired)
                if target_must_exist:
                    _persist_existing_activation(connection, activated)
                else:
                    _persist_new_activation(connection, activated)

                final_history = _history_in_transaction(connection, procedure_id_text)
                _validate_postconditions(
                    before=history,
                    after=final_history,
                    retired=retired,
                    activated=activated,
                    target_must_exist=target_must_exist,
                )
        except ProcedureActivationConflict:
            raise
        except sqlite3.Error as exc:
            raise ProcedureStoreStorageError(
                f"Unable to atomically activate procedure {procedure_id} revision "
                f"{target_candidate.revision}"
            ) from exc

    return ProcedureActivationResult(retired_record=retired, active_record=activated)


__all__ = [
    "ProcedureActivationConflict",
    "ProcedureActivationError",
    "ProcedureActivationResult",
    "activate_procedure_revision_atomically",
]
