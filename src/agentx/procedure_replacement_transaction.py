from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    ProcedureReplacementDecision,
    ProcedureReplacementOutcome,
)
from agentx.core.procedures import (
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.infrastructure.procedure_store import (
    _PROCEDURE_TABLE,
    ProcedureStore,
    ProcedureStoreError,
    _decode_row,
    _format_timestamp,
    _write_transaction,
)


class ReplacementTransactionError(Exception):
    """Base error for replacement transaction failures."""

    pass


class ReplacementConcurrentStateError(ReplacementTransactionError):
    """Raised when the store state has changed concurrently."""

    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplacementTransactionResult:
    status: str
    procedure_id: ProcedureId
    previous_revision: int
    replacement_revision: int
    reason: str | None = None


def execute_replacement_transaction(
    store: ProcedureStore,
    decision: ProcedureReplacementDecision,
    active_record: ProcedureRecord,
    target_record: ProcedureRecord,
) -> ReplacementTransactionResult:
    """Execute the atomic procedure replacement transaction.

    Verifies that the decision is ELIGIBLE, that the store's current active
    revision matches the active_record exactly, and installs the target_record.
    """
    if not isinstance(store, ProcedureStore):
        raise TypeError("store must be a ProcedureStore")
    if not isinstance(decision, ProcedureReplacementDecision):
        raise TypeError("decision must be a ProcedureReplacementDecision")
    if not isinstance(active_record, ProcedureRecord):
        raise TypeError("active_record must be a ProcedureRecord")
    if not isinstance(target_record, ProcedureRecord):
        raise TypeError("target_record must be a ProcedureRecord")

    if decision.outcome is not ProcedureReplacementOutcome.ELIGIBLE:
        return ReplacementTransactionResult(
            status="REJECTED",
            procedure_id=decision.procedure_id,
            previous_revision=decision.active_revision,
            replacement_revision=decision.target_revision,
            reason=f"Decision outcome is {decision.outcome.value}",
        )

    if decision.procedure_id != active_record.procedure_id:
        return ReplacementTransactionResult(
            status="REJECTED",
            procedure_id=decision.procedure_id,
            previous_revision=decision.active_revision,
            replacement_revision=decision.target_revision,
            reason="Active record procedure_id mismatch",
        )

    if decision.procedure_id != target_record.procedure_id:
        return ReplacementTransactionResult(
            status="REJECTED",
            procedure_id=decision.procedure_id,
            previous_revision=decision.active_revision,
            replacement_revision=decision.target_revision,
            reason="Target record procedure_id mismatch",
        )

    if decision.active_revision != active_record.revision:
        return ReplacementTransactionResult(
            status="REJECTED",
            procedure_id=decision.procedure_id,
            previous_revision=decision.active_revision,
            replacement_revision=decision.target_revision,
            reason="Active record revision mismatch",
        )

    if decision.target_revision != target_record.revision:
        return ReplacementTransactionResult(
            status="REJECTED",
            procedure_id=decision.procedure_id,
            previous_revision=decision.active_revision,
            replacement_revision=decision.target_revision,
            reason="Target record revision mismatch",
        )

    with store.database.connection() as connection:
        try:
            with _write_transaction(connection):
                active_row = connection.execute(
                    f"SELECT procedure_id, revision, created_at_utc, status, record_json "
                    f"FROM {_PROCEDURE_TABLE} WHERE procedure_id = ? AND revision = ?",
                    (decision.procedure_id.to_str(), decision.active_revision),
                ).fetchone()

                if active_row is None:
                    raise ReplacementConcurrentStateError("Active revision not found in store")

                stored_active_record = _decode_row(active_row)
                if stored_active_record != active_record:
                    raise ReplacementConcurrentStateError(
                        "Active record state has changed concurrently"
                    )

                if stored_active_record.status is not ProcedureStatus.ACTIVE:
                    raise ReplacementConcurrentStateError("Active revision is no longer ACTIVE")

                other_active = connection.execute(
                    f"SELECT revision FROM {_PROCEDURE_TABLE} "
                    f"WHERE procedure_id = ? AND status = ? AND revision != ?",
                    (
                        decision.procedure_id.to_str(),
                        ProcedureStatus.ACTIVE.value,
                        decision.active_revision,
                    ),
                ).fetchone()
                if other_active is not None:
                    raise ReplacementConcurrentStateError("Another revision is concurrently ACTIVE")

                retired_active = replace(
                    stored_active_record,
                    status=ProcedureStatus.RETIRED,
                    updated_at=datetime.now(UTC),
                )
                connection.execute(
                    f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
                    "WHERE procedure_id = ? AND revision = ?",
                    (
                        retired_active.status.value,
                        retired_active.to_json(),
                        decision.procedure_id.to_str(),
                        decision.active_revision,
                    ),
                )

                target_row = connection.execute(
                    f"SELECT procedure_id, revision, created_at_utc, status, record_json "
                    f"FROM {_PROCEDURE_TABLE} WHERE procedure_id = ? AND revision = ?",
                    (decision.procedure_id.to_str(), decision.target_revision),
                ).fetchone()

                if target_row is None:
                    max_rev_row = connection.execute(
                        "SELECT MAX(revision) AS max_rev FROM "
                        f"{_PROCEDURE_TABLE} WHERE procedure_id = ?",
                        (decision.procedure_id.to_str(),),
                    ).fetchone()
                    max_rev = (
                        int(max_rev_row["max_rev"])
                        if max_rev_row and max_rev_row["max_rev"] is not None
                        else 0
                    )
                    if decision.target_revision != max_rev + 1:
                        raise ReplacementConcurrentStateError(
                            f"Target revision {decision.target_revision} is not contiguous "
                            f"with max revision {max_rev}"
                        )

                    activated_target = replace(
                        target_record,
                        status=ProcedureStatus.ACTIVE,
                        updated_at=datetime.now(UTC),
                    )
                    connection.execute(
                        f"INSERT INTO {_PROCEDURE_TABLE} "
                        "(procedure_id, revision, created_at_utc, status, record_json) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            activated_target.procedure_id.to_str(),
                            activated_target.revision,
                            _format_timestamp(activated_target.created_at),
                            activated_target.status.value,
                            activated_target.to_json(),
                        ),
                    )
                else:
                    existing_target = _decode_row(target_row)
                    activated_target = replace(
                        existing_target,
                        status=ProcedureStatus.ACTIVE,
                        updated_at=datetime.now(UTC),
                    )
                    connection.execute(
                        f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
                        "WHERE procedure_id = ? AND revision = ?",
                        (
                            activated_target.status.value,
                            activated_target.to_json(),
                            decision.procedure_id.to_str(),
                            decision.target_revision,
                        ),
                    )

        except ReplacementConcurrentStateError as exc:
            return ReplacementTransactionResult(
                status="REJECTED",
                procedure_id=decision.procedure_id,
                previous_revision=decision.active_revision,
                replacement_revision=decision.target_revision,
                reason=str(exc),
            )
        except ProcedureStoreError as exc:
            return ReplacementTransactionResult(
                status="REJECTED",
                procedure_id=decision.procedure_id,
                previous_revision=decision.active_revision,
                replacement_revision=decision.target_revision,
                reason=str(exc),
            )
        except sqlite3.Error as exc:
            return ReplacementTransactionResult(
                status="REJECTED",
                procedure_id=decision.procedure_id,
                previous_revision=decision.active_revision,
                replacement_revision=decision.target_revision,
                reason=f"Database error: {exc}",
            )

    return ReplacementTransactionResult(
        status="APPLIED",
        procedure_id=decision.procedure_id,
        previous_revision=decision.active_revision,
        replacement_revision=decision.target_revision,
    )
